"""角色管理 + 状态查询工具"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.calculation.abilities import ability_to_modifier
from app.calculation.predefined_characters import PREDEFINED_CHARACTERS
from app.services.class_features import (
    BATTLE_MASTER_FEATURE_IDS,
    BATTLE_MASTER_MANEUVER_SAVE_ABILITY,
    BATTLE_MASTER_MANEUVER_SAVE_DC_BONUS,
    BATTLE_MASTER_SUPERIORITY_DICE,
    BATTLE_MASTER_SUPERIORITY_DIE,
    ELDRITCH_KNIGHT_FEATURE_IDS,
    grant_spellcasting,
    sync_spellcasting_fields,
    sync_eldritch_knight_spellcasting,
)
from app.conditions import remove_condition_by_id, upsert_condition
from app.services.feats import apply_feat, available_feat_ids
from app.services.skills import load_skill_content
from app.services.tools._helpers import (
    apply_death_save_failures_from_damage,
    apply_hp_change,
    compute_ac,
    get_combatant,
    is_player_reference,
    mark_unit_dying,
    prepare_character_for_combat,
    record_death_save_roll,
    reset_death_save_state,
    resolve_player_reference_id,
    sync_ac_state,
    sync_movement_state,
)

_CLASS_HIT_DICE = {
    "野蛮人": "1d12",
    "战士": "1d10",
    "圣武士": "1d10",
    "游侠": "1d10",
    "吟游诗人": "1d8",
    "牧师": "1d8",
    "德鲁伊": "1d8",
    "武僧": "1d8",
    "游荡者": "1d8",
    "邪术师": "1d8",
    "术士": "1d6",
    "法师": "1d6",
}


_STATE_CHANGE_KEYS = frozenset({
    "hp_delta",
    "set_hp",
    "ac",
    "speed",
    "abilities",
    "add_condition",
    "remove_condition",
    "resource_delta",
    "set_resource",
})

_STATE_CHANGE_KEY_HINTS = {
    "hp": "HP 请使用 set_hp（设为指定值）或 hp_delta（增减值）",
    "resources": "资源请使用 set_resource（设为指定值/上限）或 resource_delta（增减值）",
}


def _normalize_tool_mapping(field_name: str, value: dict | str | None, tool_call_id: str | None) -> dict | Command:
    """把模型工具调用中的对象参数归一化，避免 JSON 字符串卡死真实对话流程。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return Command(update={"messages": [
            ToolMessage(content=f"{field_name} 必须是对象或可解析为对象的 JSON 字符串。", tool_call_id=tool_call_id)
        ]})
    if not isinstance(parsed, dict):
        return Command(update={"messages": [
            ToolMessage(content=f"{field_name} 必须是对象，不能是 {type(parsed).__name__}。", tool_call_id=tool_call_id)
        ]})
    return parsed


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


def _sync_hit_dice_after_level_change(target: dict) -> None:
    """升级会增加生命骰总数，新生命骰默认可用，供休息工具直接消费。"""
    role_class = str(target.get("role_class") or "")
    level = max(1, int(target.get("level", 1) or 1))
    old_total = max(0, int(target.get("hit_dice_total", 0) or 0))
    old_remaining = max(0, int(target.get("hit_dice_remaining", old_total) or 0))
    target["hit_die"] = str(target.get("hit_die") or _CLASS_HIT_DICE.get(role_class) or "1d8")
    target["hit_dice_total"] = level
    target["hit_dice_remaining"] = min(level, old_remaining + max(0, level - old_total))


