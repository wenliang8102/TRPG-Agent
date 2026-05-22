"""法术注册表 — 每个法术独立模块，统一发现与元数据查询"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

from app.spells._base import SpellDef, SpellResult  # noqa: F401 — re-export

# 法术 ID → 模块名；惰性导入避免单独导入某个法术时触发 tools/spells 环。
SPELL_REGISTRY: dict[str, str] = {
    # 戏法 (0 环)
    "fire_bolt": "fire_bolt",
    "toll_the_dead": "toll_the_dead",
    "ray_of_frost": "ray_of_frost",
    "shocking_grasp": "shocking_grasp",
    # 1 环
    "magic_missile": "magic_missile",
    "cure_wounds": "cure_wounds",
    "shield": "shield",
    "burning_hands": "burning_hands",
    "ice_knife": "ice_knife",
    "guiding_bolt": "guiding_bolt",
    "thunderwave": "thunderwave",
    "mage_armor": "mage_armor",
    "charm_person": "charm_person",
    "faerie_fire": "faerie_fire",
    "fireball": "fireball",
    # 2 环
    "mirror_image": "mirror_image",
    "hold_person": "hold_person",
    "misty_step": "misty_step",
    "blur": "blur",
    "flaming_sphere": "flaming_sphere",
    "darkness": "darkness",
    "invisibility": "invisibility",
    "suggestion": "suggestion",
    # 3 环
    "counterspell": "counterspell",
}

_MODULE_CACHE: dict[str, ModuleType] = {}


def get_spell_module(spell_id: str) -> ModuleType | None:
    """按需加载法术模块，避免注册表初始化时制造循环导入。"""
    module_name = SPELL_REGISTRY.get(spell_id)
    if not module_name:
        return None
    if spell_id not in _MODULE_CACHE:
        _MODULE_CACHE[spell_id] = import_module(f"app.spells.{module_name}")
    return _MODULE_CACHE[spell_id]


def get_spell_def(spell_id: str) -> SpellDef | None:
    mod = get_spell_module(spell_id)
    return mod.SPELL_DEF if mod else None


def list_spell_defs() -> dict[str, SpellDef]:
    """返回全部法术 ID → 元数据映射，供 LLM 上下文注入"""
    return {sid: mod.SPELL_DEF for sid in SPELL_REGISTRY if (mod := get_spell_module(sid))}
