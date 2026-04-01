"""Graph node function implementations."""

from functools import lru_cache

from app.graph.constants import END_NODE, EXECUTOR_NODE, PLANNER_NODE
from app.graph.state import GraphState
from app.services.llm_service import LLMService

MAX_HISTORY_MESSAGES = 6
BASE_PLAN_TEXT = "respond to user message in plain text with context continuity"
PROMPT_TEMPLATE = "conversation history:\n{history}\n\nplan: {plan}\nuser: {user}"


@lru_cache(maxsize=1)
def _get_llm_service() -> LLMService:
    return LLMService()


def _normalize_messages(messages: object) -> list[dict]:
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, dict)]


def _build_prompt(messages: list[dict], user_input: str, plan: str) -> str:
    history_lines: list[str] = []
    for message in messages[-MAX_HISTORY_MESSAGES:]:
        role = str(message.get("role", "unknown"))
        content = str(message.get("content", ""))
        history_lines.append(f"{role}: {content}")
    history_text = "\n".join(history_lines) if history_lines else "(no history)"
    return PROMPT_TEMPLATE.format(history=history_text, plan=plan, user=user_input)


def router_node(state: GraphState) -> GraphState:
    user_input = str(state.get("user_input", "")).strip()
    if not user_input:
        return {**state, "next_node": END_NODE}
    return {**state, "next_node": PLANNER_NODE}


def planner_node(state: GraphState) -> GraphState:
    user_input = str(state.get("user_input", "")).strip()
    messages = _normalize_messages(state.get("messages"))
    plan = f"{BASE_PLAN_TEXT} (history_count={len(messages)})"
    if user_input:
        plan = f"{plan}; current_input={user_input}"
    return {**state, "messages": messages, "plan": plan, "next_node": EXECUTOR_NODE}


def executor_node(state: GraphState) -> GraphState:
    user_input = str(state.get("user_input", "")).strip()
    messages = _normalize_messages(state.get("messages"))
    plan = str(state.get("plan", "")).strip()
    prompt = _build_prompt(messages, user_input, plan)
    reply = _get_llm_service().generate(prompt)

    updated_messages = [
        *messages,
        {"role": "user", "content": user_input},
        {"role": "assistant", "content": reply},
    ]
    return {
        **state,
        "messages": updated_messages,
        "output": reply,
        "next_node": END_NODE,
    }


def tool_node(state: GraphState) -> GraphState:
    return {**state, "next_node": EXECUTOR_NODE}
