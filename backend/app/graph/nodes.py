"""Graph node function implementations."""

import json
from functools import lru_cache

from langchain_core.messages import AIMessage

from app.graph.state import GraphState
from app.services.llm_service import LLMService
from app.services.tool_service import get_tools

ASSISTANT_SYSTEM_PROMPT = (
    "You are a helpful TRPG assistant. "
    "Use tools when external facts are needed, otherwise answer directly."
)


@lru_cache(maxsize=1)
def _get_llm_service() -> LLMService:
    """使用 lru_cache 实现单例模式，获取大语言模型服务"""
    return LLMService()


def router_node(state: GraphState) -> GraphState:
    return {**state}


def assistant_node(state: GraphState) -> GraphState:
    messages = state.get("messages", [])
    
    # 动态组装附加了上下文的 System Prompt
    system_prompt = ASSISTANT_SYSTEM_PROMPT
    if player := state.get("player"):
        player_context = json.dumps(player, ensure_ascii=False, indent=2)
        system_prompt += f"\n\n[当前玩家状态]\n{player_context}"
    else:
        system_prompt += "\n\n[当前玩家状态]\n玩家尚未加载或创建角色卡。"

    response = _get_llm_service().invoke_with_tools(
        messages=messages,
        tools=get_tools(),
        system_prompt=system_prompt,
    )

    output = response.content if isinstance(response.content, str) and not response.tool_calls else ""
    return {
        **state,
        "messages": [response],
        "output": output,
    }
