"""束缚 (Restrained) 条件 — 用于蛛网等怪物动作。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="restrained",
    name_cn="束缚",
    description="速度变为0，攻击具有劣势，被攻击时攻击者具有优势，DEX豁免具有劣势。",
    effects=CombatEffects(
        attack_advantage="disadvantage",
        defend_advantage="advantage",
        prevents_movement=True,
        speed_zero=True,
        save_disadvantage=["dex"],
    ),
)

