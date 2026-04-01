"""Build and compile the StateGraph."""

from langgraph.graph import END, START, StateGraph

from app.graph import edges, nodes
from app.graph.constants import EXECUTOR_NODE, PLANNER_NODE, ROUTER_NODE, TOOL_NODE
from app.graph.state import GraphState


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node(ROUTER_NODE, nodes.router_node)
    graph.add_node(PLANNER_NODE, nodes.planner_node)
    graph.add_node(EXECUTOR_NODE, nodes.executor_node)
    graph.add_node(TOOL_NODE, nodes.tool_node)

    graph.add_edge(START, ROUTER_NODE)

    graph.add_conditional_edges(
        ROUTER_NODE,
        edges.route_from_router,
        {
            PLANNER_NODE: PLANNER_NODE,
            TOOL_NODE: TOOL_NODE,
            EXECUTOR_NODE: EXECUTOR_NODE,
            "end": END,
        },
    )

    graph.add_conditional_edges(
        PLANNER_NODE,
        edges.route_from_planner,
        {
            EXECUTOR_NODE: EXECUTOR_NODE,
            "end": END,
        },
    )

    graph.add_conditional_edges(
        TOOL_NODE,
        edges.route_from_tool,
        {
            EXECUTOR_NODE: EXECUTOR_NODE,
            "end": END,
        },
    )

    graph.add_conditional_edges(
        EXECUTOR_NODE,
        edges.route_from_executor,
        {
            "end": END,
        },
    )

    return graph.compile()

