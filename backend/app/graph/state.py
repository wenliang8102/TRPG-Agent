# backend/app/graph/state.py
from __future__ import annotations

from typing import Any, Annotated, Literal, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.conditions._base import ActiveCondition
from app.monsters.models import MonsterAction


# ── Pydantic 数据模型 ──────────────────────────────────────────
# GraphState 保持 TypedDict（LangGraph 框架要求），嵌套字段用 Pydantic 获得校验能力。
# 所有模型设置 extra="allow" 以兼容旧代码向 dict 中塞入额外字段的写法。

# 六维能力值/修正值 — 字段名 str/int 与 Python 内置冲突，用 dict 类型别名最干净
AbilityBlock = dict[str, int]
ModifierBlock = dict[str, int]


class WeaponData(BaseModel):
    """武器基础数据 — 对应 D&D 5e SRD 武器表"""
    name: str
    damage_dice: str = "1d4"
    damage_type: str = "bludgeoning"
    weapon_type: Literal["melee", "ranged"] = "melee"
    properties: list[str] = Field(default_factory=list)  # finesse, light, thrown, ...


class PlayerState(BaseModel, extra="allow"):
    """玩家常驻状态"""
    name: str = ""
    role_class: str = ""
    level: int = 1
    hp: int = 0
    max_hp: int = 0
    temp_hp: int = 0
    base_ac: int = 10                     # 无 buff 裸 AC，最终 AC 由 compute_ac() 动态计算
    ac: int = 10                          # 向后兼容别名，新代码应用 base_ac
    abilities: AbilityBlock = Field(default_factory=dict)
    modifiers: ModifierBlock = Field(default_factory=dict)
    conditions: list[ActiveCondition] = Field(default_factory=list)
    resources: dict[str, int] = Field(default_factory=dict)
    weapons: list[WeaponData] = Field(default_factory=list)
    known_spells: list[str] = Field(default_factory=list)
    known_cantrips: list[str] = Field(default_factory=list)
    spellcasting_ability: str = ""
    class_features: list[str] = Field(default_factory=list)
    concentrating_on: str | None = None  # 当前专注的法术 ID
    xp: int = 0
    arcane_tradition: str = ""  # 奥术传承（如 "evocation", "abjuration"）


class CheckState(BaseModel, extra="allow"):
    """待执行的一次检定请求"""
    kind: Literal["attack", "skill", "save", "custom"] = "custom"
    ability: Literal["str", "dex", "con", "int", "wis", "cha"] = "str"
    dc: int = 10
    target: Optional[str] = None
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal"


class RollResultState(BaseModel, extra="allow"):
    """最近一次掷骰结果"""
    dice: str = "1d20"
    raw: int = 0
    modifier: int = 0
    total: int = 0
    success: bool = False


class AttackRollState(BaseModel, extra="allow"):
    """一次攻击命中判定的完整快照，作为反应与前端展示的唯一事实源"""
    blocked: bool = False
    hit: bool = False
    crit: bool = False
    natural: int = 0
    raw_roll: int = 0
    attack_bonus: int = 0
    hit_total: int = 0
    target_ac: int = 10
    dmg_dice: str = "1d4"
    dmg_type: str = "bludgeoning"
    atk_name_display: str = ""
    advantage_used: Literal["normal", "advantage", "disadvantage"] = "normal"
    lines: list[str] = Field(default_factory=list)
    deflected: bool = False


class ReactionOptionState(BaseModel, extra="allow"):
    """一次反应提示里可选择的单个反应选项"""
    spell_id: str
    name_cn: str = ""
    min_slot: int = 1
    description: str = ""


class ReactionChoiceState(BaseModel, extra="allow"):
    """用户提交的反应选择"""
    spell_id: str | None = None
    slot_level: int | None = None


class PendingReactionState(BaseModel, extra="allow"):
    """等待玩家决定是否反应的攻击上下文"""
    type: Literal["reaction_prompt"] = "reaction_prompt"
    trigger: str = "on_hit"
    attacker_id: str = ""
    attacker_name: str = ""
    target_id: str = ""
    target_name: str = ""
    attack_roll: AttackRollState = Field(default_factory=AttackRollState)
    available_reactions: list[ReactionOptionState] = Field(default_factory=list)


class AttackInfo(BaseModel):
    """从怪物/角色动作列表中提取的单次攻击信息"""
    name: str
    attack_bonus: int = 0
    damage_dice: str = "1d4"          # d20 库可直接解析的表达式
    damage_type: str = "bludgeoning"
    reach_feet: int = 5               # 近战触及范围；远程攻击保留默认值供机会攻击等后续规则复用
    normal_range_feet: int | None = None
    long_range_feet: int | None = None


