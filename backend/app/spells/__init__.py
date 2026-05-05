"""法术注册表 — 每个法术独立模块，统一发现与元数据查询"""

from __future__ import annotations

from types import ModuleType

from app.spells import (
    burning_hands, cure_wounds, magic_missile, shield, ice_knife,
    fireball, guiding_bolt, thunderwave, mage_armor,
    fire_bolt, toll_the_dead, ray_of_frost,
    mirror_image, hold_person,
    shocking_grasp, charm_person, misty_step, blur, flaming_sphere,
    darkness, faerie_fire, invisibility, suggestion, counterspell,
)
from app.spells._base import SpellDef, SpellResult  # noqa: F401 — re-export

# 法术 ID → 模块；新增法术只需在此注册并创建同名模块
SPELL_REGISTRY: dict[str, ModuleType] = {
    # 戏法 (0 环)
    "fire_bolt": fire_bolt,
    "toll_the_dead": toll_the_dead,
    "ray_of_frost": ray_of_frost,
    "shocking_grasp": shocking_grasp,
    # 1 环
    "magic_missile": magic_missile,
    "cure_wounds": cure_wounds,
    "shield": shield,
    "burning_hands": burning_hands,
    "ice_knife": ice_knife,
    "guiding_bolt": guiding_bolt,
    "thunderwave": thunderwave,
    "mage_armor": mage_armor,
    "charm_person": charm_person,
    "faerie_fire": faerie_fire,
    "fireball": fireball,
    # 2 环
    "mirror_image": mirror_image,
    "hold_person": hold_person,
    "misty_step": misty_step,
    "blur": blur,
    "flaming_sphere": flaming_sphere,
    "darkness": darkness,
    "invisibility": invisibility,
    "suggestion": suggestion,
    # 3 环
    "counterspell": counterspell,
}


def get_spell_module(spell_id: str) -> ModuleType | None:
    return SPELL_REGISTRY.get(spell_id)


def get_spell_def(spell_id: str) -> SpellDef | None:
    mod = SPELL_REGISTRY.get(spell_id)
    return mod.SPELL_DEF if mod else None


def list_spell_defs() -> dict[str, SpellDef]:
    """返回全部法术 ID → 元数据映射，供 LLM 上下文注入"""
    return {sid: mod.SPELL_DEF for sid, mod in SPELL_REGISTRY.items()}
