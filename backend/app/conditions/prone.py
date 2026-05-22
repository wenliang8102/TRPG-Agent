"""倒地 (Prone) 条件 — 近战更容易命中，远程更难命中。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="prone",
    name_cn="倒地",
    description="倒地者只能爬行，近身攻击它具有优势，远程攻击它具有劣势；站起需要消耗一半速度。",
    effects=CombatEffects(),
)


def modify_movement_cost(condition: dict, actor: dict, base_cost: float) -> float:
    """倒地移动按爬行处理：每移动 1 尺消耗 2 尺速度。"""
    return base_cost * 2


def modify_attack_advantage(
    condition: dict,
    role: str,
    attacker: dict,
    target: dict,
    attack: dict | None,
    state: dict | None,
) -> str | None:
    """倒地只影响“攻击倒地目标”的命中，不影响倒地者自己攻击。"""
    if role != "defender":
        return None

    from app.services.tools._helpers import attack_is_melee_like

    return "advantage" if attack_is_melee_like(attacker, target, attack, state) else "disadvantage"
