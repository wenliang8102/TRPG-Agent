"""焰球 — 追踪焰球的位置与持续时间，后续怪物/工具动作可复用。"""

from app.conditions._base import ConditionDef

CONDITION_DEF = ConditionDef(
    id="flaming_sphere",
    name_cn="焰球",
    description="施法者维持一个可移动的火焰球体；本地实现记录其空间位置与碰撞伤害。需要专注。",
)
