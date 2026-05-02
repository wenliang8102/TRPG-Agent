"""角色管理 + 状态查询工具"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.calculation.abilities import ability_to_modifier
from app.calculation.predefined_characters import PREDEFINED_CHARACTERS
from app.conditions import remove_condition_by_id, upsert_condition
from app.services.tools._helpers import apply_hp_change, get_combatant, sync_movement_state



def _get_resource_caps(target: dict, player_dict: dict | None = None) -> dict[str, int]:
    """优先从角色模板推断资源上限，供恢复法术位/职业资源时截断。"""
    owner = player_dict or target

    raw_caps = owner.get("resource_caps")
    if isinstance(raw_caps, dict):
        return {k: int(v) for k, v in raw_caps.items()}

    role_class = owner.get("role_class", "")
    template = PREDEFINED_CHARACTERS.get(role_class)
    if template:
        return {k: int(v) for k, v in template.get("resources", {}).items()}

    return {}


@tool
def load_character_profile(
    role_class: str,
    tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command | str:
    """根据给定的职业（如'战士'、'法师'、'游荡者'）读取并加载该角色的预设属性卡。
    此工具会自动把角色的血量(HP)、护甲(AC)和各项能力值/修正值写入游戏的主状态中。
    在需要与角色互动前使用此工具为玩家初始化。

    Args:
        role_class: 需要加载的角色职业名称。当前支持："战士", "法师", "游荡者"。
    """
    key = role_class.strip()
    if key not in PREDEFINED_CHARACTERS:
        return f"未找到对应职业 '{key}'。支持的预设职业为：{', '.join(PREDEFINED_CHARACTERS.keys())}。"

    profile = PREDEFINED_CHARACTERS[key]

    return Command(
        update={
            "player": profile,
            "messages": [
                ToolMessage(
                    content=f"已成功加载角色卡：{key}。\n属性如下：{json.dumps(profile, ensure_ascii=False, indent=2)}",
                    tool_call_id=tool_call_id
                )
            ]
        }
    )


@tool
def modify_character_state(
    target_id: str = "player",
    changes: dict | None = None,
    action: Literal[
        "update",
        "grant_xp",
        "level_up",
        "choose_arcane_tradition",
        "apply_condition",
        "remove_condition",
    ] = "update",
    payload: dict | None = None,
    reason: str = "",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """角色状态调整技能。所有涉及 HP、AC、能力值、资源、经验、升级、学派、状态效果的变化都应通过该工具执行。

    使用方式：
    - action="update"：通用状态变更，填写 target_id 与 changes。
    - action="grant_xp"：增加经验，payload={"amount": 50}。
    - action="level_up"：玩家升级，系统按职业升级表结算。
    - action="choose_arcane_tradition"：法师选择学派，payload={"tradition": "abjuration"}。
    - action="apply_condition"：施加状态，payload={"target_id": "goblin_1", "condition_id": "blinded", "duration": 2}。
    - action="remove_condition"：移除状态，payload={"target_id": "goblin_1", "condition_id": "blinded"}。

    支持的 changes 键包括：hp_delta(增减HP)、set_hp(直接设置HP)、ac、speed、
    abilities(dict)、conditions(list)、add_condition(str 或 dict)、remove_condition(str)、
    resource_delta(dict, 如 {"spell_slot_lv1": -1} 增减资源)、set_resource(dict, 直接设置资源值) 等。
    对于资源恢复，系统会优先参考角色模板中的默认上限进行截断；set_resource 也可传 "max" 表示恢复到上限。
    对于 HP 变化，优先使用 hp_delta（正=治疗, 负=伤害）以确保边界安全。
    add_condition 接受状态 ID 字符串（如 "blinded"）或完整字典（如 {"id": "charmed", "source_id": "goblin_1", "duration": 3}）。

    Args:
        target_id: 目标单位 ID（如 "player_预设-战士"、"goblin_1"）或 "player" 表示当前玩家。
        changes: 要修改的属性字典，如 {"hp_delta": -5} 或 {"ac": 18, "add_condition": "blinded"}。
        action: 本次状态调整的技能动作，默认 update。
        payload: action 专属参数；经验、升级、学派和状态效果优先放在这里。
        reason: 修改原因的简短描述，用于日志。
    """
    payload = payload or {}

    # 角色成长类动作收口在同一个工具里，减少模型可见工具数量。
    if action == "grant_xp":
        return _grant_xp_command(int(payload["amount"]), str(payload.get("reason", reason)), state, tool_call_id)
    if action == "level_up":
        return _level_up_command(state, tool_call_id)
    if action == "choose_arcane_tradition":
        return _choose_arcane_tradition_command(str(payload["tradition"]), state, tool_call_id)

    # 状态效果通过 changes 复用既有状态写入路径，避免维护两套目标定位逻辑。
    if action == "apply_condition":
        target_id = str(payload.get("target_id", target_id))
        raw_condition: dict = {"id": str(payload["condition_id"])}
        if payload.get("source_id"):
            raw_condition["source_id"] = str(payload["source_id"])
        if payload.get("duration") is not None:
            raw_condition["duration"] = int(payload["duration"])
        changes = {"add_condition": raw_condition}
        reason = str(payload.get("reason", reason))
    elif action == "remove_condition":
        target_id = str(payload.get("target_id", target_id))
        changes = {"remove_condition": str(payload["condition_id"])}
        reason = str(payload.get("reason", reason))

    changes = changes or {}
    if not changes:
        return Command(update={"messages": [
            ToolMessage(content="未提供状态变更内容。", tool_call_id=tool_call_id)
        ]})

    update: dict = {}
    lines: list[str] = [f"[状态变更] {reason}" if reason else "[状态变更]"]
    hp_changes: list[dict] = []

    # 定位目标
    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    if target_id == "player" and player_dict:
        target_id = f"player_{player_dict.get('name', 'player')}"

    # 在战斗参与者 / 场景单位 / 玩家中查找目标
    combat_raw = state.get("combat")
    combat_dict = None
    target = None
    target_source = None  # "combat" | "scene" | "player"

    if combat_raw:
        combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
        # 统一接口：玩家通过 get_combatant 从 player_dict 获取，NPC 从 participants 获取
        target = get_combatant(combat_dict, player_dict, target_id)
        if target:
            target_source = "player" if (player_dict and target is player_dict) else "combat"

    if not target:
        scene_units: dict = state.get("scene_units") or {}
        scene_raw = scene_units
        if hasattr(scene_units, "model_dump"):
            scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()}
        elif isinstance(scene_units, dict):
            scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()}
        scene_target = scene_raw.get(target_id)
        if scene_target:
            target = scene_target
            target_source = "scene"

    # 非战斗状态下直接查找玩家
    if not target and player_dict and target_id == f"player_{player_dict.get('name', 'player')}":
        target = player_dict
        target_source = "player"

    if not target:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到目标 '{target_id}'。", tool_call_id=tool_call_id)
        ]})

    target_name = target.get("name", target_id)

    resource_caps = _get_resource_caps(target, player_dict)

    # 应用各项变更
    if "hp_delta" in changes:
        hc = apply_hp_change(target, changes["hp_delta"])
        hp_changes.append(hc)
        lines.append(f"  {target_name} HP: {hc['old_hp']} → {hc['new_hp']}")
    if "set_hp" in changes:
        old_hp = target.get("hp", 0)
        max_hp = target.get("max_hp", old_hp)
        new_hp = max(0, min(int(changes["set_hp"]), max_hp))
        target["hp"] = new_hp
        hp_changes.append({"id": target.get("id", target_id), "name": target_name, "old_hp": old_hp, "new_hp": new_hp, "max_hp": max_hp})
        lines.append(f"  {target_name} HP 设为 {new_hp}")
    if "ac" in changes:
        target["base_ac"] = int(changes["ac"])
        target["ac"] = int(changes["ac"])
        lines.append(f"  {target_name} AC → {target['ac']}")
    if "speed" in changes:
        target["speed"] = int(changes["speed"])
        current_speed = sync_movement_state(target)
        lines.append(f"  {target_name} 速度 → {target['speed']}（当前可用 {current_speed}）")
    if "abilities" in changes:
        for k, v in changes["abilities"].items():
            target.setdefault("abilities", {})[k] = int(v)
            target.setdefault("modifiers", {})[k] = ability_to_modifier(int(v))
        lines.append(f"  {target_name} 能力值已更新")

    # 状态效果管理 — 兼容字符串和 ActiveCondition 字典
    if "add_condition" in changes:
        raw = changes["add_condition"]
        new_cond, _ = upsert_condition(target, raw)
        cond_id = new_cond["id"]
        sync_movement_state(target)
        lines.append(f"  {target_name} +状态: {cond_id}")
    if "remove_condition" in changes:
        cond_id = changes["remove_condition"]
        remove_condition_by_id(target, cond_id)
        sync_movement_state(target)
        lines.append(f"  {target_name} -状态: {cond_id}")

    if "resource_delta" in changes:
        res = target.setdefault("resources", {})
        for rk, rv in changes["resource_delta"].items():
            old_v = int(res.get(rk, 0))
            new_v = max(0, old_v + int(rv))
            cap = resource_caps.get(rk)
            if cap is not None:
                new_v = min(new_v, cap)
            res[rk] = new_v
            suffix = f" / {cap}" if cap is not None else ""
            lines.append(f"  {target_name} {rk}: {old_v} → {res[rk]}{suffix}")
    if "set_resource" in changes:
        res = target.setdefault("resources", {})
        for rk, rv in changes["set_resource"].items():
            old_v = int(res.get(rk, 0))
            cap = resource_caps.get(rk)
            if isinstance(rv, str) and rv.strip().lower() in {"max", "full", "all", "上限", "满", "全部恢复"}:
                new_v = cap if cap is not None else old_v
            else:
                new_v = max(0, int(rv))
                if cap is not None:
                    new_v = min(new_v, cap)
            res[rk] = new_v
            suffix = f" / {cap}" if cap is not None else ""
            lines.append(f"  {target_name} {rk}: {old_v} → {res[rk]}{suffix}")

    # 回写变更 — 玩家数据在 player_dict 上原地修改，无需手动同步
    if target_source == "combat" and combat_dict:
        combat_dict["participants"][target_id] = target
        update["combat"] = combat_dict
    elif target_source == "scene":
        scene_raw[target_id] = target
        update["scene_units"] = scene_raw

    if target_source == "player" or (player_dict and target is player_dict):
        update["player"] = player_dict

    if hp_changes:
        update["hp_changes"] = hp_changes

    update["messages"] = [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)]
    return Command(update=update)


@tool
def inspect_unit(
    target_id: str,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """查询任意场景内单位（包括玩家、怪物、NPC）的完整属性信息。
    返回 HP、AC、能力值、攻击列表、法术位、状态效果等全部信息。

    Args:
        target_id: 目标单位 ID（如 "player_预设-法师"、"goblin_1"），或 "player" 表示当前玩家。
    """
    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    if target_id == "player":
        if not player_dict:
            return Command(update={"messages": [
                ToolMessage(content="玩家尚未加载角色卡。", tool_call_id=tool_call_id)
            ]})
        target_id = f"player_{player_dict.get('name', 'player')}"

    # 按优先级搜索：战斗参战者（含玩家）→ 场景单位 → 死亡单位 → 玩家本体
    result = None
    source = ""

    combat_raw = state.get("combat")
    if combat_raw:
        cd = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
        found = get_combatant(cd, player_dict, target_id)
        if found:
            result = found
            source = "战斗参与者"

    if not result:
        scene_units = state.get("scene_units") or {}
        if hasattr(scene_units, "items"):
            for k, v in scene_units.items():
                if k == target_id:
                    result = v.model_dump() if hasattr(v, "model_dump") else dict(v)
                    source = "场景单位"
                    break

    if not result:
        dead_units = state.get("dead_units") or {}
        if hasattr(dead_units, "items"):
            for k, v in dead_units.items():
                if k == target_id:
                    result = v.model_dump() if hasattr(v, "model_dump") else dict(v)
                    source = "死亡单位"
                    break

    if not result and player_dict and target_id == f"player_{player_dict.get('name', 'player')}":
        result = player_dict
        source = "玩家角色"

    if not result:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到单位 '{target_id}'。", tool_call_id=tool_call_id)
        ]})

    content = f"[{source}] {target_id} 完整信息:\n{json.dumps(result, ensure_ascii=False, indent=2)}"
    return Command(update={"messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]})


# ── 经验值 & 升级 ───────────────────────────────────────────────

from app.services.tools._helpers import XP_THRESHOLDS

# 法师升级表：等级 → (hp_die, spell_slots, new_spells, cantrips, class_features, arcane_tradition_prompt)
_WIZARD_LEVEL_TABLE: dict[int, dict] = {
    1: {
        "spell_slots": {"spell_slot_lv1": 2},
        "known_spells": ["magic_missile", "shield"],
        "known_cantrips": ["fire_bolt", "toll_the_dead", "ray_of_frost"],
        "class_features": [],
    },
    2: {
        "spell_slots": {"spell_slot_lv1": 3},
        "new_spells": ["ice_knife"],
        "class_features": ["arcane_recovery"],
        "choose_tradition": True,  # 升到 2 级时选择奥术传承
    },
    3: {
        "spell_slots": {"spell_slot_lv1": 4, "spell_slot_lv2": 2},
        "new_spells": ["guiding_bolt", "mirror_image", "hold_person"],
    },
}


def _apply_wizard_level_up(player_dict: dict, new_level: int) -> list[str]:
    """将法师升级到 new_level，修改 player_dict 并返回日志行。"""
    import d20

    lines: list[str] = []
    table = _WIZARD_LEVEL_TABLE.get(new_level)
    if not table:
        lines.append(f"法师暂不支持 {new_level} 级升级表。")
        return lines

    # HP 增长：1d6 + CON mod (最低 1)
    con_mod = player_dict.get("modifiers", {}).get("con", 0)
    hp_roll = d20.roll("1d6")
    hp_gain = max(1, hp_roll.total + con_mod)
    player_dict["max_hp"] = player_dict.get("max_hp", 0) + hp_gain
    player_dict["hp"] = player_dict.get("hp", 0) + hp_gain
    lines.append(f"  HP: +{hp_gain}（{hp_roll} + CON {con_mod}），最大 HP → {player_dict['max_hp']}")

    # 法术位更新
    if "spell_slots" in table:
        resources = player_dict.setdefault("resources", {})
        resource_caps = player_dict.setdefault("resource_caps", {})
        for slot_key, count in table["spell_slots"].items():
            old = resources.get(slot_key, 0)
            resources[slot_key] = count
            resource_caps[slot_key] = count
            if count > old:
                lines.append(f"  {slot_key}: {old} → {count}")

    # 新增法术
    known = player_dict.setdefault("known_spells", [])
    for spell_id in table.get("new_spells", []):
        if spell_id not in known:
            known.append(spell_id)
            lines.append(f"  学会新法术: {spell_id}")

    # 职业特性
    features = player_dict.setdefault("class_features", [])
    for feat in table.get("class_features", []):
        if feat not in features:
            features.append(feat)
            lines.append(f"  获得职业特性: {feat}")

    # 奥术传承选择提示
    if table.get("choose_tradition") and not player_dict.get("arcane_tradition"):
        lines.append("  [可选择奥术传承：塑能学派(evocation) / 防护学派(abjuration)]")
        lines.append('  使用 modify_character_state，action="choose_arcane_tradition" 进行选择')

    # 熟练加值
    from app.calculation.proficiency import calculate_proficiency_bonus
    new_prof = calculate_proficiency_bonus(new_level)
    old_prof = calculate_proficiency_bonus(new_level - 1)
    if new_prof != old_prof:
        lines.append(f"  熟练加值: +{old_prof} → +{new_prof}")

    return lines


def _get_player_dict(state: dict) -> dict | None:
    """统一读取玩家状态，兼容 Pydantic 与普通 dict。"""
    player_raw = state.get("player")
    return player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None


def _missing_player_message(tool_call_id: str | None) -> Command:
    """玩家未初始化时快速返回，保持成长工具行为一致。"""
    return Command(update={"messages": [
        ToolMessage(content="玩家尚未加载角色卡。", tool_call_id=tool_call_id)
    ]})


def _grant_xp_command(
    amount: int,
    reason: str,
    state: dict,
    tool_call_id: str | None,
) -> Command:
    """为玩家增加经验，达到门槛时提示继续调用统一状态调整工具升级。"""
    player_dict = _get_player_dict(state)
    if not player_dict:
        return _missing_player_message(tool_call_id)

    old_xp = player_dict.get("xp", 0)
    new_xp = old_xp + amount
    player_dict["xp"] = new_xp

    current_level = player_dict.get("level", 1)
    next_threshold = XP_THRESHOLDS.get(current_level + 1)

    lines = [f"[经验值] {reason}" if reason else "[经验值]"]
    lines.append(f"  {player_dict.get('name', '?')}: XP {old_xp} → {new_xp}")

    if next_threshold and new_xp >= next_threshold:
        lines.append(
            f'  ★ XP 已达到 {current_level + 1} 级门槛（{next_threshold}）！'
            '可以使用 modify_character_state，action="level_up" 升级。'
        )

    return Command(update={
        "player": player_dict,
        "messages": [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
    })


def _level_up_command(state: dict, tool_call_id: str | None) -> Command:
    """按当前职业升级表推进玩家等级。"""
    player_dict = _get_player_dict(state)
    if not player_dict:
        return _missing_player_message(tool_call_id)

    current_level = player_dict.get("level", 1)
    new_level = current_level + 1
    xp = player_dict.get("xp", 0)
    threshold = XP_THRESHOLDS.get(new_level)

    if not threshold:
        return Command(update={"messages": [
            ToolMessage(content=f"当前等级 {current_level}，暂不支持升到 {new_level} 级。", tool_call_id=tool_call_id)
        ]})

    if xp < threshold:
        return Command(update={"messages": [
            ToolMessage(content=f"XP 不足：当前 {xp}，升到 {new_level} 级需要 {threshold}。", tool_call_id=tool_call_id)
        ]})

    role_class = player_dict.get("role_class", "")
    lines = [f"[升级] {player_dict.get('name', '?')}: {current_level} → {new_level} 级"]

    if role_class == "法师":
        level_lines = _apply_wizard_level_up(player_dict, new_level)
        lines.extend(level_lines)
    else:
        lines.append(f"  当前仅支持法师升级，{role_class} 的升级表尚未实现。")
        return Command(update={"messages": [
            ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)
        ]})

    player_dict["level"] = new_level

    return Command(update={
        "player": player_dict,
        "messages": [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
    })


def _choose_arcane_tradition_command(
    tradition: str,
    state: dict,
    tool_call_id: str | None,
) -> Command:
    """为法师写入奥术传承，并授予对应职业特性。"""
    player_dict = _get_player_dict(state)
    if not player_dict:
        return _missing_player_message(tool_call_id)

    if player_dict.get("role_class") != "法师":
        return Command(update={"messages": [
            ToolMessage(content="仅法师可选择奥术传承。", tool_call_id=tool_call_id)
        ]})

    tradition = tradition.strip().lower()
    valid = {"evocation", "abjuration"}
    if tradition not in valid:
        return Command(update={"messages": [
            ToolMessage(content=f"不支持的传承: {tradition}。可选: {', '.join(valid)}", tool_call_id=tool_call_id)
        ]})

    player_dict["arcane_tradition"] = tradition
    features = player_dict.setdefault("class_features", [])
    lines = [f"[奥术传承] 选择了 {tradition}"]

    if tradition == "evocation":
        if "sculpt_spells" not in features:
            features.append("sculpt_spells")
            lines.append("  获得特性: 塑造法术 (Sculpt Spells) — 塑能系 AoE 法术可保护友方单位")
    elif tradition == "abjuration":
        if "arcane_ward" not in features:
            features.append("arcane_ward")
            # 创建初始结界
            from app.conditions._base import build_condition_extra, create_condition
            int_mod = player_dict.get("modifiers", {}).get("int", 0)
            level = player_dict.get("level", 2)
            ward_hp = level * 2 + int_mod
            conditions = player_dict.setdefault("conditions", [])
            # 移除旧结界
            player_dict["conditions"] = [c for c in conditions if c.get("id") != "arcane_ward"]
            player_dict["conditions"].append(create_condition(
                "arcane_ward",
                source_id="arcane_tradition",
                extra=build_condition_extra(ward_hp=ward_hp, ward_max_hp=ward_hp),
            ))
            lines.append(f"  获得特性: 奥术结界 (Arcane Ward) — 结界 HP: {ward_hp}")

    return Command(update={
        "player": player_dict,
        "messages": [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
    })


@tool
def grant_xp(
    amount: int,
    reason: str = "",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """兼容旧调用：为玩家角色增加经验值。新模型可见入口是 modify_character_state。"""
    return _grant_xp_command(amount, reason, state, tool_call_id)


@tool
def level_up(
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """兼容旧调用：将玩家角色升级到下一等级。新模型可见入口是 modify_character_state。"""
    return _level_up_command(state, tool_call_id)


@tool
def choose_arcane_tradition(
    tradition: str,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """兼容旧调用：为法师选择奥术传承。新模型可见入口是 modify_character_state。"""
    return _choose_arcane_tradition_command(tradition, state, tool_call_id)