class CombatantState(BaseModel, extra="allow"):
    """战斗单位快照（玩家/敌人/友方）"""
    id: str = ""
    name: str = ""
    side: Literal["player", "enemy", "ally"] = "enemy"
    hp: int = 0
    max_hp: int = 0
    base_ac: int = 10                     # 无 buff 裸 AC
    ac: int = 10                          # 向后兼容别名
    initiative: int = 0
    speed: int = 30
    conditions: list[ActiveCondition] = Field(default_factory=list)
    damage_resistances: list[str] = Field(default_factory=list)
    damage_immunities: list[str] = Field(default_factory=list)
    damage_vulnerabilities: list[str] = Field(default_factory=list)

    # 六维能力值与修正（用于怪物攻击/豁免计算）
    abilities: AbilityBlock = Field(default_factory=dict)
    modifiers: ModifierBlock = Field(default_factory=dict)
    proficiency_bonus: int = 2

    # 该单位可用的攻击列表（从 Open5e actions 解析）
    attacks: list[AttackInfo] = Field(default_factory=list)
    # 结构化动作列表；后续怪物优先通过这里扩展能力。
    actions: list[MonsterAction] = Field(default_factory=list)
    traits: list[str] = Field(default_factory=list)
    action_recharges: dict[str, bool] = Field(default_factory=dict)

    # 动作资源
    action_available: bool = True
    bonus_action_available: bool = True
    reaction_available: bool = True
    movement_left: int = 30


class CombatState(BaseModel, extra="allow"):
    """战斗对局整体状态（扁平化动作经济管理）"""
    round: int = 0
    participants: dict[str, CombatantState] = Field(default_factory=dict)
    initiative_order: list[str] = Field(default_factory=list)
    current_actor_id: str = ""


class Point2D(BaseModel):
    """二维坐标点 — 空间系统只承认平面位置，不引入 z 轴"""
    x: float = 0
    y: float = 0


class PlaneMapState(BaseModel, extra="allow"):
    """平面地图定义 — 只保存可序列化边界信息，几何计算交给 Shapely 临时完成"""
    id: str = ""
    name: str = ""
    width: float = 100
    height: float = 100
    grid_size: float = 5
    description: str = ""


class UnitPlacementState(BaseModel, extra="allow"):
    """单位在某张平面地图上的落点"""
    unit_id: str = ""
    map_id: str = ""
    position: Point2D = Field(default_factory=Point2D)
    facing_deg: float = 0
    footprint_radius: float = 2.5


class SpaceState(BaseModel, extra="allow"):
    """空间系统总状态 — 地图与单位落点独立于战斗数值，便于剧情换图和跨阶段复用"""
    active_map_id: str = ""
    maps: dict[str, PlaneMapState] = Field(default_factory=dict)
    placements: dict[str, UnitPlacementState] = Field(default_factory=dict)


def merge_space_state(left: SpaceState | dict | None, right: SpaceState | dict | None) -> dict:
    """合并同一步工具产生的空间快照，避免并发摆放单位时互相覆盖。"""
    if right is None:
        return _space_dict(left)
    if left is None:
        return _space_dict(right)

    left_dict = _space_dict(left)
    right_dict = _space_dict(right)
    merged = {
        **left_dict,
        **right_dict,
        "maps": {
            **left_dict.get("maps", {}),
            **right_dict.get("maps", {}),
        },
        "placements": _merge_space_placements(
            left_dict.get("placements", {}),
            right_dict.get("placements", {}),
        ),
    }
    return SpaceState.model_validate(merged).model_dump()


def _space_dict(value: SpaceState | dict | None) -> dict:
    """把 reducer 收到的 Pydantic/dict 统一成普通 dict，便于做浅层领域合并。"""
    if value is None:
        return SpaceState().model_dump()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return SpaceState.model_validate(value).model_dump()


def _merge_space_placements(left: dict, right: dict) -> dict:
    """并发新增取并集；纯删除或移动取右侧快照，保留 remove_unit/end_combat 语义。"""
    left_keys = set(left)
    right_keys = set(right)
    if right_keys <= left_keys:
        return dict(right)
    return {**left, **right}


# ── LangGraph 共享状态 ─────────────────────────────────────────


class GraphState(TypedDict, total=False):
    """整个 LangGraph 在节点间传递的共享状态"""

    # --- 核心对话流程字段 ---
    messages: Annotated[list[AnyMessage], add_messages]
    output: str

    conversation_summary: str
    episodic_context: list[str]
    session_id: str
    active_combat_message_start: Optional[int]
    combat_archives: list[dict[str, Any]]

    # --- 扩展领域字段 ---
    phase: Literal["exploration", "combat", "resolution"]

    scene_summary: str
    player: PlayerState

    pending_check: Optional[CheckState]
    last_roll: Optional[RollResultState]
    pending_reaction: Optional[PendingReactionState]
    reaction_choice: Optional[ReactionChoiceState]

    # 场景单位池 — spawn 产出放这里，start_combat 从中挑选参战者
    scene_units: dict[str, CombatantState]

    # 平面空间状态 — 记录地图与单位坐标，供移动、距离、范围判定复用
    space: Annotated[SpaceState, merge_space_state]

    combat: Optional[CombatState]

    # 战斗结束后的死亡单位归档（搜尸体等剧情用途）
    dead_units: dict[str, CombatantState]

    # 本轮攻击产生的 HP 变动记录，供前端渲染血条动画
    hp_changes: list[dict]

    event_log: list[dict]
