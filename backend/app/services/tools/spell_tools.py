"""法术施放工具"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.services.tools._helpers import get_combatant, get_condition_action_block_reason
from app.spells import get_spell_module


def _cantrip_dice_count(character_level: int) -> int:
    """戏法伤害骰数随角色等级缩放：1级=1, 5级=2, 11级=3, 17级=4"""
    if character_level >= 17:
        return 4
    if character_level >= 11:
        return 3
    if character_level >= 5:
        return 2
    return 1


def _break_concentration(player_dict: dict, lines: list[str]) -> None:
    """丢弃当前专注法术：移除关联条件并清除 concentrating_on"""
    old_spell = player_dict.get("concentrating_on")
    if not old_spell:
        return
    caster_name = player_dict.get("name", "?")
    conditions = player_dict.get("conditions", [])
    player_dict["conditions"] = [c for c in conditions if c.get("source_id") != f"concentration:{old_spell}"]
    player_dict["concentrating_on"] = None
    lines.append(f"（{caster_name} 不再专注于 {old_spell}）")


@tool
def cast_spell(
    spell_id: str,
    target_ids: list[str],
    slot_level: int = 0,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """施放法术或戏法。系统自动消耗法术位、计算伤害/治疗/豁免、应用效果。
    戏法（0 环）不消耗法术位，伤害随角色等级缩放。

    Args:
        spell_id: 法术标识符（如 "magic_missile", "fire_bolt", "shield"）。
        target_ids: 目标单位 ID 列表。对自身施法传 ["self"]。
        slot_level: 使用的法术位等级。0 表示使用该法术最低环位。戏法无需指定。
    """
    def _reject(msg: str) -> Command:
        return Command(update={"messages": [ToolMessage(content=msg, tool_call_id=tool_call_id)]})

    spell_mod = get_spell_module(spell_id)
    if not spell_mod:
        return _reject(f"未知法术 '{spell_id}'。")

    spell_def = spell_mod.SPELL_DEF
    min_level = spell_def["level"]
    is_cantrip = min_level == 0

    if is_cantrip:
        slot_level = 0
    else:
        slot_level = slot_level or min_level
        if slot_level < min_level:
            return _reject(f"{spell_def['name_cn']}至少需要{min_level}环法术位。")

    player_raw = state.get("player")
    if not player_raw:
        return _reject("玩家尚未加载角色卡。")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw)
    player_id = f"player_{player_dict.get('name', 'player')}"

    # 戏法从 known_cantrips 校验，有环法术从 known_spells 校验
    if is_cantrip:
        if spell_id not in player_dict.get("known_cantrips", []):
            return _reject(f"角色不会戏法 '{spell_def['name_cn']}'。已知戏法: {player_dict.get('known_cantrips', [])}")
    else:
        if spell_id not in player_dict.get("known_spells", []):
            return _reject(f"角色不会 '{spell_def['name_cn']}'。已知法术: {player_dict.get('known_spells', [])}")

    # 戏法不消耗法术位
    consume_key = None
    if not is_cantrip:
        from app.services.tools._helpers import consume_spell_slot
        resources = player_dict.get("resources", {})
        consume_key = consume_spell_slot(resources, slot_level)
        if not consume_key:
            return _reject(f"{slot_level}环法术位已耗尽。")

    combat_raw = state.get("combat")
    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw) if combat_raw else None
    participants = combat_dict.get("participants", {}) if combat_dict else {}

    # 动作经济
    casting_time = spell_def.get("casting_time", "action")
    if player_dict.get("id"):
        if casting_time in ("action", "bonus_action") and combat_dict and combat_dict.get("current_actor_id") != player_id:
            return _reject(f"当前不是 {player_dict.get('name')} 的回合。")
        action_map = {"action": "action_available", "bonus_action": "bonus_action_available", "reaction": "reaction_available"}
        action_key = action_map[casting_time]
        if not player_dict.get(action_key, True):
            label = {"action": "动作", "bonus_action": "附赠动作", "reaction": "反应"}[casting_time]
            return _reject(f"本回合的{label}已用尽。")
        if block_reason := get_condition_action_block_reason(player_dict, casting_time):
            return _reject(block_reason)
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

    # 消耗法术位
    resources = player_dict.get("resources", {})
    if consume_key:
        resources[consume_key] -= 1
        player_dict["resources"] = resources

    # 专注管理：施放新专注法术时丢弃旧专注
    extra_lines: list[str] = []
    is_concentration = spell_def.get("concentration", False)
    if is_concentration:
        _break_concentration(player_dict, extra_lines)

    # 执行法术，传入戏法缩放信息
    kwargs = {}
    if is_cantrip:
        kwargs["cantrip_scale"] = _cantrip_dice_count(player_dict.get("level", 1))
    result = spell_mod.execute(caster=player_dict, targets=targets, slot_level=slot_level, **kwargs)

    # 标记专注
    if is_concentration:
        player_dict["concentrating_on"] = spell_id

    # 防护学派结界刷新：施放防护系法术时恢复结界 HP
    if not is_cantrip and spell_def.get("school") == "abjuration" and "arcane_ward" in player_dict.get("class_features", []):
        _refresh_arcane_ward(player_dict, slot_level, extra_lines)

    update: dict = {"player": player_dict}
    if combat_dict:
        update["combat"] = combat_dict
    if has_scene_target:
        update["scene_units"] = scene_raw

    if hp_changes := result.get("hp_changes"):
        update["hp_changes"] = hp_changes

    lines = extra_lines + result.get("lines", [])
    if consume_key:
        lines.append(f"（剩余{slot_level}环法术位: {resources.get(consume_key, 0)}）")
    update["messages"] = [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)]
    return Command(update=update)


def _refresh_arcane_ward(player_dict: dict, spell_level: int, lines: list[str]) -> None:
    """防护学派：施放防护系法术时创建或恢复奥术结界"""
    from app.conditions._base import build_condition_extra, create_condition, find_condition

    level = player_dict.get("level", 1)
    int_mod = player_dict.get("modifiers", {}).get("int", 0)
    ward_max = level * 2 + int_mod
    name = player_dict.get("name", "?")

    conditions = player_dict.setdefault("conditions", [])
    ward = find_condition(conditions, "arcane_ward")

    if ward:
        # 恢复结界 HP = spell_level * 2
        old_hp = ward.get("extra", {}).get("ward_hp", 0)
        new_hp = min(old_hp + spell_level * 2, ward_max)
        ward.setdefault("extra", {})["ward_hp"] = new_hp
        lines.append(f"  [奥术结界] {name} 的结界恢复至 {new_hp}/{ward_max} HP")
    else:
        # 首次创建结界
        conditions.append(create_condition("arcane_ward", source_id=name, extra=build_condition_extra(ward_hp=ward_max, ward_max=ward_max)))
        lines.append(f"  [奥术结界] {name} 建立奥术结界（{ward_max}/{ward_max} HP）")
