"""电爪 (Shocking Grasp) — 戏法/塑能，近战法术攻击，命中后压制反应。"""

from app.conditions._base import build_condition_extra, create_condition
from app.spells._base import SpellDef, SpellResult
from app.spells._resolvers import resolve_spell_attack

SPELL_DEF: SpellDef = {
    "name": "Shocking Grasp",
    "name_cn": "电爪",
    "level": 0,
    "school": "evocation",
    "casting_time": "action",
    "range": "touch",
    "description": "近战法术攻击，命中造成闪电伤害，目标直到其下个回合开始前不能进行反应。伤害随等级增长。",
}


def _suppress_reaction(caster: dict, target: dict, lines: list[str]) -> None:
    """命中后只压制反应，避免误伤目标下一回合的普通动作。"""
    target_name = target.get("name", "?")
    caster_id = caster.get("id", "")
    target.setdefault("conditions", []).append(
        create_condition(
            "reaction_suppressed",
            source_id=caster_id,
            duration=1,
            extra=build_condition_extra(expire_on_turn_start_of=target.get("id", "")),
        )
    )
    lines.append(f"  [电击] {target_name} 直到其下个回合开始前不能进行反应。")


def execute(caster: dict, targets: list[dict], slot_level: int, *, cantrip_scale: int = 1, **_) -> SpellResult:
    """单目标近战法术攻击，命中造成 1d8 闪电伤害并禁用反应。"""
    if not targets:
        return {"lines": ["未指定目标。"]}

    return resolve_spell_attack(
        caster,
        targets[0],
        spell_name_cn="电爪",
        slot_level=0,
        damage_formula=f"{cantrip_scale}d8",
        damage_type="闪电",
        attack_label="近战法术攻击",
        on_hit_extra=_suppress_reaction,
    )
