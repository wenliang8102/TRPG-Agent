"""怪物动作模型辅助函数。"""

from __future__ import annotations

from app.graph.state import AttackInfo
from app.monsters.models import DamagePart, MonsterAction, RechargeSpec


# 结构化动作仍要兼容旧攻击入口，转换逻辑集中在这里。
def action_to_attack_info(action: MonsterAction) -> AttackInfo:
    """把普通攻击动作降级为旧 AttackInfo，供 attack_action 复用。"""
    first_damage = action.damage[0] if action.damage else DamagePart()
    return AttackInfo(
        name=action.name,
        attack_bonus=action.attack_bonus or 0,
        damage_dice=first_damage.dice,
        damage_type=first_damage.damage_type,
        reach_feet=action.reach_feet or 5,
        normal_range_feet=action.normal_range_feet,
        long_range_feet=action.long_range_feet,
    )


def recharge_5_6() -> RechargeSpec:
    """D&D 常见 Recharge 5-6，避免本地数据到处手写字段。"""
    return RechargeSpec(die="1d6", min_roll=5)
