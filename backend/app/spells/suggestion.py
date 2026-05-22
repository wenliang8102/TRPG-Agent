"""暗示术 (Suggestion) — 2环附魔，WIS 豁免失败后受一句合理暗示影响。"""

from app.conditions._base import build_condition_extra, create_condition
from app.spells._base import SpellDef, SpellResult, get_spell_dc

SPELL_DEF: SpellDef = {
    "name": "Suggestion",
    "name_cn": "暗示术",
    "level": 2,
    "school": "enchantment",
    "casting_time": "action",
    "range": "30 feet",
    "description": "目标进行 WIS 豁免，失败则按一句合理暗示行动。需要专注，最长 8 小时。",
    "concentration": True,
}


def execute(caster: dict, targets: list[dict], slot_level: int, **kwargs) -> SpellResult:
    """单目标 WIS 豁免，失败记录 suggestion 条件供叙事层解释。"""
    from app.services.tools._helpers import roll_actor_save

    if not targets:
        return {"lines": ["未指定目标。"]}

    target = targets[0]
    caster_name = caster.get("name", "?")
    target_name = target.get("name", "?")
    spell_dc = get_spell_dc(caster)
    suggestion_text = kwargs.get("suggestion", "")
    lines = [f"{caster_name} 施放 暗示术 → {target_name} — DC {spell_dc} WIS 豁免"]

    save_roll, auto_fail_reason, disadvantaged = roll_actor_save(target, "wis")
    if auto_fail_reason:
        lines.append(f"  → 豁免自动失败（{auto_fail_reason}）！{target_name} 受到暗示影响。")
    elif save_roll.total >= spell_dc:
        roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
        lines.append(f"  → 豁免成功({roll_text})，{target_name} 抵抗了暗示。")
        return {"lines": lines}
    else:
        roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
        lines.append(f"  → 豁免失败({roll_text})！{target_name} 受到暗示影响。")

    target.setdefault("conditions", []).append(
        create_condition(
            "suggestion",
            source_id="concentration:suggestion",
            extra=build_condition_extra(text=suggestion_text, source_actor_id=caster.get("id", "")),
        )
    )
    return {"lines": lines}
