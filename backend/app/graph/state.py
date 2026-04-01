"""State definitions for LangGraph."""

from typing import Optional, TypedDict


class GraphState(TypedDict, total=False):
    """Shared state flowing through the graph."""

    messages: list[dict]
    user_input: str
    plan: str
    output: str
    next_node: Optional[str]

