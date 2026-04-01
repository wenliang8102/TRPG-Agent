# backend/app/graph/state.py
from typing import Literal, Optional, TypedDict


# 角色六维能力值（通常用于检定基础值）
class AbilityBlock(TypedDict, total=False):
    str: int  # 力量
    dex: int  # 敏捷
    con: int  # 体质
    int: int  # 智力
    wis: int  # 感知
    cha: int  # 魅力


# 六维对应修正值（通常由能力值推导）
class ModifierBlock(TypedDict, total=False):
    str: int  # 力量修正
    dex: int  # 敏捷修正
    con: int  # 体质修正
    int: int  # 智力修正
    wis: int  # 感知修正
    cha: int  # 魅力修正


# 玩家常驻状态（可在探索/战斗阶段复用）
class PlayerState(TypedDict, total=False):
    name: str
    role_class: str
    level: int
    hp: int
    max_hp: int
    temp_hp: int
    ac: int
    abilities: AbilityBlock
    modifiers: ModifierBlock
    conditions: list[str]          # e.g. ["poisoned", "prone"]
    resources: dict[str, int]      # e.g. {"spell_slot_lv1": 2}


# 待执行的一次检定请求
class CheckState(TypedDict, total=False):
    kind: Literal["attack", "skill", "save", "custom"]
    ability: Literal["str", "dex", "con", "int", "wis", "cha"]
    dc: int
    target: Optional[str]
    advantage: Literal["normal", "advantage", "disadvantage"]


# 最近一次掷骰结果
class RollResultState(TypedDict, total=False):
    dice: str                      # e.g. "1d20"
    raw: int
    modifier: int
    total: int
    success: bool


# 战斗单位快照（玩家/敌人/友方）
class CombatantState(TypedDict, total=False):
    id: str
    name: str
    side: Literal["player", "enemy", "ally"]
    hp: int
    max_hp: int
    ac: int
    conditions: list[str]


# 整个 LangGraph 在节点间传递的共享状态
class GraphState(TypedDict, total=False):
    # --- 现有流程字段 ---
    messages: list[dict]           # 对话历史（供模型上下文使用）
    user_input: str                # 当前回合输入
    plan: str                      # planner 节点产出的中间计划
    output: str                    # executor 节点产出的最终回复

    # --- V1 新增 ---
    session_id: str                # 会话唯一标识
    phase: Literal["exploration", "combat", "resolution"]
    turn_index: int                # 当前回合序号

    scene_summary: str             # 场景摘要，减少长上下文重复
    player: PlayerState

    pending_check: Optional[CheckState]      # 等待掷骰解析的检定
    last_roll: Optional[RollResultState]     # 最近一次检定/攻击结果

    in_combat: bool
    round: int
    combatants: list[CombatantState]

    event_log: list[dict]          # 记录关键事件，便于回放/调试