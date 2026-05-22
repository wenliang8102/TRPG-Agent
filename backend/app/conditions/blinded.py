from app.conditions._base import ConditionDef, CombatEffects

CONDITION_DEF = ConditionDef(
    id="blinded",
    name_cn="目盲",
    description=(
        "目盲的生物不能视物，其所有需要视觉的属性检定直接失败。"
        "对目盲的生物发动攻击检定时具有优势；目盲生物发动攻击检定时具有劣势。"
    ),
    effects=CombatEffects(
        attack_advantage="disadvantage",
        defend_advantage="advantage",
    ),
)
