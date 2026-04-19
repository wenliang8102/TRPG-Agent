"""曳光弹 (Guiding Bolt) — 1环塑能，远程法术攻击，光耀伤害及单次造优"""

from app.conditions._base import ActiveCondition
from app.spells._base import SpellDef, SpellResult
from app.spells._resolvers import resolve_spell_attack

SPELL_DEF: SpellDef = {
    "name": "Guiding Bolt",
    "name_cn": "曳光弹",
    "level": 1,
    "school": "evocation",
    "casting_time": "action",
    "range": "120 feet",
    "description": "进行一次远程法术攻击。命中受到 4d6 点光耀伤害，并在你的下个回合结束前，对目标发动的下一次攻击检定具有优势。升环每高一环伤害增加 1d6。",
}


def _apply_mark(caster: dict, target: dict, lines: list[str]) -> None:
    """命中后附加曳光弹造优标记"""
    target_name = target.get("name", "?")
    caster_name = caster.get("name", "?")
    target_conditions = target.setdefault("conditions", [])
    mark_cond = ActiveCondition(
        id="guiding_bolt_mark",
        source_id=caster_name,
        duration=1,
        extra={"consume_on_attacked": True},
    )
    target_conditions.append(mark_cond.model_dump())
    lines.append(
        f"  [造优效果] 秘法的微光在 {target_name} 身上闪耀，"
        f"对它发动的下一次攻击检定具有优势！(持续至 {caster_name} 下回合结束)"
    )


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """1个目标，远程法术攻击，4d6光耀伤害（升环每环+1d6），命中造优"""
    if not targets:
        return {"lines": ["未指定目标。"]}

    dice_count = 4 + max(0, slot_level - 1)
    return resolve_spell_attack(
        caster, targets[0],
        spell_name_cn="曳光弹",
        slot_level=slot_level,
        damage_formula=f"{dice_count}d6",
        damage_type="光耀",
        on_hit_extra=_apply_mark,
    )
