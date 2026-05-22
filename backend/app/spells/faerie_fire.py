"""妖火 (Faerie Fire) — 1环塑能，DEX 豁免失败后被描边造优。"""

from app.conditions._base import create_condition
from app.spells._base import SpellDef, SpellResult, get_spell_dc

SPELL_DEF: SpellDef = {
    "name": "Faerie Fire",
    "name_cn": "妖火",
    "level": 1,
    "school": "evocation",
    "casting_time": "action",
    "range": "60 feet",
    "area": {"shape": "circle", "radius": 20},
    "description": "20 尺立方内目标进行 DEX 豁免，失败后被光辉描边；针对它的攻击检定具有优势。需要专注，持续 1 分钟。",
    "concentration": True,
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """范围内目标逐个 DEX 豁免，失败挂载 faerie_fire_mark。"""
    from app.conditions import remove_condition_by_id
    from app.services.tools._helpers import roll_actor_save

    caster_name = caster.get("name", "?")
    spell_dc = get_spell_dc(caster)
    lines = [f"{caster_name} 施放 妖火 — DC {spell_dc} DEX 豁免"]
    for target in targets:
        target_name = target.get("name", "?")
        save_roll, auto_fail_reason, disadvantaged = roll_actor_save(target, "dex")
        if auto_fail_reason:
            saved = False
            roll_text = f"自动失败（{auto_fail_reason}）"
        else:
            saved = save_roll.total >= spell_dc
            roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)

        if saved:
            lines.append(f"  → {target_name}: 豁免成功({roll_text})，避开妖火。")
            continue

        remove_condition_by_id(target, "invisible")
        target.setdefault("conditions", []).append(create_condition("faerie_fire_mark", source_id="concentration:faerie_fire", duration=10))
        lines.append(f"  → {target_name}: 豁免失败({roll_text})，被妖火描边。")
    return {"lines": lines}
