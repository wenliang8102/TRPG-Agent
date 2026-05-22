"""状态系统核心类型 — ActiveCondition 模型与 ConditionDef 元数据"""

from __future__ import annotations

from typing import Any
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


# ── 条件实例构造辅助 ────────────────────────────────────────────


def build_condition_extra(
    *,
    save_ends: dict[str, Any] | None = None,
    expire_on_turn_start_of: str | None = None,
    consume_on_attacked: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    """统一构造条件 extra，避免各法术散落地手写生命周期字段。"""
    data = dict(extra)
    if save_ends is not None:
        data["save_ends"] = save_ends
    if expire_on_turn_start_of is not None:
        data["expire_on_turn_start_of"] = expire_on_turn_start_of
    if consume_on_attacked:
        data["consume_on_attacked"] = True
    return data


def create_condition(
    condition_id: str,
    *,
    source_id: str = "",
    duration: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一创建活跃状态实例，保持 spells/tools 的挂载结构一致。"""
    return ActiveCondition(
        id=condition_id,
        source_id=source_id,
        duration=duration,
        extra=extra or {},
    ).model_dump()


def coerce_condition_input(raw_condition: str | dict[str, Any]) -> dict[str, Any]:
    """把字符串或散落 dict 统一标准化为 ActiveCondition 结构。"""
    if isinstance(raw_condition, str):
        return create_condition(raw_condition)

    data = dict(raw_condition)
    normalized = create_condition(
        data["id"],
        source_id=data.get("source_id", ""),
        duration=data.get("duration"),
        extra=dict(data.get("extra", {})),
    )
    for key, value in data.items():
        if key not in {"id", "source_id", "duration", "extra"}:
            normalized[key] = value
    return normalized


def upsert_condition(
    target: dict,
    raw_condition: str | dict[str, Any],
    *,
    replace_existing: bool = False,
) -> tuple[dict[str, Any], bool]:
    """向目标挂载条件；必要时替换同 ID 旧状态。"""
    normalized = coerce_condition_input(raw_condition)
    condition_id = normalized["id"]
    conditions = target.setdefault("conditions", [])

    exists = has_condition(conditions, condition_id)
    if exists and not replace_existing:
        return normalized, False

    if exists:
        target["conditions"] = [condition for condition in conditions if condition.get("id") != condition_id]

    target.setdefault("conditions", []).append(normalized)
    return normalized, True


def remove_condition_by_id(target: dict, condition_id: str) -> bool:
    """按条件 ID 移除目标身上的状态，返回是否有实际删除。"""
    conditions = target.get("conditions", [])
    remaining = [condition for condition in conditions if condition.get("id") != condition_id]
    if len(remaining) == len(conditions):
        return False

    target["conditions"] = remaining
    return True


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
