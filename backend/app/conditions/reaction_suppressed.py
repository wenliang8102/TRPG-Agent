"""反应压制 — 用于电爪这类只禁止反应的短效状态。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="reaction_suppressed",
    name_cn="反应压制",
    description="目标暂时不能执行反应。该状态不阻止普通动作。",
    effects=CombatEffects(prevents_reactions=True),
)
