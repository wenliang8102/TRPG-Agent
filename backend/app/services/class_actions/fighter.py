"""战士基础职业动作。"""

from __future__ import annotations

import d20

from app.services.class_actions.types import ClassActionContext, ClassActionResult

FIGHTER_ACTION_IDS: tuple[str, ...] = ("second_wind", "action_surge")


def use_second_wind(context: ClassActionContext) -> ClassActionResult:
    """回气按 PHB 消耗附赠动作，并恢复 1d10 + 战士等级的生命值。"""
    actor = context.actor
    actor_name = actor.get("name", "角色")
    state = context.state or {}
    combat = state.get("combat")
    if combat and not actor.get("bonus_action_available", True):
        return ClassActionResult(lines=[f"{actor_name} 本回合的附赠动作已用尽，不能使用回气。"], update={})

    resources = actor.setdefault("resources", {})
    uses = int(resources.get("second_wind_uses", 0) or 0)
    if uses <= 0:
        return ClassActionResult(lines=[f"{actor_name} 的回气次数已用尽。"], update={})

    old_hp = int(actor.get("hp", 0) or 0)
    max_hp = int(actor.get("max_hp", old_hp) or old_hp)
    fighter_level = int(actor.get("level", 1) or 1)
    roll = d20.roll("1d10")
    healing = roll.total + fighter_level
    new_hp = min(max_hp, old_hp + healing)

    resources["second_wind_uses"] = uses - 1
    actor["hp"] = new_hp
    if combat:
        actor["bonus_action_available"] = False
    return ClassActionResult(
        lines=[
            f"{actor_name} 使用回气，恢复 {new_hp - old_hp} HP（{roll} + 战士等级 {fighter_level}）。",
            f"HP: {old_hp} → {new_hp} / {max_hp}",
            f"second_wind_uses: {uses} → {uses - 1}",
        ],
        update={"hp_changes": [{
            "id": actor.get("id", actor_name),
            "name": actor_name,
            "old_hp": old_hp,
            "new_hp": new_hp,
            "max_hp": max_hp,
        }]},
    )


def use_action_surge(context: ClassActionContext) -> ClassActionResult:
    """动作如潮在战斗中提供一个额外普通动作，不覆盖原本动作状态。"""
    actor = context.actor
    actor_name = actor.get("name", "角色")
    state = context.state or {}
    combat = state.get("combat")
    if not combat:
        return ClassActionResult(lines=[f"{actor_name} 当前不在战斗中，不能使用动作如潮。"], update={})

    resources = actor.setdefault("resources", {})
    uses = int(resources.get("action_surge_uses", 0) or 0)
    if uses <= 0:
        return ClassActionResult(lines=[f"{actor_name} 的动作如潮次数已用尽。"], update={})

    old_extra_action_available = bool(actor.get("extra_action_available", False))
    resources["action_surge_uses"] = uses - 1
    actor["extra_action_available"] = True
    return ClassActionResult(
        lines=[
            f"{actor_name} 使用动作如潮，获得额外动作。",
            f"extra_action_available: {old_extra_action_available} → True",
            f"action_surge_uses: {uses} → {uses - 1}",
        ],
        update={},
    )
