"""燃烧之手 (Burning Hands) — 1环塑能，锥形 AoE + DEX 豁免"""

from app.spells._base import SpellDef, SpellResult
from app.spells._resolvers import resolve_aoe_save

SPELL_DEF: SpellDef = {
    "name": "Burning Hands",
    "name_cn": "燃烧之手",
    "level": 1,
    "school": "evocation",
    "casting_time": "action",
    "range": "self (15-foot cone)",
    "area": {"shape": "cone", "origin": "self", "length": 15, "angle_deg": 53.13},
    "description": "15尺锥形区域，目标DEX豁免，失败受3d6火焰伤害，成功减半。升环+1d6。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """3d6 火焰伤害（升环+1d6），DEX 豁免成功减半"""
    dice_count = 3 + (slot_level - 1)
    return resolve_aoe_save(
        caster, targets,
        spell_name_cn="燃烧之手",
        slot_level=slot_level,
        damage_formula=f"{dice_count}d6",
        damage_type="火焰",
        save_ability="dex",
        spell_school="evocation",
    )
