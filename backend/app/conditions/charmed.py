from app.conditions._base import ConditionDef, CombatEffects

# 魅惑的"不能攻击魅惑者"和"社交检定优势"依赖 source_id 追踪，
# 由战斗解算层根据 ActiveCondition.source_id 判定，无法纯靠 CombatEffects 标志表达
CONDITION_DEF = ConditionDef(
    id="charmed",
    name_cn="魅惑",
    description=(
        "被魅惑生物不能攻击魅惑者，其造成伤害的能力或魔法效应也不能以魅惑者为目标。"
        "魅惑者对被魅惑生物进行任何与社交相关的属性检定时具有优势。\n"
        "受击即解除：该生物受到攻击后魅惑状态自动解除。"
    ),
    effects=CombatEffects(),
)
