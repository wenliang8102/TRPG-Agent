"""Conditional routes and edge rules."""

from langchain_core.messages import AIMessage

from app.graph.constants import (
    ASSISTANT_NODE,
    END_NODE,
    ROUTER_NODE,
    TOOL_NODE,
    SUMMARIZE_NODE,
    MONSTER_COMBAT_NODE,
    REACTION_RESOLUTION_NODE,
)
from app.graph.state import GraphState


def route_from_router(state: GraphState) -> str:
    if state.get("pending_reaction"):
        if state.get("reaction_choice") is not None:
            return REACTION_RESOLUTION_NODE
        return END_NODE

    messages = state.get("messages", [])
    if not messages:
        return END_NODE
    return ASSISTANT_NODE


def route_from_assistant(state: GraphState) -> str:
    messages = state.get("messages", [])
    if not messages:
        return END_NODE

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return TOOL_NODE

    # [修改]：如果本轮后确认无需执行工具指令，检查上下文，判断是否由于消息过多需触发截断与大纲总结。
    # 放大触发阈值。TRPG 需要保留相对充沛的短期精细记忆。
    # 阈值设定为 > 40 条，即大约允许滑动窗口在 20~40 条之间移动。
    if len(messages) > 40:
        return SUMMARIZE_NODE
        
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
    """工具执行后：若当前轮到怪物行动 → 进入自动战斗节点；否则回 LLM"""
    if _is_monster_turn(state):
        return MONSTER_COMBAT_NODE
    return ASSISTANT_NODE


def route_from_monster_combat(state: GraphState) -> str:
    """怪物单步执行后：下一个仍是怪物 → 继续循环；轮到玩家 → 回 LLM 叙述"""
    if state.get("pending_reaction"):
        return END_NODE
    if _is_monster_turn(state):
        return MONSTER_COMBAT_NODE
    return ASSISTANT_NODE


def route_from_reaction_resolution(state: GraphState) -> str:
    """反应解析后：若仍存在待决反应则暂停，否则按当前行动者继续路由"""
    if state.get("pending_reaction"):
        return END_NODE
    if _is_monster_turn(state):
        return MONSTER_COMBAT_NODE
    return ASSISTANT_NODE


ROUTE_OPTIONS = {
    ASSISTANT_NODE: ASSISTANT_NODE,
    TOOL_NODE: TOOL_NODE,
    MONSTER_COMBAT_NODE: MONSTER_COMBAT_NODE,
    REACTION_RESOLUTION_NODE: REACTION_RESOLUTION_NODE,
    END_NODE: END_NODE,
}


ROUTER_NODE_NAME = ROUTER_NODE
