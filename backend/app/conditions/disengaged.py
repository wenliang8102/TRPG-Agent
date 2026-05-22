"""撤离状态 — 标记本回合移动不会触发借机攻击。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="disengaged",
    name_cn="撤离",
    description="直到本回合结束，移动不会触发借机攻击。",
    effects=CombatEffects(),
)