@tool
def load_character_profile(
    role_class: str,
    character_name: str = "",
    *,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command | str:
    """根据玩家姓名与职业读取并加载角色预设属性卡。
    此工具会自动把角色的血量(HP)、护甲(AC)和各项能力值/修正值写入游戏的主状态中。
    在需要与角色互动前使用此工具为玩家初始化；角色 ID 就是 character_name，支持中文。
    参数示例：{"character_name": "温良", "role_class": "法师"}。

    Args:
        role_class: 角色职业名称；当前支持 "战士"、"法师"、"游荡者" 等预设职业。
        character_name: 玩家告诉你的角色姓名，例如 "温良"；未提供时临时使用职业名作为名字。
    """
    key = role_class.strip()
    if key not in PREDEFINED_CHARACTERS:
        return f"未找到对应职业 '{key}'。支持的预设职业为：{', '.join(PREDEFINED_CHARACTERS.keys())}。"

    profile = deepcopy(PREDEFINED_CHARACTERS[key])
    profile["role_class"] = key
    profile["name"] = character_name.strip() or key
    profile["id"] = profile["name"]
    profile["side"] = "player"
    _sync_hit_dice_after_level_change(profile)
    sync_spellcasting_fields(profile)

    return Command(
        update={
            "player": profile,
            "messages": [
                ToolMessage(
                    content=f"已成功加载角色卡：{profile['name']}（职业：{key}，ID：{profile['id']}）。\n属性如下：{json.dumps(profile, ensure_ascii=False, indent=2)}",
                    tool_call_id=tool_call_id
                )
            ]
        }
    )


@tool
def modify_character_state(
    target_id: str = "player",
    changes: dict | str | None = None,
    action: Literal[
        "help",
        "update",
        "grant_xp",
        "level_up",
        "choose_arcane_tradition",
        "choose_fighter_archetype",
        "choose_feat",
        "apply_condition",
        "remove_condition",
        "record_death_save",
        "stabilize",
        "revive",
    ] = "update",
    payload: dict | str | None = None,
    reason: str = "",
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """角色状态调整与成长入口。用于 HP/AC/能力值/资源/状态效果、死亡豁免与复活，以及经验、升级、子职选择。
    不要用它重放攻击、施法、怪物动作工具刚刚结算过的结果。
    冒险模组的节点奖励由剧情奖励专用工具领取；不要用它手动发放冒险节点 XP，也不要把这类奖励当作玩家临时指令执行。
    成长动作支持玩家和角色型友方；对友方使用时必须把 target_id 设为 scene_units 或 combat.participants 中的友方 ID。
    如不确定 action、changes 或 payload 的写法，先用 action="help" 查看状态说明；
    成长流程用 action="help", payload={"topic": "progression"} 查看完整说明。
    参数示例：
    - 扣血：{"target_id": "温良", "action": "update", "changes": {"hp_delta": -3}, "reason": "陷阱伤害"}
    - 恢复资源：{"target_id": "player", "action": "update", "changes": {"resource_delta": {"spell_slot_lv1": 1}}, "reason": "短休恢复"}
    - 加状态：{"action": "apply_condition", "payload": {"target_id": "goblin_1", "condition_id": "prone", "duration": 1}}
    - 记录死亡豁免：{"target_id": "player", "action": "record_death_save", "payload": {"roll_total": 13}}
    - 复活玩家：{"target_id": "player", "action": "revive", "payload": {"hp": 1}, "reason": "治疗药水"}
    - 获得经验：仅在非冒险模组的手动调整场景下使用，传 {"action": "grant_xp", "payload": {"amount": 75, "reason": "规则结算"}}
    - 友方获得经验：{"target_id": "fighter_companion", "action": "grant_xp", "payload": {"amount": 900, "reason": "同伴奖励"}}
    - 友方升级：{"target_id": "fighter_companion", "action": "level_up"}
    - 友方选择战士范型：{"target_id": "fighter_companion", "action": "choose_fighter_archetype", "payload": {"archetype": "battle_master"}}
    - 友方选择专长：{"target_id": "fighter_companion", "action": "choose_feat", "payload": {"feat": "tough"}}

    Args:
        target_id: 目标单位 ID；"player" 表示当前玩家，也可传场景或战斗中的友方角色 ID。
        changes: action="update" 时的状态变更字典，常用 hp_delta、set_hp、resource_delta、set_resource、add_condition、remove_condition。
        action: 状态调整动作；常用 "update"、"record_death_save"、"revive"、"apply_condition"、"remove_condition"、"grant_xp"、"level_up"。
        payload: 非 update 动作的参数；不要把 update 的 changes 字段塞到 payload。
        reason: 修改原因，会进入工具结果，便于回合记录。
    """
    # 工具调用边界兼容：部分模型会把对象参数序列化成 JSON 字符串，入口处统一归一化。
    payload = _normalize_tool_mapping("payload", payload, tool_call_id)
    if isinstance(payload, Command):
        return payload
    changes = _normalize_tool_mapping("changes", changes, tool_call_id)
    if isinstance(changes, Command):
        return changes

    # 复杂说明仍由统一工具按需返回，模型可见工具面保持稳定。
    if action == "help":
        topic = str(payload.get("topic", "state")).strip().lower()
        skill_id = "character_progression" if topic in {"progression", "growth", "level", "level_up", "xp", "subclass"} else "character_state_management"
        return Command(update={"messages": [
            ToolMessage(content=load_skill_content(skill_id), tool_call_id=tool_call_id)
        ]})

    # 角色成长类动作收口在同一个工具里，减少模型可见工具数量。
    if action == "grant_xp":
        if _adventure_runtime_handles_xp(state):
            return Command(update={"messages": [
                ToolMessage(
                    content="冒险模组节点奖励必须通过 claim_adventure_reward 领取；本次未修改 XP。请先查看待发放剧情奖励。",
                    tool_call_id=tool_call_id,
                )
            ]})
        return _grant_xp_command(target_id, int(payload["amount"]), str(payload.get("reason", reason)), state, tool_call_id)
    if action == "level_up":
        return _level_up_command(target_id, state, tool_call_id)
    if action == "choose_arcane_tradition":
        return _choose_arcane_tradition_command(target_id, str(payload["tradition"]), state, tool_call_id)
    if action == "choose_fighter_archetype":
        return _choose_fighter_archetype_command(target_id, str(payload["archetype"]), state, tool_call_id)
    if action == "choose_feat":
        return _choose_feat_command(target_id, str(payload["feat"]), state, tool_call_id)

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

    death_actions = {"record_death_save", "stabilize", "revive"}
    changes = changes or {}
    if not changes and action not in death_actions:
        extra = " action=\"update\" 的状态变更请写入 changes，而不是 payload。" if payload else ""
        return Command(update={"messages": [
            ToolMessage(content=f"未提供状态变更内容。{extra}", tool_call_id=tool_call_id)
        ]})

    invalid_keys = sorted(set(changes) - _STATE_CHANGE_KEYS) if action not in death_actions else []
    if invalid_keys:
        hints = [hint for key, hint in _STATE_CHANGE_KEY_HINTS.items() if key in invalid_keys]
        hint_text = f"\n常见修正: {'；'.join(hints)}。" if hints else ""
        return Command(update={"messages": [
            ToolMessage(
                content=(
                    f"状态变更参数无效：未知 changes 字段 {', '.join(invalid_keys)}。"
                    f"{hint_text}\n本次未执行任何状态修改；使用 action=\"help\" 查看完整说明。"
                ),
                tool_call_id=tool_call_id,
            )
        ]})

    update: dict = {}
    lines: list[str] = [f"[状态变更] {reason}" if reason else "[状态变更]"]
    hp_changes: list[dict] = []

    # 定位目标
    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    target_id = resolve_player_reference_id(player_dict, target_id)

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
    if not target and is_player_reference(player_dict, target_id):
        target = player_dict
        target_source = "player"

    if not target:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到目标 '{target_id}'。", tool_call_id=tool_call_id)
        ]})

    target_name = target.get("name", target_id)

    resource_caps = _get_resource_caps(target, player_dict)

    # 应用各项变更
    if action == "record_death_save":
        if target.get("is_stable"):
            lines.append(f"  {target_name} 已经伤势稳定，无需继续进行死亡豁免。")
        elif "roll_total" not in payload and "raw_roll" not in payload and "final_total" not in payload:
            return Command(update={"messages": [
                ToolMessage(
                    content="记录死亡豁免需要 payload.roll_total（或 raw_roll/final_total）。",
                    tool_call_id=tool_call_id,
                )
            ]})
        else:
            old_hp = target.get("hp", 0)
            roll_total = int(payload.get("roll_total") or payload.get("raw_roll") or payload.get("final_total"))
            lines.extend(f"  {line}" for line in record_death_save_roll(target, roll_total))
            if target.get("hp", old_hp) != old_hp:
                hp_changes.append({
                    "id": target.get("id", target_id),
                    "name": target_name,
                    "old_hp": old_hp,
                    "new_hp": target.get("hp", old_hp),
                    "max_hp": target.get("max_hp", old_hp),
                })
    elif action == "stabilize":
        target["hp"] = 0
        target["death_save_successes"] = 0
        target["death_save_failures"] = 0
        target["is_stable"] = True
        target["is_dead"] = False
        lines.append(f"  {target_name} 伤势稳定，保持 0 HP。")
    elif action == "revive":
        old_hp = target.get("hp", 0)
        max_hp = target.get("max_hp", old_hp)
        new_hp = max(1, min(int(payload.get("hp", 1)), max_hp))
        target["hp"] = new_hp
        reset_death_save_state(target)
        hp_changes.append({
            "id": target.get("id", target_id),
            "name": target_name,
            "old_hp": old_hp,
            "new_hp": new_hp,
            "max_hp": max_hp,
        })
        lines.append(f"  {target_name} 复活并恢复至 {new_hp} HP。")

    if "hp_delta" in changes:
        hc = apply_hp_change(target, changes["hp_delta"])
        hp_changes.append(hc)
        lines.append(f"  {target_name} HP: {hc['old_hp']} → {hc['new_hp']}")
        if hc["old_hp"] > 0 and hc["new_hp"] == 0:
            mark_unit_dying(target, lines)
        elif hc["old_hp"] == 0 and hc["new_hp"] == 0 and int(changes["hp_delta"]) < 0:
            apply_death_save_failures_from_damage(target, crit=False, lines=lines)
    if "set_hp" in changes:
        old_hp = target.get("hp", 0)
        max_hp = target.get("max_hp", old_hp)
        new_hp = max(0, min(int(changes["set_hp"]), max_hp))
        target["hp"] = new_hp
        if new_hp > 0:
            reset_death_save_state(target)
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
        sync_ac_state(target)
        sync_movement_state(target)
        lines.append(f"  {target_name} +状态: {cond_id}")
    if "remove_condition" in changes:
        cond_id = changes["remove_condition"]
        remove_condition_by_id(target, cond_id)
        sync_ac_state(target)
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
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """查询任意场景内单位（包括玩家、怪物、NPC）的完整属性信息。
    返回 HP、AC、能力值、攻击列表、法术位、状态效果等全部信息。

    Args:
        target_id: 目标单位 ID；玩家角色的 ID 就是玩家名字，也兼容 "player" 表示当前玩家。
    """
    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    target_id = resolve_player_reference_id(player_dict, target_id)

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

    if not result and is_player_reference(player_dict, target_id):
        result = player_dict
        source = "玩家角色"

    if not result:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到单位 '{target_id}'。", tool_call_id=tool_call_id)
        ]})

    if isinstance(result, dict):
        result["ac"] = compute_ac(result)

    content = f"[{source}] {target_id} 完整信息:\n{json.dumps(result, ensure_ascii=False, indent=2)}"
    return Command(update={"messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]})


# ── 经验值 & 升级 ───────────────────────────────────────────────

from app.services.tools._helpers import XP_THRESHOLDS

# 法师升级表：等级 → (hp_die, spell_slots, new_spells, cantrips, class_features, arcane_tradition_prompt)
_WIZARD_LEVEL_TABLE: dict[int, dict] = {
    1: {
        "spell_slots": {"spell_slot_lv1": 2},
        "known_spells": ["magic_missile", "mage_armor"],
        "known_cantrips": ["fire_bolt", "toll_the_dead", "ray_of_frost"],
        "class_features": [],
    },
    2: {
        "spell_slots": {"spell_slot_lv1": 3},
        "resources": {"arcane_recovery_uses": 1},
        "new_spells": ["shield"],
        "class_features": ["arcane_recovery"],
        "choose_tradition": True,  # 升到 2 级时选择奥术传承
    },
    3: {
        "spell_slots": {"spell_slot_lv1": 4, "spell_slot_lv2": 2},
        "new_spells": ["guiding_bolt", "mirror_image", "hold_person"],
    },
}


# 战士升级表只覆盖当前 1-5 级目标，3 级范型由独立动作选择。
_FIGHTER_LEVEL_TABLE: dict[int, dict] = {
    2: {
        "class_features": ["action_surge"],
        "resources": {"action_surge_uses": 1},
    },
    3: {
        "choose_archetype": True,
    },
    4: {
        "class_features": ["ability_score_improvement"],
    },
    5: {
        "class_features": ["extra_attack"],
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
    player_dict["level"] = new_level
    _sync_hit_dice_after_level_change(player_dict)
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

    resources = player_dict.setdefault("resources", {})
    resource_caps = player_dict.setdefault("resource_caps", {})
    for resource_key, count in table.get("resources", {}).items():
        old = resources.get(resource_key, 0)
        resources[resource_key] = count
        resource_caps[resource_key] = count
        if count > old:
            lines.append(f"  {resource_key}: {old} → {count}")

    new_spells = table.get("new_spells", [])
    known_before = set(player_dict.get("known_spells", []))
    grant_spellcasting(
        player_dict,
        ability=str(player_dict.get("spellcasting_ability") or "int"),
        spells=new_spells,
        spell_slots=table.get("spell_slots"),
    )
    for spell_id in new_spells:
        if spell_id not in known_before:
            lines.append(f"  学会新法术: {spell_id}")

    # 职业特性
    features = player_dict.setdefault("class_features", [])
    for feat in table.get("class_features", []):
        if feat not in features:
            features.append(feat)
            lines.append(f"  获得职业特性: {feat}")

    # 奥术传承选择提示
    if table.get("choose_tradition") and not player_dict.get("arcane_tradition"):
        lines.append("  [必须选择奥术传承：塑能学派(evocation) / 防护学派(abjuration)]")
        lines.append('  先使用 modify_character_state，action="choose_arcane_tradition" 完成选择，再继续后续流程')

    # 熟练加值
    from app.calculation.proficiency import calculate_proficiency_bonus
    new_prof = calculate_proficiency_bonus(new_level)
    old_prof = calculate_proficiency_bonus(new_level - 1)
    player_dict["proficiency_bonus"] = new_prof
    if new_prof != old_prof:
        lines.append(f"  熟练加值: +{old_prof} → +{new_prof}")

    return lines


def _apply_fighter_level_up(player_dict: dict, new_level: int) -> list[str]:
    """按战士职业表升级到 new_level，仅实现当前需要的 1-3 级。"""
    lines: list[str] = []
    table = _FIGHTER_LEVEL_TABLE.get(new_level)
    if not table:
        lines.append(f"战士暂不支持 {new_level} 级升级表。")
        return lines

    # 战士规则采用固定升级生命值：6 + 体质调整值。
    con_mod = player_dict.get("modifiers", {}).get("con", 0)
    hp_gain = max(1, 6 + con_mod)
    player_dict["max_hp"] = player_dict.get("max_hp", 0) + hp_gain
    player_dict["hp"] = player_dict.get("hp", 0) + hp_gain
    player_dict["level"] = new_level
    _sync_hit_dice_after_level_change(player_dict)
    lines.append(f"  HP: +{hp_gain}（6 + CON {con_mod}），最大 HP → {player_dict['max_hp']}")

    resources = player_dict.setdefault("resources", {})
    resource_caps = player_dict.setdefault("resource_caps", {})
    for resource_key, count in table.get("resources", {}).items():
        old = resources.get(resource_key, 0)
        resources[resource_key] = count
        resource_caps[resource_key] = count
        if count > old:
            lines.append(f"  {resource_key}: {old} → {count}")

    features = player_dict.setdefault("class_features", [])
    for feat in table.get("class_features", []):
        if feat not in features:
            features.append(feat)
            lines.append(f"  获得职业特性: {feat}")

    if table.get("choose_archetype") and not player_dict.get("fighter_archetype"):
        lines.append("  [必须选择武术范型：勇士(champion) / 战斗大师(battle_master) / 奥法骑士(eldritch_knight)]")
        lines.append('  先使用 modify_character_state，action="choose_fighter_archetype" 完成选择，再继续后续流程')

    from app.calculation.proficiency import calculate_proficiency_bonus
    player_dict["proficiency_bonus"] = calculate_proficiency_bonus(new_level)
    lines.extend(sync_eldritch_knight_spellcasting(player_dict))

    return lines


def _get_player_dict(state: dict) -> dict | None:
    """统一读取玩家状态，兼容 Pydantic 与普通 dict。"""
    player_raw = state.get("player")
    return player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None


def _adventure_runtime_handles_xp(state: dict) -> bool:
    """冒险模组激活时，经验奖励必须由后台运行时写回，避免主模型重复结算。"""
    adventure_raw = state.get("adventure") if state else None
    if hasattr(adventure_raw, "model_dump"):
        adventure_raw = adventure_raw.model_dump()
    if not adventure_raw or not isinstance(adventure_raw, dict):
        return False
    return bool(adventure_raw.get("module_id") and adventure_raw.get("active_node_id"))


def _resolve_growth_target(state: dict, target_id: str) -> tuple[dict | None, str, dict]:
    """成长系统只处理玩家和角色型友方，避免把普通怪物误当角色卡升级。"""
    player_dict = _get_player_dict(state)
    update: dict = {}
    target_id = resolve_player_reference_id(player_dict, target_id)

    if is_player_reference(player_dict, target_id):
        return player_dict, "player", update

    combat_raw = state.get("combat")
    if combat_raw:
        combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
        target = get_combatant(combat_dict, player_dict, target_id)
        if target:
            update["combat"] = combat_dict
            return target, "combat", update

    scene_units = state.get("scene_units") or {}
    scene_raw = {
        k: v.model_dump() if hasattr(v, "model_dump") else dict(v)
        for k, v in scene_units.items()
    } if hasattr(scene_units, "items") else {}
    if target_id in scene_raw:
        update["scene_units"] = scene_raw
        return scene_raw[target_id], "scene", update

    return None, "", update


def _is_growth_character(target: dict | None) -> bool:
    """成长目标必须是玩家或友方角色，且拥有职业字段。"""
    if not target:
        return False
    side = target.get("side", "player")
    return side in {"player", "ally"} and bool(target.get("role_class"))


def _growth_role_class(target: dict) -> str:
    """友方模板使用英文职业 ID，这里统一成升级表使用的中文职业名。"""
    role_class = str(target.get("role_class", "")).strip().lower()
    aliases = {
        "fighter": "战士",
        "wizard": "法师",
        "战士": "战士",
        "法师": "法师",
    }
    return aliases.get(role_class, str(target.get("role_class", "")))


def _build_character_growth_update(
    target: dict,
    target_source: str,
    base_update: dict,
    messages: list[ToolMessage],
) -> dict:
    """按角色当前位置回写成长结果，避免玩家、场景、战斗维护多套写回规则。"""
    prepare_character_for_combat(
        target,
        side=target.get("side", "ally"),
        fallback_id=target.get("id"),
        reset_action_economy=False,
    )
    update = dict(base_update)
    if target_source == "player":
        update["player"] = target
    elif target_source == "combat":
        update["combat"]["participants"][target["id"]] = target
    elif target_source == "scene":
        update["scene_units"][target["id"]] = target
    update["messages"] = messages
    return update


def _missing_player_message(tool_call_id: str | None) -> Command:
    """玩家未初始化时快速返回，保持成长工具行为一致。"""
    return Command(update={"messages": [
        ToolMessage(content="玩家尚未加载角色卡。", tool_call_id=tool_call_id)
    ]})


def _grant_xp_command(
    target_id: str,
    amount: int,
    reason: str,
    state: dict,
    tool_call_id: str | None,
) -> Command:
    """为玩家或角色型友方增加经验，先打通成长目标定位。"""
    target, source, base_update = _resolve_growth_target(state, target_id)
    if not target:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到成长目标 '{target_id}'。", tool_call_id=tool_call_id)
        ]})
    if not _is_growth_character(target):
        return Command(update={"messages": [
            ToolMessage(content=f"{target.get('name', target_id)} 不是可升级的玩家或友方角色。", tool_call_id=tool_call_id)
        ]})

    old_xp = target.get("xp", 0)
    new_xp = old_xp + amount
    target["xp"] = new_xp

    current_level = target.get("level", 1)
    next_threshold = XP_THRESHOLDS.get(current_level + 1)

    lines = [f"[经验值] {reason}" if reason else "[经验值]"]
    lines.append(f"  {target.get('name', '?')}: XP {old_xp} → {new_xp}")

    if next_threshold and new_xp >= next_threshold:
        lines.append(
            f'  ☑ XP 已达到 {current_level + 1} 级门槛（{next_threshold}）！'
            '可以使用 modify_character_state，action="level_up" 升级。'
        )

    return Command(update=_build_character_growth_update(
        target,
        source,
        base_update,
        [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
    ))

def _level_up_command(target_id: str, state: dict, tool_call_id: str | None) -> Command:
    """按当前职业升级表推进指定角色等级。"""
    target, source, base_update = _resolve_growth_target(state, target_id)
    if not target:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到成长目标 '{target_id}'。", tool_call_id=tool_call_id)
        ]})
    if not _is_growth_character(target):
        return Command(update={"messages": [
            ToolMessage(content=f"{target.get('name', target_id)} 不是可升级的玩家或友方角色。", tool_call_id=tool_call_id)
        ]})

    current_level = target.get("level", 1)
    new_level = current_level + 1
    xp = target.get("xp", 0)
    threshold = XP_THRESHOLDS.get(new_level)

    if not threshold:
        return Command(update={"messages": [
            ToolMessage(content=f"当前等级 {current_level}，暂不支持升到 {new_level} 级。", tool_call_id=tool_call_id)
        ]})

    if xp < threshold:
        return Command(update={"messages": [
            ToolMessage(content=f"XP 不足：当前 {xp}，升到 {new_level} 级需要 {threshold}。", tool_call_id=tool_call_id)
        ]})

    role_class = _growth_role_class(target)
    lines = [f"[升级] {target.get('name', '?')}: {current_level} → {new_level} 级"]

    if role_class == "法师":
        level_lines = _apply_wizard_level_up(target, new_level)
        lines.extend(level_lines)
    elif role_class == "战士":
        level_lines = _apply_fighter_level_up(target, new_level)
        lines.extend(level_lines)
    else:
        lines.append(f"  当前仅支持法师和战士升级，{role_class} 的升级表尚未实现。")
        return Command(update={"messages": [
            ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)
        ]})

    target["level"] = new_level

    return Command(update=_build_character_growth_update(
        target,
        source,
        base_update,
        [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
    ))


def _choose_fighter_archetype_command(
    target_id: str,
    archetype: str,
    state: dict,
    tool_call_id: str | None,
) -> Command:
    """为 3 级战士写入武术范型，并授予当前版本支持的范型字段。"""
    target, source, base_update = _resolve_growth_target(state, target_id)
    if not target:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到成长目标 '{target_id}'。", tool_call_id=tool_call_id)
        ]})
    if not _is_growth_character(target):
        return Command(update={"messages": [
            ToolMessage(content=f"{target.get('name', target_id)} 不是可升级的玩家或友方角色。", tool_call_id=tool_call_id)
        ]})

    if _growth_role_class(target) != "战士":
        return Command(update={"messages": [
            ToolMessage(content="仅战士可选择武术范型。", tool_call_id=tool_call_id)
        ]})

    if target.get("level", 1) < 3:
        return Command(update={"messages": [
            ToolMessage(content="战士达到 3 级后才能选择武术范型。", tool_call_id=tool_call_id)
        ]})

    if target.get("fighter_archetype"):
        return Command(update={"messages": [
            ToolMessage(content=f"已选择武术范型: {target['fighter_archetype']}。", tool_call_id=tool_call_id)
        ]})

    archetype = archetype.strip().lower()
    valid = {"champion", "battle_master", "eldritch_knight"}
    if archetype not in valid:
        return Command(update={"messages": [
            ToolMessage(content=f"不支持的武术范型: {archetype}。可选: {', '.join(sorted(valid))}", tool_call_id=tool_call_id)
        ]})

    target["fighter_archetype"] = archetype
    features = target.setdefault("class_features", [])
    lines = [f"[武术范型] 选择了 {archetype}"]

    if archetype == "champion":
        if "improved_critical" not in features:
            features.append("improved_critical")
            lines.append("  获得特性: 精通重击 (Improved Critical) — 武器攻击天然 19 或 20 时造成重击")
    elif archetype == "battle_master":
        for feat in BATTLE_MASTER_FEATURE_IDS:
            if feat not in features:
                features.append(feat)
        resources = target.setdefault("resources", {})
        resource_caps = target.setdefault("resource_caps", {})
        resources["superiority_dice"] = BATTLE_MASTER_SUPERIORITY_DICE
        resource_caps["superiority_dice"] = BATTLE_MASTER_SUPERIORITY_DICE
        modifiers = target.get("modifiers", {})
        target["superiority_die"] = BATTLE_MASTER_SUPERIORITY_DIE
        target["maneuvers_known_count"] = 3
        target["maneuvers"] = []
        target["maneuver_save_ability"] = BATTLE_MASTER_MANEUVER_SAVE_ABILITY
        target["maneuver_save_dc"] = 8 + BATTLE_MASTER_MANEUVER_SAVE_DC_BONUS + max(
            modifiers.get("str", 0),
            modifiers.get("dex", 0),
        )
        target["artisan_tool_proficiency"] = ""
        lines.append("  获得特性: 卓越战技 (Combat Superiority) — 4 枚 d8 卓越骰")
        lines.append("  获得特性: 战争学徒 (Student of War)")
    elif archetype == "eldritch_knight":
        for feat in ELDRITCH_KNIGHT_FEATURE_IDS:
            if feat not in features:
                features.append(feat)
        lines.extend(sync_eldritch_knight_spellcasting(target))
        lines.append("  获得特性: 奥法骑士施法 (Eldritch Knight Spellcasting)")
        lines.append("  获得特性: 武器联结 (Weapon Bond)")

    return Command(update=_build_character_growth_update(
        target,
        source,
        base_update,
        [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
    ))


def _choose_arcane_tradition_command(
    target_id: str,
    tradition: str,
    state: dict,
    tool_call_id: str | None,
) -> Command:
    """为法师写入奥术传承，并授予对应职业特性。"""
    target, source, base_update = _resolve_growth_target(state, target_id)
    if not target:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到成长目标 '{target_id}'。", tool_call_id=tool_call_id)
        ]})
    if not _is_growth_character(target):
        return Command(update={"messages": [
            ToolMessage(content=f"{target.get('name', target_id)} 不是可升级的玩家或友方角色。", tool_call_id=tool_call_id)
        ]})

    if _growth_role_class(target) != "法师":
        return Command(update={"messages": [
            ToolMessage(content="仅法师可选择奥术传承。", tool_call_id=tool_call_id)
        ]})

    tradition = tradition.strip().lower()
    valid = {"evocation", "abjuration"}
    if tradition not in valid:
        return Command(update={"messages": [
            ToolMessage(content=f"不支持的传承: {tradition}。可选: {', '.join(valid)}", tool_call_id=tool_call_id)
        ]})

    target["arcane_tradition"] = tradition
    features = target.setdefault("class_features", [])
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
            int_mod = target.get("modifiers", {}).get("int", 0)
            level = target.get("level", 2)
            ward_hp = level * 2 + int_mod
            conditions = target.setdefault("conditions", [])
            # 移除旧结界
            target["conditions"] = [c for c in conditions if c.get("id") != "arcane_ward"]
            target["conditions"].append(create_condition(
                "arcane_ward",
                source_id="arcane_tradition",
                extra=build_condition_extra(ward_hp=ward_hp, ward_max_hp=ward_hp),
            ))
            lines.append(f"  获得特性: 奥术结界 (Arcane Ward) — 结界 HP: {ward_hp}")

    return Command(update=_build_character_growth_update(
        target,
        source,
        base_update,
        [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
    ))


def _choose_feat_command(
    target_id: str,
    feat_id: str,
    state: dict,
    tool_call_id: str | None,
) -> Command:
    """为目标角色记录专长，复杂效果先交给专长注册表逐步扩展。"""
    target, source, base_update = _resolve_growth_target(state, target_id)
    if not target:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到成长目标 '{target_id}'。", tool_call_id=tool_call_id)
        ]})
    if not _is_growth_character(target):
        return Command(update={"messages": [
            ToolMessage(content=f"{target.get('name', target_id)} 不是可选择专长的玩家或友方角色。", tool_call_id=tool_call_id)
        ]})

    feat_id = feat_id.strip().lower()
    if feat_id not in available_feat_ids():
        return Command(update={"messages": [
            ToolMessage(
                content=f"不支持的专长: {feat_id}。可选: {', '.join(available_feat_ids())}",
                tool_call_id=tool_call_id,
            )
        ]})

    feats = target.setdefault("feats", [])
    if feat_id in feats:
        return Command(update={"messages": [
            ToolMessage(content=f"已选择专长: {feat_id}。", tool_call_id=tool_call_id)
        ]})

    feats.append(feat_id)
    feature_id = f"feat_{feat_id}"
    features = target.setdefault("class_features", [])
    if feature_id not in features:
        features.append(feature_id)

    feat_name, feat_lines = apply_feat(target, feat_id)
    lines = [f"[专长] 选择了 {feat_name} ({feat_id})"]
    lines.extend(feat_lines)

    return Command(update=_build_character_growth_update(
        target,
        source,
        base_update,
        [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
    ))



