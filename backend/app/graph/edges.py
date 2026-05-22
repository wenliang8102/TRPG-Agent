"""Conditional routes and edge rules."""

from langchain_core.messages import AIMessage

from app.graph.constants import (
    ASSISTANT_NODE,
    COMBAT_END_NODE,
    COMBAT_ASSISTANT_NODE,
    COMBAT_EXECUTOR_NODE,
    COMBAT_RESOLUTION_NODE,
    COMBAT_START_NODE,
    DEATH_SAVE_PAUSE_NODE,
    END_NODE,
    ROUTER_NODE,
    TOOL_NODE,
    REACTION_RESOLUTION_NODE,
    STATE_DEATH_SAVE_PAUSE_TURN_KEY,
)
from app.graph.state import GraphState


def _assistant_node_for_phase(state: GraphState) -> str:
    """根据当前 phase 选择对话代理节点。"""
    if state.get("phase") == "combat" and state.get("combat"):
        return COMBAT_ASSISTANT_NODE
    return ASSISTANT_NODE


def _state_value_to_dict(value: object) -> dict:
    """兼容 LangGraph 里混用 Pydantic 与 dict 的状态快照。"""
    if not value:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


def _death_save_pause_turn_id(state: GraphState, combat: dict, actor_id: str) -> str:
    """用轮次和行动者标识一次倒地玩家回合，避免玩家回复后重复暂停。"""
    return f"{state.get('active_combat_message_start', 'detached')}:{combat.get('round', 0)}:{actor_id}"


def _needs_player_death_save_pause(state: GraphState) -> bool:
    """玩家倒地回合先停在图节点，把掷死亡豁免前的发言权交还给玩家。"""
    if not _is_combat_active(state):
        return False

    combat = _state_value_to_dict(state.get("combat"))
    player = _state_value_to_dict(state.get("player"))
    actor_id = combat.get("current_actor_id")
    if not actor_id or actor_id != player.get("id"):
        return False
    if player.get("hp", 0) > 0 or player.get("is_stable") or player.get("is_dead"):
        return False

    pause_turn_id = _death_save_pause_turn_id(state, combat, actor_id)
    return state.get(STATE_DEATH_SAVE_PAUSE_TURN_KEY) != pause_turn_id


def _is_combat_active(state: GraphState) -> bool:
    return state.get("phase") == "combat" and state.get("combat") is not None


def route_from_router(state: GraphState) -> str:
    if state.get("pending_combat_executor"):
        return COMBAT_EXECUTOR_NODE

    if state.get("pending_combat_start"):
        return COMBAT_START_NODE

    if state.get("pending_combat_end"):
        return COMBAT_END_NODE

    if state.get("pending_reaction"):
        if state.get("reaction_choice") is not None:
            return REACTION_RESOLUTION_NODE
        return END_NODE

    if _needs_player_death_save_pause(state):
        return DEATH_SAVE_PAUSE_NODE

    messages = state.get("messages", [])
    if not messages:
        return END_NODE
    return _assistant_node_for_phase(state)


def route_from_assistant(state: GraphState) -> str:
    return _route_after_assistant_message(state)


def route_from_combat_assistant(state: GraphState) -> str:
    return _route_after_assistant_message(state)


def _route_after_assistant_message(state: GraphState) -> str:
    if state.get("pending_combat_executor"):
        return COMBAT_EXECUTOR_NODE

    if state.get("pending_combat_start"):
        return COMBAT_START_NODE

    if state.get("pending_combat_end"):
        return COMBAT_END_NODE

    messages = state.get("messages", [])
    if not messages:
        return END_NODE

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return TOOL_NODE

    return END_NODE


def _is_monster_turn(state: GraphState) -> bool:
    """检查当前行动者是否为非玩家方"""
    combat = state.get("combat")
    if not combat:
        return False
    combat_dict = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat)
    current_id = combat_dict.get("current_actor_id", "")

    # 玩家不在 participants 中，通过 player.id 判断
    player = state.get("player")
    if player:
        pd = player.model_dump() if hasattr(player, "model_dump") else dict(player)
        if pd.get("id") == current_id:
            return False

    # 查 NPC participants
    participants = combat_dict.get("participants", {})
    actor = participants.get(current_id, {})
    return actor.get("hp", 0) > 0


def route_from_tool(state: GraphState) -> str:
    """工具执行后：战斗态统一回 combat assistant，待决反应除外。"""
    if state.get("pending_combat_executor"):
        return COMBAT_EXECUTOR_NODE
    if state.get("pending_combat_start"):
        return COMBAT_START_NODE
    if state.get("pending_combat_end"):
        return COMBAT_END_NODE
    if state.get("pending_reaction"):
        return END_NODE
    if _is_combat_active(state):
        return COMBAT_RESOLUTION_NODE
    return _assistant_node_for_phase(state)


def route_from_combat_resolution(state: GraphState) -> str:
    """战斗后置收束：不再中断玩家倒地流程，直接回到 phase 对应 assistant。"""
    if state.get("pending_reaction"):
        return END_NODE
    if _needs_player_death_save_pause(state):
        return DEATH_SAVE_PAUSE_NODE
    return _assistant_node_for_phase(state)


def route_from_combat_start(state: GraphState) -> str:
    """开战计划执行后按 phase 回到对应代理；失败则继续探索态处理。"""
    return _assistant_node_for_phase(state)


def route_from_combat_end(state: GraphState) -> str:
    """结束战斗 workflow 后按新 phase 回到对应代理。"""
    return _assistant_node_for_phase(state)


def route_from_combat_executor(state: GraphState) -> str:
    """执行器完成后进入战斗后置节点，让反应/死亡豁免等流程继续接管。"""
    if state.get("pending_reaction"):
        return END_NODE
    if _is_combat_active(state):
        return COMBAT_RESOLUTION_NODE
    return _assistant_node_for_phase(state)


def route_from_reaction_resolution(state: GraphState) -> str:
    """反应解析后：若仍存在待决反应则暂停，否则统一交回 phase 对应 assistant。"""
    if state.get("pending_reaction"):
        return END_NODE
    if _is_combat_active(state):
        return COMBAT_RESOLUTION_NODE
    return _assistant_node_for_phase(state)


ROUTE_OPTIONS = {
    ASSISTANT_NODE: ASSISTANT_NODE,
    COMBAT_ASSISTANT_NODE: COMBAT_ASSISTANT_NODE,
    COMBAT_END_NODE: COMBAT_END_NODE,
    COMBAT_EXECUTOR_NODE: COMBAT_EXECUTOR_NODE,
    COMBAT_START_NODE: COMBAT_START_NODE,
    COMBAT_RESOLUTION_NODE: COMBAT_RESOLUTION_NODE,
    DEATH_SAVE_PAUSE_NODE: DEATH_SAVE_PAUSE_NODE,
    TOOL_NODE: TOOL_NODE,
    REACTION_RESOLUTION_NODE: REACTION_RESOLUTION_NODE,
    END_NODE: END_NODE,
}


ROUTER_NODE_NAME = ROUTER_NODE
