"""法术系统公共类型与工具函数"""

from __future__ import annotations

import re
from typing import Literal, TypedDict


class SpellAreaDef(TypedDict, total=False):
    """法术范围形状元数据；运行时仍只保存可序列化的简单字段。"""
    shape: Literal["circle", "cone", "square"]
    origin: Literal["point", "self", "target"]
    radius: float
    length: float
    size: float
    angle_deg: float


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
    area: SpellAreaDef


class SpellResult(TypedDict, total=False):
    """法术执行结果 — 各法术 execute() 的统一返回格式"""
    lines: list[str]
    hp_changes: list[dict]
    space: dict
    blocked_action: bool


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


def get_spell_range_feet(spell_def: SpellDef) -> int | None:
    """从法术 range 文本提取最大施法距离；self 返回 0，touch 返回 5。"""
    range_text = str(spell_def.get("range", "")).lower()
    if not range_text:
        return None
    if range_text.startswith("self"):
        return 0
    if range_text == "touch":
        return 5

    match = re.search(r"(\d+)\s*(?:feet|foot|ft)", range_text)
    if match:
        return int(match.group(1))
    return None
