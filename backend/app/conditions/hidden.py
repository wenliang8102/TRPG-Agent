"""隐藏状态 — 用于 Nimble Escape 的 Hide 收益。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="hidden",
    name_cn="隐藏",
    description="隐藏者攻击具有优势，被攻击时攻击者具有劣势；攻击或施法后显露。",
    effects=CombatEffects(
        attack_advantage="advantage",
        defend_advantage="disadvantage",
    ),
)
