"""状态注册表 — 每个状态独立模块，统一发现与查询"""

from __future__ import annotations

from types import ModuleType

from app.conditions import blinded, charmed, incapacitated, invisible, guiding_bolt_mark
from app.conditions._base import (  # noqa: F401 — re-export
    ActiveCondition,
    CombatEffects,
    ConditionDef,
    find_condition,
    has_condition,
    tick_conditions,
)

# 状态 ID → 模块；新增状态只需在此注册并创建同名模块
CONDITION_REGISTRY: dict[str, ModuleType] = {
    "blinded": blinded,
    "charmed": charmed,
    "incapacitated": incapacitated,
    "invisible": invisible,
    "guiding_bolt_mark": guiding_bolt_mark,
}


def get_condition_def(condition_id: str) -> ConditionDef | None:
    mod = CONDITION_REGISTRY.get(condition_id)
    return mod.CONDITION_DEF if mod else None


def list_condition_defs() -> dict[str, ConditionDef]:
    """返回全部状态 ID → 元数据映射"""
    return {cid: mod.CONDITION_DEF for cid, mod in CONDITION_REGISTRY.items()}


def get_combat_effects(condition_id: str) -> CombatEffects | None:
    """快速获取状态的战斗效果声明"""
    cdef = get_condition_def(condition_id)
    return cdef.effects if cdef else None
