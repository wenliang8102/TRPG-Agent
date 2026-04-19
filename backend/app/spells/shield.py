"""护盾术 (Shield) — 1环防护，反应动作 AC+5

AC 提升效果由 conditions/shield_active.py 的 modify_ac 钩子在 compute_ac() 时动态计算，
本法术只负责挂载 shield_active 条件（duration=1，回合结束自动过期）。"""

from app.conditions._base import ActiveCondition, has_condition
from app.spells._base import SpellDef, SpellResult

SPELL_DEF: SpellDef = {
    "name": "Shield",
    "name_cn": "护盾术",
    "level": 1,
    "school": "abjuration",
    "casting_time": "reaction",
    "range": "self",
    "description": "反应动作施放，直到下一回合开始前AC+5。",
}


def execute(caster: dict, targets: list[dict], slot_level: int, **_) -> SpellResult:
    """挂载 shield_active 条件（duration=1），AC+5 由 compute_ac() 动态处理"""
    target = targets[0]
    caster_name = caster.get("name", "?")

    conditions = target.setdefault("conditions", [])

    # 移除旧的字符串格式兼容标记（如有）
    target["conditions"] = [c for c in conditions if c != "shield_active"]
    conditions = target["conditions"]

    if not has_condition(conditions, "shield_active"):
        conditions.append(
            ActiveCondition(
                id="shield_active",
                source_id=caster_name,
                duration=1,
            ).model_dump()
        )

    lines = [
        f"{caster_name} 施放 护盾术!",
        f"AC +5（持续到下一回合开始）",
    ]

    return {"lines": lines}
