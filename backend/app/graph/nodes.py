"""Graph node function implementations."""

from app.graph.state import GraphState


def router_node(state: GraphState) -> GraphState:
    return {**state, "next_node": "planner"}


def planner_node(state: GraphState) -> GraphState:
    user_input = state.get("user_input", "")
    return {**state, "plan": f"placeholder plan for: {user_input}", "next_node": "executor"}


def executor_node(state: GraphState) -> GraphState:
    plan = state.get("plan", "")
    return {**state, "output": f"placeholder result based on [{plan}]", "next_node": "end"}


def tool_node(state: GraphState) -> GraphState:
    return {**state, "next_node": "executor"}

