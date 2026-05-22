"""焰球 (Flaming Sphere) — 2环咒法，持续火焰威胁。"""

import d20

from app.conditions._base import build_condition_extra, create_condition
from app.spells._base import SpellDef, SpellResult

SPELL_DEF: SpellDef = {
    "name": "Flaming Sphere",
    "name_cn": "焰球",
    "level": 2,
    "school": "conjuration",
    "casting_time": "action",
    "range": "60 feet",
    "area": {"shape": "circle", "radius": 5},
    "description": "在射程内创造 5 尺直径火焰球体。附近生物需进行 DEX 豁免，失败受火焰伤害。需要专注，持续 1 分钟。",
    "concentration": True,
}


def damage_targets_near_sphere(
    caster: dict,
    all_combatants: dict[str, dict],
    condition: dict,
    state: dict,
    *,
    trigger: str,
) -> SpellResult:
    """焰球持续伤害统一入口：移动撞击和回合结束贴近都走同一套 DEX 豁免。"""
    from app.services.tools._helpers import apply_damage_to_target, roll_actor_save
    from app.spells._base import get_spell_dc
    from app.space.geometry import build_space_state, units_in_radius

    position = condition.get("extra", {}).get("position", {})
    if not position or not state.get("space"):
        return {"lines": [], "hp_changes": []}

    space = build_space_state(state.get("space"))
    caster_id = caster.get("id", "")
    if caster_id not in space.placements:
        return {"lines": [], "hp_changes": []}

    caster_placement = space.placements[caster_id]
    from app.graph.state import Point2D

    origin = Point2D(**position)
    target_ids = [
        unit_id for unit_id, _ in units_in_radius(
            space.placements,
            map_id=caster_placement.map_id,
            origin=origin,
            radius=5,
        )
        if unit_id in all_combatants
    ]
    if not target_ids:
        return {"lines": [], "hp_changes": []}

    damage_dice = condition.get("extra", {}).get("damage_dice", "2d6")
    damage_roll = d20.roll(damage_dice)
    full_damage = max(1, damage_roll.total)
    half_damage = full_damage // 2
    spell_dc = get_spell_dc(caster)
    lines = [f"  [焰球{trigger}] 伤害骰 {damage_roll} = {full_damage} 火焰伤害，DC {spell_dc} DEX 豁免。"]
    hp_changes: list[dict] = []

    for target_id in target_ids:
        target = all_combatants[target_id]
        save_roll, auto_fail_reason, disadvantaged = roll_actor_save(target, "dex")
        saved = False if auto_fail_reason else save_roll.total >= spell_dc
        roll_text = f"自动失败（{auto_fail_reason}）" if auto_fail_reason else f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
        actual_damage = half_damage if saved else full_damage
        lines.append(f"  → {target.get('name', '?')}: {'成功' if saved else '失败'}({roll_text})，承受 {actual_damage} 火焰伤害。")
        _, hp_change, damage_lines = apply_damage_to_target(target, actual_damage, damage_type="火焰")
        hp_changes.append(hp_change)
        lines.extend(f"  {line}" for line in damage_lines)

    return {"lines": lines, "hp_changes": hp_changes}


def execute(caster: dict, targets: list[dict], slot_level: int, **kwargs) -> SpellResult:
    """先记录焰球状态；当前点选范围内目标立即承受一次 DEX 豁免伤害。"""
    from app.spells._resolvers import resolve_aoe_save

    caster_name = caster.get("name", "?")
    target_point = kwargs.get("target_point", {})
    caster["conditions"] = [c for c in caster.get("conditions", []) if c.get("id") != "flaming_sphere"]
    caster.setdefault("conditions", []).append(
        create_condition(
            "flaming_sphere",
            source_id="concentration:flaming_sphere",
            duration=10,
            extra=build_condition_extra(position=target_point, damage_dice=f"{2 + max(0, slot_level - 2)}d6"),
        )
    )

    result = resolve_aoe_save(
        caster,
        targets,
        spell_name_cn="焰球",
        slot_level=slot_level,
        damage_formula=f"{2 + max(0, slot_level - 2)}d6",
        damage_type="火焰",
        save_ability="dex",
        spell_school="conjuration",
    )
    result["lines"] = [
        f"{caster_name} 施放 焰球，火焰球体出现在 ({target_point.get('x', '?')}, {target_point.get('y', '?')})。",
        *result.get("lines", []),
    ]
    return result
