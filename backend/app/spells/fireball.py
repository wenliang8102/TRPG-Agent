"""火球术 (Fireball) — 3环塑能，150尺/半径20尺 AoE + DEX 豁免"""

from app.spells._base import SpellDef, SpellResult
from app.spells._resolvers import resolve_aoe_save

SPELL_DEF: SpellDef = {
    "name": "Fireball",
    "name_cn": "火球术",
    "level": 3,
    "school": "evocation",
    "casting_time": "action",
    "range": "150 feet (20-foot radius sphere)",
    "description": "目标点周围半径20尺球状区域内的每个生物必须进行敏捷豁免。失败受8d6点火焰伤害，成功减半。升环每高一环伤害增加1d6。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """8d6 火焰伤害（3环以上升环每环+1d6），DEX 豁免成功减半"""
    dice_count = 8 + max(0, slot_level - 3)
    return resolve_aoe_save(
        caster, targets,
        spell_name_cn="火球术",
        slot_level=slot_level,
        damage_formula=f"{dice_count}d6",
        damage_type="火焰",
        save_ability="dex",
        spell_school="evocation",
    )
