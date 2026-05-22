from app.conditions._base import ConditionDef, CombatEffects

CONDITION_DEF = ConditionDef(
    id="invisible",
    name_cn="隐形",
    description=(
        "隐形的生物只能被魔法或特殊感官观察到。在判断隐匿时，该生物视为处于重度遮蔽。"
        "对隐形的生物发动攻击检定时具有劣势；隐形的生物发动攻击检定时具有优势。"
    ),
    effects=CombatEffects(
        attack_advantage="advantage",
        defend_advantage="disadvantage",
    ),
)
