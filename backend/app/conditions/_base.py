"""状态系统核心类型 — ActiveCondition 模型与 ConditionDef 元数据"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CombatEffects(BaseModel):
    """状态对战斗系统的数据驱动效果声明，由战斗解算自动查询"""
    # 该单位发起攻击时的优劣势
    attack_advantage: Literal["advantage", "disadvantage"] | None = None
    # 该单位被攻击时，攻击者获得的优劣势
    defend_advantage: Literal["advantage", "disadvantage"] | None = None
    # 速度归零
    speed_zero: bool = False
    # 无法执行动作
    prevents_actions: bool = False
    # 无法执行反应
    prevents_reactions: bool = False
    # 对指定属性的豁免具有劣势（如 Restrained → ["dex"]）
    save_disadvantage: list[str] = Field(default_factory=list)
    # 无法移动（如 Restrained / Grappled）
    prevents_movement: bool = False
    # 受击即消耗（如 曳光弹造优标记 / 魅惑状态被攻击解除）
    consume_on_attacked: bool = False


class ConditionDef(BaseModel):
    """状态的静态定义 — 注册表中每个状态的描述与效果"""
    id: str
    name_cn: str
    description: str
    effects: CombatEffects = Field(default_factory=CombatEffects)


class ActiveCondition(BaseModel):
    """角色身上的一个活跃状态实例 — 支持来源追踪与持续时间，为法术/技能状态预留拓展"""
    id: str                                # "blinded", "hunters_mark" 等
    source_id: str = ""                    # 施加来源（单位 ID 或法术名）
    duration: int | None = None            # 剩余回合数，None = 手动移除
    extra: dict = Field(default_factory=dict)  # 法术/技能专属数据预留

    model_config = {"arbitrary_types_allowed": True}


# ── 查询辅助 ────────────────────────────────────────────────────


def has_condition(conditions: list[dict], condition_id: str) -> bool:
    """检查状态列表中是否存在指定 ID 的状态"""
    return any(c.get("id") == condition_id for c in conditions)


def find_condition(conditions: list[dict], condition_id: str) -> dict | None:
    """从状态列表中取出指定 ID 的第一个匹配"""
    return next((c for c in conditions if c.get("id") == condition_id), None)


def tick_conditions(conditions: list[dict]) -> tuple[list[dict], list[str]]:
    """回合结束时递减持续时间，返回 (剩余状态列表, 本次过期的状态 ID 列表)"""
    remaining: list[dict] = []
    expired: list[str] = []
    for c in conditions:
        dur = c.get("duration")
        if dur is None:
            remaining.append(c)
            continue
        dur -= 1
        if dur <= 0:
            expired.append(c["id"])
        else:
            c["duration"] = dur
            remaining.append(c)
    return remaining, expired
