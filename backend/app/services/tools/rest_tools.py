"""休息结算工具。"""

from __future__ import annotations

from typing import Annotated, Literal

import d20
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.calculation.predefined_characters import PREDEFINED_CHARACTERS
from app.services.tools._helpers import is_player_reference, reset_death_save_state, resolve_player_reference_id

RestType = Literal["short", "long"]
ResourceRecovery = Literal["short_rest", "long_rest"]

RESOURCE_RECOVERY_RULES: dict[str, ResourceRecovery] = {
    "second_wind_uses": "short_rest",
    "action_surge_uses": "short_rest",
    "superiority_dice": "short_rest",
    "ki_points": "short_rest",
    "arcane_recovery_uses": "long_rest",
    "rage_uses": "long_rest",
}

CLASS_HIT_DICE: dict[str, str] = {
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


def _state_value_to_dict(value) -> dict | None:
    """休息会原地修改角色状态，入口处统一转成普通字典。"""
    if not value:
        return None
    return value.model_dump() if hasattr(value, "model_dump") else dict(value)


def _mapping_state_to_dict(value) -> dict:
    """场景单位映射可能来自 Pydantic 或普通 dict，工具内统一成可写字典。"""
    if not value:
        return {}
    if hasattr(value, "items"):
        return {k: _state_value_to_dict(v) or {} for k, v in value.items()}
    return {}


def _resource_recovery_rule(resource_key: str) -> ResourceRecovery:
    """用资源名集中表达 5e 恢复节奏，避免在工具逻辑里散落职业判断。"""
    if resource_key.startswith("pact_magic_lv"):
        return "short_rest"
    if resource_key.startswith("spell_slot_lv"):
        return "long_rest"
    return RESOURCE_RECOVERY_RULES.get(resource_key, "long_rest")


def _resource_caps(unit: dict) -> dict[str, int]:
    """优先读取状态上限，旧存档缺失时从预设角色卡推断。"""
    caps = unit.setdefault("resource_caps", {})
    role_class = str(unit.get("role_class") or "")
    template = PREDEFINED_CHARACTERS.get(role_class, {})
    template_caps = template.get("resource_caps") or template.get("resources") or {}

    for key, value in template_caps.items():
        caps.setdefault(key, int(value))
    return {key: int(value) for key, value in caps.items()}


def _normalize_hit_dice(unit: dict) -> tuple[str, int, int]:
    """补齐旧角色缺失的生命骰字段，供短休治疗和长休恢复复用。"""
    role_class = str(unit.get("role_class") or "")
    level = max(1, int(unit.get("level", 1) or 1))
    hit_die = str(unit.get("hit_die") or CLASS_HIT_DICE.get(role_class) or "1d8")
    total = max(1, int(unit.get("hit_dice_total", level) or level))
    remaining = max(0, min(int(unit.get("hit_dice_remaining", total) or 0), total))

    unit["hit_die"] = hit_die
    unit["hit_dice_total"] = total
    unit["hit_dice_remaining"] = remaining
    return hit_die, total, remaining


def _is_short_rest_resource(rule: ResourceRecovery) -> bool:
    return rule == "short_rest"


def _restore_resources(unit: dict, rest_type: RestType, lines: list[str], label: str) -> None:
    """按资源恢复标记回满对应资源，短休不恢复普通法术位。"""
    resources = unit.setdefault("resources", {})
    for key, cap in sorted(_resource_caps(unit).items()):
        rule = _resource_recovery_rule(key)
        if rest_type == "short" and not _is_short_rest_resource(rule):
            continue
        old = int(resources.get(key, 0) or 0)
        if old == cap:
            continue
        resources[key] = cap
        rest_label = "短休" if rule == "short_rest" else "长休"
        lines.append(f"  {label} {key}: {old} -> {cap}（{rest_label}恢复）")


def _spend_hit_dice(unit: dict, hit_dice_to_spend: int, lines: list[str], label: str) -> dict | None:
    """短休消耗生命骰治疗；治疗量按生命骰 + CON 调整值逐枚结算。"""
    if hit_dice_to_spend <= 0:
        return None

    hit_die, _, remaining = _normalize_hit_dice(unit)
    spend = min(hit_dice_to_spend, remaining)
    if spend <= 0:
        lines.append(f"  {label} 没有可用生命骰，无法通过短休治疗。")
        return None

    old_hp = int(unit.get("hp", 0) or 0)
    max_hp = int(unit.get("max_hp", old_hp) or old_hp)
    con_mod = int(unit.get("modifiers", {}).get("con", 0) or 0)
    maximized = unit.get("hit_dice_healing_maximized_until") == "24h"
    rolls = [d20.roll(hit_die) for _ in range(spend)]
    if maximized:
        die_size = int(hit_die.split("d", 1)[1])
        healing = sum(max(0, die_size + con_mod) for _ in rolls)
    else:
        healing = sum(max(0, roll.total + con_mod) for roll in rolls)
    new_hp = min(max_hp, old_hp + healing)

    unit["hp"] = new_hp
    unit["hit_dice_remaining"] = remaining - spend
    if new_hp > 0:
        reset_death_save_state(unit)

    roll_text = ", ".join(str(roll) for roll in rolls)
    lines.append(
        f"  {label} 消耗 {spend} 枚生命骰，恢复 {new_hp - old_hp} HP"
        f"（{roll_text}，每枚 CON {con_mod:+d}{'，活力药水取最大值' if maximized else ''}）。"
    )
    lines.append(f"  {label} HP: {old_hp} -> {new_hp} / {max_hp}")
    lines.append(f"  {label} hit_dice_remaining: {remaining} -> {unit['hit_dice_remaining']}")

    return {
        "id": unit.get("id", label),
        "name": label,
        "old_hp": old_hp,
        "new_hp": new_hp,
        "max_hp": max_hp,
    }


def _finish_long_rest(unit: dict, lines: list[str], label: str) -> dict | None:
    """长休回满 HP、清除临时状态，并恢复已消耗生命骰的一半。"""
    _, total, remaining = _normalize_hit_dice(unit)
    old_hp = int(unit.get("hp", 0) or 0)
    max_hp = int(unit.get("max_hp", old_hp) or old_hp)
    old_remaining = remaining
    old_conditions = [str(condition.get("id")) for condition in unit.get("conditions", []) if condition.get("id")]
    spent = total - remaining
    recovered = min(spent, max(1, total // 2)) if spent > 0 else 0

    unit["hp"] = max_hp
    unit["temp_hp"] = 0
    unit["hit_dice_remaining"] = min(total, remaining + recovered)
    unit["conditions"] = []
    unit["concentrating_on"] = None
    reset_death_save_state(unit)

    if old_hp != max_hp:
        lines.append(f"  {label} HP: {old_hp} -> {max_hp} / {max_hp}")
    if old_remaining != unit["hit_dice_remaining"]:
        lines.append(f"  {label} hit_dice_remaining: {old_remaining} -> {unit['hit_dice_remaining']}")
    if old_conditions:
        lines.append(f"  {label} 清除状态: {', '.join(old_conditions)}")

    return {
        "id": unit.get("id", label),
        "name": label,
        "old_hp": old_hp,
        "new_hp": max_hp,
        "max_hp": max_hp,
    } if old_hp != max_hp else None


def _target_units(target_ids: list[str], state: dict) -> tuple[list[tuple[str, dict, str]], dict]:
    """根据 ID 精确选择休息对象；友方是否休息完全由 target_ids 决定。"""
    player = _state_value_to_dict(state.get("player"))
    scene_units = _mapping_state_to_dict(state.get("scene_units"))
    update: dict = {}
    targets: list[tuple[str, dict, str]] = []

    for raw_id in target_ids:
        target_id = str(raw_id).strip()
        if player and is_player_reference(player, target_id):
            resolve_player_reference_id(player, target_id)
            targets.append(("player", player, "player"))
        elif target_id in scene_units:
            targets.append((target_id, scene_units[target_id], "scene_units"))
        else:
            raise KeyError(target_id)

    if any(source == "scene_units" for _, _, source in targets):
        update["scene_units"] = scene_units
    if any(source == "player" for _, _, source in targets):
        update["player"] = player
    return targets, update


@tool
def take_rest(
    rest_type: RestType,
    target_ids: list[str],
    hit_dice_to_spend: int = 0,
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """结算短休或长休；友方是否参与由 target_ids 明确指定。

    短休恢复回气、动作如潮、卓越骰、气、契约魔法等短休资源；普通法术位不恢复。
    短休可消耗生命骰治疗，治疗量为每枚生命骰 + CON 调整值。
    长休恢复 HP、普通法术位、短休资源与长休资源，并恢复已消耗生命骰的一半（至少 1 枚）。
    参数示例：{"rest_type": "short", "target_ids": ["player", "fighter_companion"], "hit_dice_to_spend": 1}。
    """
    if state.get("combat"):
        return Command(update={"messages": [
            ToolMessage(content="当前仍在战斗中，不能进行休息。", tool_call_id=tool_call_id)
        ]})

    try:
        targets, update = _target_units(target_ids, state)
    except KeyError as exc:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到休息目标 '{exc.args[0]}'。", tool_call_id=tool_call_id)
        ]})

    lines = [f"[{'短休' if rest_type == 'short' else '长休'}]"]
    hp_changes: list[dict] = []

    for _, unit, _ in targets:
        label = str(unit.get("name") or unit.get("id") or "角色")
        _normalize_hit_dice(unit)
        _restore_resources(unit, rest_type, lines, label)
        if rest_type == "short":
            hp_change = _spend_hit_dice(unit, hit_dice_to_spend, lines, label)
        else:
            hp_change = _finish_long_rest(unit, lines, label)
        if hp_change:
            hp_changes.append(hp_change)

    if hp_changes:
        update["hp_changes"] = hp_changes
    update["messages"] = [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)]
    return Command(update=update)
