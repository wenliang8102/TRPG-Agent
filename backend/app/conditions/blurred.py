"""朦胧 (Blurred) 条件 — 模糊术制造的攻击劣势。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="blurred",
    name_cn="朦胧",
    description="目标身形扭曲晃动，对其发动的攻击检定具有劣势。",
    effects=CombatEffects(defend_advantage="disadvantage"),
)
