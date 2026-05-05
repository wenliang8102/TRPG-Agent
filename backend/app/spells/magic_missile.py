"""魔法飞弹 (Magic Missile) — 1环塑能，自动命中多目标"""

import d20

from app.spells._base import SpellDef, SpellResult
from app.services.tools._helpers import apply_damage_to_target

SPELL_DEF: SpellDef = {
    "name": "Magic Missile",
    "name_cn": "魔法飞弹",
    "level": 1,
    "school": "evocation",
    "casting_time": "action",
    "range": "120 feet",
    "description": "自动命中，发射3枚飞弹(升环+1枚)，每枚1d4+1力场伤害。可分配给不同目标。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """无需命中骰，每枚 1d4+1，基础3枚，每升1环+1枚"""
    dart_count = 3 + (slot_level - 1)
    caster_name = caster.get("name", "?")

    # 均匀分配飞弹到各目标
    per_target = dart_count // len(targets)
    remainder = dart_count % len(targets)

    lines: list[str] = [f"{caster_name} 施放 魔法飞弹（{slot_level}环）— {dart_count} 枚飞弹!"]
    hp_changes: list[dict] = []

    for i, target in enumerate(targets):
        n = per_target + (1 if i < remainder else 0)
        total_damage = 0
        rolls: list[str] = []
        for _ in range(n):
            r = d20.roll("1d4+1")
            total_damage += r.total
            rolls.append(str(r))

        target_name = target.get("name", "?")
        lines.append(f"  → {target_name}: {n}枚 [{', '.join(rolls)}] = {total_damage} 力场伤害")

        _, hp_change, damage_lines = apply_damage_to_target(target, total_damage, damage_type="force")
        hp_changes.append(hp_change)
        lines.extend(f"  {line}" for line in damage_lines)

    return {"lines": lines, "hp_changes": hp_changes}
