"""隐形术 (Invisibility) — 2环幻术，目标获得隐形。"""

from app.conditions._base import create_condition
from app.spells._base import SpellDef, SpellResult

SPELL_DEF: SpellDef = {
    "name": "Invisibility",
    "name_cn": "隐形术",
    "level": 2,
    "school": "illusion",
    "casting_time": "action",
    "range": "touch",
    "description": "目标隐形，直到法术结束，或目标攻击/施法。需要专注，持续 1 小时。本地实现由专注和条件系统维持。",
    "concentration": True,
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """触摸目标并挂载 invisible 条件。"""
    caster_name = caster.get("name", "?")
    lines = [f"{caster_name} 施放 隐形术。"]
    for target in targets[: max(1, slot_level - 1)]:
        target["conditions"] = [c for c in target.get("conditions", []) if c.get("id") != "invisible"]
        target.setdefault("conditions", []).append(create_condition("invisible", source_id="concentration:invisibility"))
        lines.append(f"  → {target.get('name', '?')} 获得隐形。")
    return {"lines": lines}
