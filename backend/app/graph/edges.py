"""Conditional routes and edge rules."""

from langchain_core.messages import AIMessage

from app.graph.constants import (
    ASSISTANT_NODE,
    COMBAT_ASSISTANT_NODE,
    COMBAT_RESOLUTION_NODE,
    END_NODE,
    ROUTER_NODE,
    TOOL_NODE,
    REACTION_RESOLUTION_NODE,
)
from app.graph.state import GraphState


def _assistant_node_for_phase(state: GraphState) -> str:
    """根据当前 phase 选择对话代理节点。"""
    if state.get("phase") == "combat" and state.get("combat"):
        return COMBAT_ASSISTANT_NODE
    return ASSISTANT_NODE


def _is_combat_active(state: GraphState) -> bool:
    return state.get("phase") == "combat" and state.get("combat") is not None


def route_from_router(state: GraphState) -> str:
    if state.get("pending_reaction"):
        if state.get("reaction_choice") is not None:
            return REACTION_RESOLUTION_NODE
        return END_NODE

    messages = state.get("messages", [])
    if not messages:
        return END_NODE
    return _assistant_node_for_phase(state)


def route_from_assistant(state: GraphState) -> str:
    return _route_after_assistant_message(state)


def route_from_combat_assistant(state: GraphState) -> str:
    return _route_after_assistant_message(state)


def _route_after_assistant_message(state: GraphState) -> str:
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
    if state.get("pending_reaction"):
        return END_NODE
    if _is_combat_active(state):
        return COMBAT_RESOLUTION_NODE
    return _assistant_node_for_phase(state)


def route_from_combat_resolution(state: GraphState) -> str:
    """战斗后置收束：团灭/恢复在此完成，其余情况回到 phase 对应 assistant。"""
    if state.get("pending_reaction"):
        return END_NODE
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
    COMBAT_RESOLUTION_NODE: COMBAT_RESOLUTION_NODE,
    TOOL_NODE: TOOL_NODE,
    REACTION_RESOLUTION_NODE: REACTION_RESOLUTION_NODE,
    END_NODE: END_NODE,
}


ROUTER_NODE_NAME = ROUTER_NODE
