"""冰冻射线减速状态。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="ray_of_frost_slow",
    name_cn="冰冻减速",
    description="速度降低 10 尺，直到施法者下一回合开始。",
    effects=CombatEffects(),
)


def modify_speed(condition: dict, _target: dict, current_speed: int) -> int:
    """把减速值挂在 condition extra，避免直接污染基础 speed。"""
    penalty = int(condition.get("extra", {}).get("speed_penalty", 10))
    return max(0, current_speed - penalty)
