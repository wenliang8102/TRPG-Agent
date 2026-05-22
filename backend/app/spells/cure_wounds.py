"""治疗创伤 (Cure Wounds) — 1环塑能，触碰治疗"""

import d20

from app.spells._base import SpellDef, SpellResult, get_spellcasting_mod

SPELL_DEF: SpellDef = {
    "name": "Cure Wounds",
    "name_cn": "治疗创伤",
    "level": 1,
    "school": "evocation",
    "casting_time": "action",
    "range": "touch",
    "description": "触碰一个生物，恢复1d8+施法属性修正的HP。升环时每多1环多1d8。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """1d8+施法修正值治疗量，每升1环+1d8"""
    from app.services.tools._helpers import reset_death_save_state

    target = targets[0]
    spell_mod = get_spellcasting_mod(caster)
    dice_count = 1 + (slot_level - 1)
    formula = f"{dice_count}d8+{spell_mod}"

    result = d20.roll(formula)
    healing = max(1, result.total)

    caster_name = caster.get("name", "?")
    target_name = target.get("name", "?")

    old_hp = target.get("hp", 0)
    max_hp = target.get("max_hp", old_hp)
    new_hp = min(old_hp + healing, max_hp)
    target["hp"] = new_hp
    if new_hp > 0:
        reset_death_save_state(target)

    lines = [
        f"{caster_name} 施放 治疗创伤（{slot_level}环）→ {target_name}",
        f"治疗骰: {result} → 恢复 {healing} HP",
        f"{target_name} HP: {old_hp} → {new_hp}",
    ]

    hp_changes = [{
        "id": target.get("id", ""),
        "name": target_name,
        "old_hp": old_hp,
        "new_hp": new_hp,
        "max_hp": max_hp,
    }]

    return {"lines": lines, "hp_changes": hp_changes}
