"""暗示术状态 — 记录暗示文本，并阻止明显违背暗示的工具行动。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="suggestion",
    name_cn="暗示",
    description="目标受到一句合理暗示影响；具体执行由叙事与战斗上下文解释。需要专注。",
    effects=CombatEffects(),
)


def on_attack_eligibility(condition: dict, attacker: dict, target: dict) -> str | None:
    """被暗示的目标不能直接攻击暗示来源，避免最常见的规则穿透。"""
    source_id = condition.get("extra", {}).get("source_actor_id") or condition.get("source_id", "")
    if source_id and target.get("id") == source_id:
        return f"{attacker.get('name', '?')} 正受暗示术影响，不能直接攻击暗示来源 {target.get('name', '?')}。"
    return None
