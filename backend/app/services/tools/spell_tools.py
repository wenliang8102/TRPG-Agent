"""法术施放工具"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.services.tools._helpers import get_combatant
from app.spells import get_spell_module


@tool
def cast_spell(
    spell_id: str,
    target_ids: list[str],
    slot_level: int = 0,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """施放法术。系统自动消耗法术位、计算伤害/治疗/豁免、应用效果。

    Args:
        spell_id: 法术标识符（如 "magic_missile", "cure_wounds", "shield", "burning_hands"）。
        target_ids: 目标单位 ID 列表。对自身施法传 ["self"]。
        slot_level: 使用的法术位等级。0 表示使用该法术最低环位。
    """
    def _reject(msg: str) -> Command:
        return Command(update={"messages": [ToolMessage(content=msg, tool_call_id=tool_call_id)]})

    spell_mod = get_spell_module(spell_id)
    if not spell_mod:
        return _reject(f"未知法术 '{spell_id}'。")

    spell_def = spell_mod.SPELL_DEF
    min_level = spell_def["level"]
    slot_level = slot_level or min_level
    if slot_level < min_level:
        return _reject(f"{spell_def['name_cn']}至少需要{min_level}环法术位。")

    player_raw = state.get("player")
    if not player_raw:
        return _reject("玩家尚未加载角色卡。")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw)
    player_id = f"player_{player_dict.get('name', 'player')}"

    if spell_id not in player_dict.get("known_spells", []):
        return _reject(f"角色不会 '{spell_def['name_cn']}'。已知法术: {player_dict.get('known_spells', [])}")

    slot_key = f"spell_slot_lv{slot_level}"
    pact_key = f"pact_magic_lv{slot_level}"
    resources = player_dict.get("resources", {})
    if resources.get(slot_key, 0) > 0:
        consume_key = slot_key
    elif resources.get(pact_key, 0) > 0:
        consume_key = pact_key
    else:
        return _reject(f"{slot_level}环法术位已耗尽。")

    combat_raw = state.get("combat")
    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw) if combat_raw else None
    participants = combat_dict.get("participants", {}) if combat_dict else {}

    # 动作经济 — 玩家的战斗覆盖字段直接在 player_dict 上
    casting_time = spell_def.get("casting_time", "action")
    if player_dict.get("id"):  # 玩家在战斗中（有战斗覆盖字段）
        if casting_time in ("action", "bonus_action") and combat_dict and combat_dict.get("current_actor_id") != player_id:
            return _reject(f"当前不是 {player_dict.get('name')} 的回合。")
        action_map = {"action": "action_available", "bonus_action": "bonus_action_available", "reaction": "reaction_available"}
        action_key = action_map[casting_time]
        if not player_dict.get(action_key, True):
            label = {"action": "动作", "bonus_action": "附赠动作", "reaction": "反应"}[casting_time]
            return _reject(f"本回合的{label}已用尽。")
        player_dict[action_key] = False

    # 解析目标
    scene_units_raw = state.get("scene_units") or {}
    scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units_raw.items()} if hasattr(scene_units_raw, "items") else {}

    targets: list[dict] = []
    has_scene_target = False
    for tid in target_ids:
        if tid == "self":
            targets.append(player_dict)
        elif combat_dict:
            found = get_combatant(combat_dict, player_dict, tid)
            if found:
                targets.append(found)
            elif tid in scene_raw:
                targets.append(scene_raw[tid])
                has_scene_target = True
            else:
                return _reject(f"找不到目标 '{tid}'。")
        elif tid in scene_raw:
            targets.append(scene_raw[tid])
            has_scene_target = True
        else:
            return _reject(f"找不到目标 '{tid}'。")

    resources[consume_key] -= 1
    player_dict["resources"] = resources

    result = spell_mod.execute(caster=player_dict, targets=targets, slot_level=slot_level)

    update: dict = {"player": player_dict}
    if combat_dict:
        update["combat"] = combat_dict
    if has_scene_target:
        update["scene_units"] = scene_raw

    if hp_changes := result.get("hp_changes"):
        update["hp_changes"] = hp_changes

    lines = result.get("lines", [])
    lines.append(f"（剩余{slot_level}环法术位: {resources.get(consume_key, 0)}）")
    update["messages"] = [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)]
    return Command(update=update)
