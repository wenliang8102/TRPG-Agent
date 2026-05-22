"""护盾术状态 — 反应施放，AC+5 持续到施法者下一回合开始"""

from app.conditions._base import ConditionDef, CombatEffects

CONDITION_DEF = ConditionDef(
    id="shield_active",
    name_cn="护盾术",
    description="护盾术生效中，AC+5，持续到施法者下一回合开始。",
    effects=CombatEffects(),
)


def modify_ac(unit: dict, current_ac: int) -> int:
    """护盾术：AC 直接 +5"""
    return current_ac + 5
