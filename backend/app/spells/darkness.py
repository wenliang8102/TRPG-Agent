"""黑暗术 (Darkness) — 2环塑能，点选范围内制造重度遮蔽。"""

from app.conditions._base import create_condition
from app.spells._base import SpellDef, SpellResult

SPELL_DEF: SpellDef = {
    "name": "Darkness",
    "name_cn": "黑暗术",
    "level": 2,
    "school": "evocation",
    "casting_time": "action",
    "range": "60 feet",
    "area": {"shape": "circle", "radius": 15},
    "description": "在目标点创造半径 15 尺的魔法黑暗。战斗中对当前范围内单位挂载目盲近似。需要专注，持续 10 分钟。",
    "concentration": True,
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """当前战斗近似为对范围内目标施加 blinded，专注结束时统一清理。"""
    caster_name = caster.get("name", "?")
    lines = [f"{caster_name} 施放 黑暗术，魔法黑暗吞没目标区域。"]
    for target in targets:
        target.setdefault("conditions", []).append(create_condition("blinded", source_id="concentration:darkness"))
        lines.append(f"  → {target.get('name', '?')} 处于黑暗中，按目盲处理。")
    return {"lines": lines}
