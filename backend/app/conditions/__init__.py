"""状态注册表 — 每个状态独立模块，统一发现与查询"""

from __future__ import annotations

from types import ModuleType

from app.conditions import (
    attached,
    arcane_ward,
    blinded,
    blurred,
    charmed,
    confused,
    disengaged,
    faerie_fire_mark,
    flaming_sphere,
    frightened,
    guiding_bolt_mark,
    hidden,
    incapacitated,
    invisible,
    mage_armor,
    mirror_image,
    paralyzed,
    prone,
    ray_of_frost_slow,
    reaction_suppressed,
    restrained,
    shield_active,
    spell_reflection,
    suggestion,
)
from app.conditions._base import (  # noqa: F401 — re-export
    ActiveCondition,
    CombatEffects,
    ConditionDef,
    coerce_condition_input,
    find_condition,
    has_condition,
    remove_condition_by_id,
    tick_conditions,
    upsert_condition,
)

# 状态 ID → 模块；新增状态只需在此注册并创建同名模块
CONDITION_REGISTRY: dict[str, ModuleType] = {
    "blinded": blinded,
    "charmed": charmed,
    "confused": confused,
    "disengaged": disengaged,
    "incapacitated": incapacitated,
    "hidden": hidden,
    "invisible": invisible,
    "guiding_bolt_mark": guiding_bolt_mark,
    "mage_armor": mage_armor,
    "shield_active": shield_active,
    "spell_reflection": spell_reflection,
    "mirror_image": mirror_image,
    "paralyzed": paralyzed,
    "arcane_ward": arcane_ward,
    "ray_of_frost_slow": ray_of_frost_slow,
    "reaction_suppressed": reaction_suppressed,
    "prone": prone,
    "restrained": restrained,
    "attached": attached,
    "blurred": blurred,
    "faerie_fire_mark": faerie_fire_mark,
    "flaming_sphere": flaming_sphere,
    "frightened": frightened,
    "suggestion": suggestion,
}


def get_condition_def(condition_id: str) -> ConditionDef | None:
    mod = CONDITION_REGISTRY.get(condition_id)
    return mod.CONDITION_DEF if mod else None


def get_condition_module(condition_id: str) -> ModuleType | None:
    """获取状态的完整模块引用，供查询钩子函数（modify_ac / on_attack_eligibility 等）"""
    return CONDITION_REGISTRY.get(condition_id)


def list_condition_defs() -> dict[str, ConditionDef]:
    """返回全部状态 ID → 元数据映射"""
    return {cid: mod.CONDITION_DEF for cid, mod in CONDITION_REGISTRY.items()}


def get_combat_effects(condition_id: str) -> CombatEffects | None:
    """快速获取状态的战斗效果声明"""
    cdef = get_condition_def(condition_id)
    return cdef.effects if cdef else None
