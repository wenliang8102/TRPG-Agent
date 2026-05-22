"""护盾术 (Shield) — 1环防护，反应动作 AC+5

AC 提升效果由 conditions/shield_active.py 的 modify_ac 钩子在 compute_ac() 时动态计算，
本法术只负责挂载 shield_active 条件，并在施法者下个回合开始时移除。"""

from app.conditions._base import build_condition_extra, create_condition, has_condition
from app.services.tools._helpers import sync_ac_state
from app.spells._base import SpellDef, SpellResult

SPELL_DEF: SpellDef = {
    "name": "Shield",
    "name_cn": "护盾术",
    "level": 1,
    "school": "abjuration",
    "casting_time": "reaction",
    "reaction_trigger": "on_hit",
    "range": "self",
    "description": "反应动作施放，直到下一回合开始前AC+5。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """挂载 shield_active 条件，并在施法者下个回合开始前保持 AC+5"""
    target = targets[0]
    caster_name = caster.get("name", "?")
    expire_on_turn_start_of = target.get("id") or caster.get("id") or caster_name

    conditions = target.setdefault("conditions", [])

    # 移除旧的字符串格式兼容标记（如有）
    target["conditions"] = [c for c in conditions if c != "shield_active"]
    conditions = target["conditions"]

    if not has_condition(conditions, "shield_active"):
        conditions.append(
            create_condition(
                "shield_active",
                source_id=caster_name,
                extra=build_condition_extra(expire_on_turn_start_of=expire_on_turn_start_of),
            )
        )

    lines = [
        f"{caster_name} 施放 护盾术!",
        f"AC +5（持续到下一回合开始）",
    ]
    sync_ac_state(target)

    return {"lines": lines}
