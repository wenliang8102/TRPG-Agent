"""法术系统公共类型与工具函数"""

from __future__ import annotations

from typing import TypedDict


class SpellDef(TypedDict, total=False):
    """法术静态元数据 — 注册表中每个法术的描述信息"""
    name: str
    name_cn: str
    level: int
    school: str
    casting_time: str   # "action" | "bonus_action" | "reaction"
    range: str
    description: str
    concentration: bool  # 是否需要专注维持，默认 False
    reaction_trigger: str  # 反应法术触发时机: "on_hit" | "on_enemy_cast" | "on_leave_reach"


class SpellResult(TypedDict, total=False):
    """法术执行结果 — 各法术 execute() 的统一返回格式"""
    lines: list[str]
    hp_changes: list[dict]


def get_spell_dc(caster: dict) -> int:
    """法术豁免DC = 8 + 熟练加值 + 施法属性修正"""
    ability = caster.get("spellcasting_ability", "int")
    mod = caster.get("modifiers", {}).get(ability, 0)
    level = caster.get("level", 1)
    prof = (level - 1) // 4 + 2
    return 8 + prof + mod


def get_spellcasting_mod(caster: dict) -> int:
    """施法属性修正值"""
    ability = caster.get("spellcasting_ability", "int")
    return caster.get("modifiers", {}).get(ability, 0)
