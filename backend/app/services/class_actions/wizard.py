"""法师主动职业动作。"""

from __future__ import annotations

import math

from app.services.class_actions.types import ClassActionContext, ClassActionResult

WIZARD_ACTION_IDS: tuple[str, ...] = ("arcane_recovery",)


def _spell_slot_level(resource_key: str) -> int:
    """从 spell_slot_lvN 资源名提取环级，奥术回想只处理普通法术位。"""
    return int(resource_key.removeprefix("spell_slot_lv"))


def use_arcane_recovery(context: ClassActionContext) -> ClassActionResult:
    """奥术回想在短休后恢复部分普通法术位，恢复总环级不超过法师等级一半向上取整。"""
    actor = context.actor
    actor_name = actor.get("name", "角色")
    payload = context.payload or {}
    resources = actor.setdefault("resources", {})
    resource_caps = actor.setdefault("resource_caps", {})
    uses = int(resources.get("arcane_recovery_uses", 1) or 0)

    if uses <= 0:
        return ClassActionResult(lines=[f"{actor_name} 的奥术回想已在本次长休周期内用过。"], update={})

    restore_slots = payload.get("restore_slots")
    if not isinstance(restore_slots, dict) or not restore_slots:
        return ClassActionResult(lines=["奥术回想需要 payload.restore_slots 指定要恢复的普通法术位。"], update={})

    level_budget = max(1, math.ceil(int(actor.get("level", 1) or 1) / 2))
    requested_cost = 0
    changes: list[str] = []

    for slot_key, raw_count in restore_slots.items():
        slot_key = str(slot_key)
        if not slot_key.startswith("spell_slot_lv"):
            return ClassActionResult(lines=[f"奥术回想不能恢复 {slot_key}，只能恢复普通法术位。"], update={})
        count = int(raw_count)
        if count <= 0:
            return ClassActionResult(lines=[f"{slot_key} 的恢复数量必须大于 0。"], update={})
        cap = int(resource_caps.get(slot_key, 0) or 0)
        old = int(resources.get(slot_key, 0) or 0)
        if cap <= 0:
            return ClassActionResult(lines=[f"{actor_name} 没有 {slot_key} 的法术位上限。"], update={})
        if old >= cap:
            return ClassActionResult(lines=[f"{slot_key} 已经是满值，不能用奥术回想恢复。"], update={})
        requested_cost += _spell_slot_level(slot_key) * count
        if old + count > cap:
            return ClassActionResult(lines=[f"{slot_key} 只能恢复到上限 {cap}，当前 {old}。"], update={})
        changes.append(f"{slot_key}: {old} -> {old + count}")

    if requested_cost > level_budget:
        return ClassActionResult(lines=[
            f"奥术回想可恢复的法术位总环级最多为 {level_budget}，本次请求为 {requested_cost}。"
        ], update={})

    for slot_key, raw_count in restore_slots.items():
        resources[str(slot_key)] = int(resources.get(str(slot_key), 0) or 0) + int(raw_count)
    resources["arcane_recovery_uses"] = uses - 1

    return ClassActionResult(
        lines=[
            f"{actor_name} 使用奥术回想，恢复法术位总环级 {requested_cost}/{level_budget}。",
            *changes,
            f"arcane_recovery_uses: {uses} -> {uses - 1}",
        ],
        update={},
    )
