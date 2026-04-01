"""Conditional routes and edge rules."""

from app.graph.constants import END_NODE, EXECUTOR_NODE, PLANNER_NODE, ROUTER_NODE, TOOL_NODE
from app.graph.state import GraphState


def route_from_router(state: GraphState) -> str:
    user_input = str(state.get("user_input", "")).strip()
    if not user_input:
        return END_NODE
    return PLANNER_NODE


def route_from_planner(state: GraphState) -> str:
    return EXECUTOR_NODE


def route_from_executor(state: GraphState) -> str:
    return END_NODE


def route_from_tool(state: GraphState) -> str:
    return EXECUTOR_NODE


ROUTE_OPTIONS = {
    PLANNER_NODE: PLANNER_NODE,
    EXECUTOR_NODE: EXECUTOR_NODE,
    TOOL_NODE: TOOL_NODE,
    END_NODE: END_NODE,
}


ROUTER_NODE_NAME = ROUTER_NODE

