from app.conditions._base import ConditionDef, CombatEffects

CONDITION_DEF = ConditionDef(
    id="incapacitated",
    name_cn="失能",
    description="失能的生物不能执行任何动作或反应。",
    effects=CombatEffects(
        prevents_actions=True,
        prevents_reactions=True,
    ),
)


def on_attack_eligibility(condition: dict, attacker: dict, target: dict) -> str | None:
    """失能：无法执行任何动作"""
    return f"{attacker.get('name', '?')} 处于失能状态，无法行动！"
