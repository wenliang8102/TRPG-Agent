"""Build and compile the StateGraph."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.graph import edges, nodes
from app.graph.constants import (
    ASSISTANT_NODE,
    COMBAT_ASSISTANT_NODE,
    COMBAT_RESOLUTION_NODE,
    ROUTER_NODE,
    TOOL_NODE,
    REACTION_RESOLUTION_NODE,
)
from app.graph.state import GraphState
from app.services.tool_service import get_tools


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    graph = StateGraph(GraphState)

    graph.add_node(ROUTER_NODE, nodes.router_node)
    graph.add_node(ASSISTANT_NODE, nodes.assistant_node)
    graph.add_node(COMBAT_ASSISTANT_NODE, nodes.combat_assistant_node)
    graph.add_node(TOOL_NODE, ToolNode(get_tools()))
    graph.add_node(COMBAT_RESOLUTION_NODE, nodes.combat_resolution_node)
    graph.add_node(REACTION_RESOLUTION_NODE, nodes.resolve_reaction_node)

    graph.add_edge(START, ROUTER_NODE)

    graph.add_conditional_edges(
        ROUTER_NODE,
        edges.route_from_router,
        {
            ASSISTANT_NODE: ASSISTANT_NODE,
            COMBAT_ASSISTANT_NODE: COMBAT_ASSISTANT_NODE,
            TOOL_NODE: TOOL_NODE,
            REACTION_RESOLUTION_NODE: REACTION_RESOLUTION_NODE,
            "end": END,
        },
    )

    graph.add_conditional_edges(
        ASSISTANT_NODE,
        edges.route_from_assistant,
        {
            TOOL_NODE: TOOL_NODE,
            "end": END,
        },
    )

    graph.add_conditional_edges(
        COMBAT_ASSISTANT_NODE,
        edges.route_from_combat_assistant,
        {
            TOOL_NODE: TOOL_NODE,
            "end": END,
        },
    )

    graph.add_conditional_edges(
        TOOL_NODE,
        edges.route_from_tool,
        {
            ASSISTANT_NODE: ASSISTANT_NODE,
            COMBAT_ASSISTANT_NODE: COMBAT_ASSISTANT_NODE,
            COMBAT_RESOLUTION_NODE: COMBAT_RESOLUTION_NODE,
            "end": END,
        },
    )

    graph.add_conditional_edges(
        COMBAT_RESOLUTION_NODE,
        edges.route_from_combat_resolution,
        {
            ASSISTANT_NODE: ASSISTANT_NODE,
            COMBAT_ASSISTANT_NODE: COMBAT_ASSISTANT_NODE,
            "end": END,
        },
    )

    graph.add_conditional_edges(
        REACTION_RESOLUTION_NODE,
        edges.route_from_reaction_resolution,
        {
            COMBAT_RESOLUTION_NODE: COMBAT_RESOLUTION_NODE,
            ASSISTANT_NODE: ASSISTANT_NODE,
            COMBAT_ASSISTANT_NODE: COMBAT_ASSISTANT_NODE,
            "end": END,
        },
    )

    return graph.compile(checkpointer=checkpointer)
