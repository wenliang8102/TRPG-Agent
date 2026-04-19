"""雷鸣波 (Thunderwave) — 1环塑能，15尺立方 AoE + CON 豁免 + 推离效果"""

from app.spells._base import SpellDef, SpellResult
from app.spells._resolvers import resolve_aoe_save

SPELL_DEF: SpellDef = {
    "name": "Thunderwave",
    "name_cn": "雷鸣波",
    "level": 1,
    "school": "evocation",
    "casting_time": "action",
    "range": "self (15-foot cube)",
    "description": "15尺立方区域内的每个生物必须进行体质豁免。失败受2d8点雷鸣伤害并被推离10尺，成功减半且不被推离。法术会发出300尺内可听见的巨响。升环每高一环伤害增加1d8。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """2d8 雷鸣伤害（升环每环+1d8），CON 豁免成功减半且不被推离，失败全额且推离10尺"""
    dice_count = 2 + max(0, slot_level - 1)
    return resolve_aoe_save(
        caster, targets,
        spell_name_cn="雷鸣波",
        slot_level=slot_level,
        damage_formula=f"{dice_count}d8",
        damage_type="雷鸣",
        save_ability="con",
        spell_school="evocation",
        extra_per_target="豁免失败者被推离 10 尺",
    )
