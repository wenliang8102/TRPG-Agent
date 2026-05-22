"""贴附 (Attached) 条件 — 用于 Stirge 吸血后的持续效果。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="attached",
    name_cn="贴附",
    description="贴附在目标身上，回合开始造成吸血伤害；可主动脱离或被手动移除。",
    effects=CombatEffects(prevents_movement=True, speed_zero=True),
)

