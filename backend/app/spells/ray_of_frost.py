"""冰冻射线 (Ray of Frost) — 戏法/塑能，远程法术攻击，冷冻伤害 + 减速"""

from app.conditions import upsert_condition
from app.conditions._base import build_condition_extra, create_condition
from app.spells._resolvers import resolve_spell_attack
from app.spells._base import SpellDef, SpellResult

SPELL_DEF: SpellDef = {
    "name": "Ray of Frost",
    "name_cn": "冰冻射线",
    "level": 0,
    "school": "evocation",
    "casting_time": "action",
    "range": "60 feet",
    "description": "远程法术攻击，命中造成 1d8 冷冻伤害且目标速度降低 10 尺直到你的下一回合开始。伤害随等级增长。",
}


def _apply_slow(caster: dict, target: dict, lines: list[str]) -> None:
    """命中后挂载减速状态，而不是直接篡改基础 speed。"""
    from app.services.tools._helpers import sync_movement_state

    target_name = target.get("name", "?")
    caster_id = caster.get("id", "")
    slow_condition = create_condition(
        "ray_of_frost_slow",
        source_id=caster_id,
        duration=1 if not caster_id else None,
        extra=build_condition_extra(
            speed_penalty=10,
            expire_on_turn_start_of=caster_id,
        ),
    )
    upsert_condition(target, slow_condition, replace_existing=True)
    new_speed = sync_movement_state(target)
    base_speed = target.get("speed", 30)
    lines.append(f"  [冰冻] {target_name} 速度降低 10 尺（{base_speed} → {new_speed}）")


def execute(caster: dict, targets: list[dict], slot_level: int, *, cantrip_scale: int = 1, **_) -> SpellResult:
    """单目标远程法术攻击，1d8 冷冻伤害 + 减速"""
    if not targets:
        return {"lines": ["未指定目标。"]}

    damage_formula = f"{cantrip_scale}d8"
    return resolve_spell_attack(
        caster, targets[0],
        spell_name_cn="冰冻射线",
        slot_level=0,
        damage_formula=damage_formula,
        damage_type="冷冻",
        on_hit_extra=_apply_slow,
    )
