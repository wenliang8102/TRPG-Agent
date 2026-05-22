"""迷踪步 (Misty Step) — 2环咒法，附赠动作瞬移 30 尺。"""

from app.spells._base import SpellDef, SpellResult

SPELL_DEF: SpellDef = {
    "name": "Misty Step",
    "name_cn": "迷踪步",
    "level": 2,
    "school": "conjuration",
    "casting_time": "bonus_action",
    "range": "30 feet",
    "description": "你短暂被银雾包裹，瞬移到 30 尺内可见的空位。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **kwargs) -> SpellResult:
    """空间校验和实际落点移动由 cast_spell 统一处理。"""
    caster_name = caster.get("name", "?")
    move_line = kwargs["move_line"]
    return {"lines": [f"{caster_name} 施放 迷踪步。", move_line], "space": kwargs["space_update"]}
