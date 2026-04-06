"""Chat session orchestration service for graph execution and persistence."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from app.config.settings import settings
from app.graph.builder import build_graph
from app.memory.checkpointer import get_checkpointer
from app.memory.memory import EnhancedMemory


class ChatSessionService:
    """Coordinates graph invocation with official thread-based checkpointing."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self._memory = EnhancedMemory()  # 添加增强记忆功能

    def process_turn(
        self,
        message: Optional[str] = None,
        session_id: Optional[str] = None,
        resume_action: Optional[str] = None,
    ) -> dict[str, Any]:
        current_session_id = session_id or str(uuid4())
        config = {"configurable": {"thread_id": current_session_id}}

        # 1. 记录切片基准
        num_msgs_before = self._get_message_count(config)

        # 2. 执行图流转
        if resume_action:
            self._graph.invoke(Command(resume=resume_action), config=config)
        elif message:
            self._graph.invoke({"messages": [HumanMessage(content=message)]}, config=config)
        else:
            raise ValueError("Must provide either message or resume_action.")

        # 3. 提取执行结果
        state = self._graph.get_state(config)
        
        # 应用简单记忆压缩（如果消息太多）
        messages = state.values.get("messages", [])
        if len(messages) > self._memory.compression_threshold:
            processed_messages = self._memory.process_messages(messages)
            # 更新状态中的消息
            state.values["messages"] = processed_messages
            messages = processed_messages

        # 查找最新的完整 AI 回复文本
        reply = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and not msg.tool_calls:
                reply = msg.content if isinstance(msg.content, str) else ""
                break

        return {
            "reply": self._extract_new_reply(state, num_msgs_before),
            "plan": None,
            "session_id": current_session_id,
            "pending_action": self._get_pending_action(state),
        }

    def _get_message_count(self, config: dict) -> int:
        """获取当前历史消息数量作为增量解析的基准。"""
        try:
            state = self._graph.get_state(config)
            return len(state.values.get("messages", [])) if state and hasattr(state, "values") else 0
        except Exception:
            return 0

    def _get_pending_action(self, state: Any) -> Optional[dict]:
        """从图状态中提取因 interrupt 挂起的交互动作。"""
        if state.tasks and state.tasks[0].interrupts:
            return state.tasks[0].interrupts[0].value
        return None

    def _extract_new_reply(self, state: Any, num_msgs_before: int) -> str:
        """提取本次执行中新产生的 AI 纯文本回复。"""
        all_messages = state.values.get("messages", [])
        new_messages = all_messages[num_msgs_before:]
        
        reply_parts = []
        for msg in new_messages:
            if isinstance(msg, AIMessage) and msg.content:
                if isinstance(msg.content, str):
                    reply_parts.append(msg.content)
                elif isinstance(msg.content, list):
                    for part in msg.content:
                        if isinstance(part, str):
                            reply_parts.append(part)
                        elif isinstance(part, dict) and "text" in part:
                            reply_parts.append(part["text"])
                            
        return "\n\n".join(reply_parts).strip()


@lru_cache(maxsize=1)
def get_chat_session_service() -> ChatSessionService:
    graph = build_graph(checkpointer=get_checkpointer(settings.memory_db_path))
    return ChatSessionService(graph=graph)
