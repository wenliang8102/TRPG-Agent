"""怪物领域数据入口。"""

from app.monsters.models import (
    DamagePart,
    EffectSpec,
    MonsterAction,
    RechargeSpec,
    SaveSpec,
    TargetSpec,
)

__all__ = [
    "DamagePart",
    "EffectSpec",
    "MonsterAction",
    "RechargeSpec",
    "SaveSpec",
    "TargetSpec",
]
