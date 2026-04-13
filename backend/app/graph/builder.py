"""Build and compile the StateGraph."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.graph import edges, nodes
from app.graph.constants import ASSISTANT_NODE, ROUTER_NODE, TOOL_NODE, SUMMARIZE_NODE, MONSTER_COMBAT_NODE
from app.graph.state import GraphState
from app.services.tool_service import get_tools


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    graph = StateGraph(GraphState)

    graph.add_node(ROUTER_NODE, nodes.router_node)
    graph.add_node(ASSISTANT_NODE, nodes.assistant_node)
    graph.add_node(TOOL_NODE, ToolNode(get_tools()))
    graph.add_node(SUMMARIZE_NODE, nodes.summarize_conversation_node)
    graph.add_node(MONSTER_COMBAT_NODE, nodes.monster_combat_node)

    graph.add_edge(START, ROUTER_NODE)

    graph.add_conditional_edges(
        ROUTER_NODE,
        edges.route_from_router,
        {
            ASSISTANT_NODE: ASSISTANT_NODE,
            TOOL_NODE: TOOL_NODE,
            "end": END,
        },
    )

    graph.add_conditional_edges(
        ASSISTANT_NODE,
        edges.route_from_assistant,
        {
            TOOL_NODE: TOOL_NODE,
            SUMMARIZE_NODE: SUMMARIZE_NODE,
            "end": END,
        },
    )

    graph.add_conditional_edges(
        TOOL_NODE,
        edges.route_from_tool,
        {
            ASSISTANT_NODE: ASSISTANT_NODE,
            MONSTER_COMBAT_NODE: MONSTER_COMBAT_NODE,
        },
    )

    # 怪物单步执行后条件路由：下一个仍是怪物 → 自循环；玩家回合 → LLM 叙述
    graph.add_conditional_edges(
        MONSTER_COMBAT_NODE,
        edges.route_from_monster_combat,
        {
            MONSTER_COMBAT_NODE: MONSTER_COMBAT_NODE,
            ASSISTANT_NODE: ASSISTANT_NODE,
        },
    )

    # 总结完一定直接结束本回合图流转。由于状态已被精简并落库，下一轮读取时将清爽上阵。
    graph.add_edge(SUMMARIZE_NODE, END)

    return graph.compile(checkpointer=checkpointer)
