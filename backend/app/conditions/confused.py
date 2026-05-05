"""困惑状态 — 用于 Spectator 的 Confusion Ray。"""

import d20

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="confused",
    name_cn="困惑",
    description="不能反应；回合开始随机决定本回合行为。",
    effects=CombatEffects(prevents_reactions=True),
)


def on_turn_start(condition: dict, actor: dict, all_combatants: dict[str, dict]) -> list[str]:
    """原规则是 d8 随机行为；把结果写入 extra，后续动作资格读取。"""
    roll = d20.roll("1d8")
    extra = condition.setdefault("extra", {})
    extra["roll"] = roll.total
    if roll.total <= 4:
        extra["behavior"] = "no_action"
        text = "不能移动，也不能执行动作。"
    elif roll.total <= 6:
        extra["behavior"] = "random_movement"
        text = "不能执行动作，并会以叙事方式随机移动。"
    else:
        extra["behavior"] = "random_melee"
        text = "必须随机攻击触及范围内的一个生物；若没有目标则本回合无动作。"
    return [f"  [{actor.get('name', '?')}] 困惑射线 d8={roll.total}：{text}"]


def on_action_eligibility(condition: dict, actor: dict) -> str | None:
    """困惑结果为 1-6 时直接阻止动作。"""
    behavior = condition.get("extra", {}).get("behavior")
    if behavior in {"no_action", "random_movement"}:
        return f"{actor.get('name', '?')} 受困惑射线影响，本回合不能执行动作。"
    return None
