"""角色管理 + 状态查询工具"""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.calculation.abilities import ability_to_modifier
from app.calculation.predefined_characters import PREDEFINED_CHARACTERS
from app.services.tools._helpers import apply_hp_change, get_combatant



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
    target_id: str,
    changes: dict,
    reason: str = "",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """调整任意角色/战斗单位的状态属性。所有涉及 HP、AC、能力值、资源等数值变化都应通过该工具执行。

    支持的 changes 键包括：hp_delta(增减HP)、set_hp(直接设置HP)、ac、speed、
    abilities(dict)、conditions(list)、add_condition(str 或 dict)、remove_condition(str)、
    resource_delta(dict, 如 {"spell_slot_lv1": -1} 增减资源)、set_resource(dict, 直接设置资源值) 等。
    对于资源恢复，系统会优先参考角色模板中的默认上限进行截断；set_resource 也可传 "max" 表示恢复到上限。
    对于 HP 变化，优先使用 hp_delta（正=治疗, 负=伤害）以确保边界安全。
    add_condition 接受状态 ID 字符串（如 "blinded"）或完整字典（如 {"id": "charmed", "source_id": "goblin_1", "duration": 3}）。

    Args:
        target_id: 目标单位 ID（如 "player_预设-战士"、"goblin_1"）或 "player" 表示当前玩家。
        changes: 要修改的属性字典，如 {"hp_delta": -5} 或 {"ac": 18, "add_condition": "blinded"}。
        reason: 修改原因的简短描述，用于日志。
    """
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
        lines.append(f"  {target_name} 速度 → {target['speed']}")
    if "abilities" in changes:
        for k, v in changes["abilities"].items():
            target.setdefault("abilities", {})[k] = int(v)
            target.setdefault("modifiers", {})[k] = ability_to_modifier(int(v))
        lines.append(f"  {target_name} 能力值已更新")

    # 状态效果管理 — 兼容字符串和 ActiveCondition 字典
    if "add_condition" in changes:
        conds: list[dict] = target.setdefault("conditions", [])
        raw = changes["add_condition"]
        new_cond = {"id": raw} if isinstance(raw, str) else dict(raw)
        cond_id = new_cond["id"]
        if not any(c.get("id") == cond_id for c in conds):
            conds.append(new_cond)
        lines.append(f"  {target_name} +状态: {cond_id}")
    if "remove_condition" in changes:
        conds = target.get("conditions", [])
        cond_id = changes["remove_condition"]
        target["conditions"] = [c for c in conds if c.get("id") != cond_id]
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
