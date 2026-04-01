"""Conditional routes and edge rules."""

from app.graph.constants import END_NODE, EXECUTOR_NODE, PLANNER_NODE, ROUTER_NODE, TOOL_NODE
from app.graph.state import GraphState


def route_from_router(state: GraphState) -> str:
    return state.get("next_node") or PLANNER_NODE


def route_from_planner(state: GraphState) -> str:
    return state.get("next_node") or EXECUTOR_NODE


def route_from_executor(state: GraphState) -> str:
    return state.get("next_node") or END_NODE


def route_from_tool(state: GraphState) -> str:
    return state.get("next_node") or EXECUTOR_NODE


ROUTE_OPTIONS = {
    PLANNER_NODE: PLANNER_NODE,
    EXECUTOR_NODE: EXECUTOR_NODE,
    TOOL_NODE: TOOL_NODE,
    END_NODE: END_NODE,
}


ROUTER_NODE_NAME = ROUTER_NODE

