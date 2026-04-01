"""Graph node function implementations."""

from functools import lru_cache

from app.graph.state import GraphState
from app.services.llm_service import LLMService

# 限制历史消息上下文长度
MAX_HISTORY_MESSAGES = 6
BASE_PLAN_TEXT = "respond to user message in plain text with context continuity"
# 构建 LLM 提示词的模板
PROMPT_TEMPLATE = "conversation history:\n{history}\n\nplan: {plan}\nuser: {user}"


@lru_cache(maxsize=1)
def _get_llm_service() -> LLMService:
    """使用 lru_cache 实现单例模式，获取大语言模型服务"""
    return LLMService()


def _normalize_messages(messages: object) -> list[dict]:
    """确保传入的消息历史是一个字典列表，过滤掉格式不正确的数据"""
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, dict)]


def _build_prompt(messages: list[dict], user_input: str, plan: str) -> str:
    """截取最近的 MAX_HISTORY_MESSAGES 条对话历史，并将其组装成最终的提示词"""
    history_lines: list[str] = []
    for message in messages[-MAX_HISTORY_MESSAGES:]:
        role = str(message.get("role", "unknown"))
        content = str(message.get("content", ""))
        history_lines.append(f"{role}: {content}")
    history_text = "\n".join(history_lines) if history_lines else "(no history)"
    return PROMPT_TEMPLATE.format(history=history_text, plan=plan, user=user_input)


def router_node(state: GraphState) -> GraphState:
    """
    路由节点：仅做状态透传，不承担流程分流职责。
    具体下一跳由 edges.route_from_router 决定。
    """
    return {**state}


def planner_node(state: GraphState) -> GraphState:
    """
    计划节点：根据当前状态和历史消息，生成处理计划。
    节点只负责生成计划，不负责声明下一跳，由边规则决定流转。
    """
    user_input = str(state.get("user_input", "")).strip()
    messages = _normalize_messages(state.get("messages"))
    plan = f"{BASE_PLAN_TEXT} (history_count={len(messages)})"
    if user_input:
        plan = f"{plan}; current_input={user_input}"
    return {**state, "messages": messages, "plan": plan}


def executor_node(state: GraphState) -> GraphState:
    """
    执行节点：调用大模型生成回复。
    组装提示词 -> 调用 LLM -> 更新对话历史。
    """
    user_input = str(state.get("user_input", "")).strip()
    messages = _normalize_messages(state.get("messages"))
    plan = str(state.get("plan", "")).strip()

    # 1. 组装发给大模型的提示词
    prompt = _build_prompt(messages, user_input, plan)

    # 2. 调用 LLM 生成回复
    reply = _get_llm_service().generate(prompt)

    # 3. 将新的用户输入和 AI 回复追加到历史消息中
    updated_messages = [
        *messages,
        {"role": "user", "content": user_input},
        {"role": "assistant", "content": reply},
    ]

    # 4. 更新全局状态，准备结束
    return {
        **state,
        "messages": updated_messages,
        "output": reply,
    }


def tool_node(state: GraphState) -> GraphState:
    """
    工具节点：处理外部工具调用。
    处理完成后由边规则决定下一跳（默认回到执行节点）。
    """
    return {**state}