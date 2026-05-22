"""麻痹 (Paralyzed) 条件 — 由 Hold Person 等法术施加

效果：禁止动作和反应，STR/DEX 豁免自动失败，
被攻击时攻击者有优势，5 尺内近战命中自动暴击。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="paralyzed",
    name_cn="麻痹",
    description="无法移动或说话。自动未通过 STR/DEX 豁免。攻击者有优势，5尺内近战命中自动暴击。",
    effects=CombatEffects(
        prevents_actions=True,
        prevents_reactions=True,
        prevents_movement=True,
        speed_zero=True,
        # 被攻击时攻击者获得优势
        defend_advantage="advantage",
    ),
)


def on_attack_eligibility(condition: dict, attacker: dict, target: dict) -> str | None:
    """麻痹单位无法发起攻击"""
    return f"{attacker.get('name', '?')} 处于麻痹状态，无法行动！"


def on_attack_resolved(condition: dict, attacker: dict, target: dict, roll_info: dict) -> dict | None:
    """麻痹目标被命中后，将暴击改判收敛到条件钩子里。"""
    if not roll_info.get("hit") or roll_info.get("crit"):
        return None
    return {"crit": True}


def auto_fail_save(condition: dict, actor: dict, ability: str) -> str | None:
    """麻痹导致 STR/DEX 豁免自动失败。"""
    if ability in {"str", "dex"}:
        return "麻痹导致该豁免自动失败"
    return None
