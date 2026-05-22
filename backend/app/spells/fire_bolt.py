"""火焰箭 (Fire Bolt) — 戏法/塑能，远程法术攻击，火焰伤害"""

from app.spells._base import SpellDef, SpellResult
from app.spells._resolvers import resolve_spell_attack

SPELL_DEF: SpellDef = {
    "name": "Fire Bolt",
    "name_cn": "火焰箭",
    "level": 0,
    "school": "evocation",
    "casting_time": "action",
    "range": "120 feet",
    "description": "远程法术攻击，命中造成 1d10 火焰伤害。伤害随等级增长。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, *, cantrip_scale: int = 1, **_) -> SpellResult:
    """单目标远程法术攻击，1d10 火焰伤害（随等级缩放）"""
    if not targets:
        return {"lines": ["未指定目标。"]}

    damage_formula = f"{cantrip_scale}d10"
    return resolve_spell_attack(
        caster, targets[0],
        spell_name_cn="火焰箭",
        slot_level=0,
        damage_formula=damage_formula,
        damage_type="火焰",
    )
