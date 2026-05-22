"""妖火标记 — 被妖火描边后攻击者更容易命中。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="faerie_fire_mark",
    name_cn="妖火标记",
    description="目标轮廓被魔法光辉描出，针对该目标的攻击检定具有优势，且无法从隐形中获益。",
    effects=CombatEffects(defend_advantage="advantage"),
)
