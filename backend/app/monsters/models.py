"""怪物动作领域模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DamagePart(BaseModel):
    """一次动作中的一段伤害，允许同次命中混合多种伤害类型。"""
    dice: str = "1d4"
    damage_type: str = "bludgeoning"


class EffectSpec(BaseModel):
    """动作附加效果声明，resolver 只解释当前批次需要的效果。"""
    kind: Literal["condition", "damage", "description", "reduce_max_hp_by_damage"] = "description"
    apply_to: Literal["target", "self"] = "target"
    condition_id: str | None = None
    duration: int | None = None
    save_ends: dict[str, Any] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    damage: DamagePart | None = None
    damage_multiplier: Literal["full", "half"] = "full"
    text: str = ""


class SaveSpec(BaseModel):
    """豁免规则声明，用数据表达 DC、属性以及成功/失败后的效果。"""
    ability: Literal["str", "dex", "con", "int", "wis", "cha"] = "str"
    dc: int = 10
    success: list[EffectSpec] = Field(default_factory=list)
    failure: list[EffectSpec] = Field(default_factory=list)


class TargetSpec(BaseModel):
    """动作目标形态；当前覆盖单体、多目标、半径和锥形。"""
    kind: Literal["single", "multi", "radius", "cone"] = "single"
    count: int = 1
    radius_feet: float | None = None
    length_feet: float | None = None
    angle_deg: float = 53.13
    include_self: bool = False


class RechargeSpec(BaseModel):
    """Recharge X-Y 的最小数据模型，默认表达 5-6。"""
    die: str = "1d6"
    min_roll: int = 5


class MonsterAction(BaseModel):
    """结构化怪物动作；普通攻击、多重攻击和豁免能力共享这一层数据。"""
    id: str
    name: str
    kind: Literal[
        "attack",
        "multiattack",
        "save_effect",
        "area_save",
        "spell",
        "bonus_action",
        "reaction",
        "special",
    ]
    action_type: Literal["action", "bonus_action", "reaction"] = "action"
    attack_bonus: int | None = None
    damage: list[DamagePart] = Field(default_factory=list)
    reach_feet: int | None = None
    normal_range_feet: int | None = None
    long_range_feet: int | None = None
    target: TargetSpec = Field(default_factory=TargetSpec)
    save: SaveSpec | None = None
    on_hit: list[EffectSpec] = Field(default_factory=list)
    recharge: RechargeSpec | None = None
    sequence: list[str] = Field(default_factory=list)
    sequence_mode: Literal["all", "on_previous_hit"] = "all"
    spell_id: str | None = None
    slot_level: int = 0
    description: str = ""
