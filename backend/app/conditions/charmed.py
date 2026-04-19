from app.conditions._base import ConditionDef, CombatEffects

# 魅惑的"不能攻击魅惑者"和"社交检定优势"依赖 source_id 追踪，
# 由 on_attack_eligibility 钩子在攻击解算时判定
CONDITION_DEF = ConditionDef(
    id="charmed",
    name_cn="魅惑",
    description=(
        "被魅惑生物不能攻击魅惑者，其造成伤害的能力或魔法效应也不能以魅惑者为目标。"
        "魅惑者对被魅惑生物进行任何与社交相关的属性检定时具有优势。"
    ),
    effects=CombatEffects(),
)


def on_attack_eligibility(condition: dict, attacker: dict, target: dict) -> str | None:
    """魅惑：不能攻击施加魅惑的来源单位"""
    if condition.get("source_id") == target.get("id", ""):
        return f"{attacker.get('name', '?')} 被魅惑，无法攻击 {target.get('name', '?')}！"
    return None
