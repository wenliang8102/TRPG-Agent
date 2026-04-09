"""协调图流转与持久化执行的服务层。"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from app.config.settings import settings
from app.graph.builder import build_graph
from app.memory.checkpointer import get_checkpointer


class ChatSessionService:
    """借助原生 Thread-based Checkpointer 负责包装、路由以及发起 Graph 推理调用。"""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    def process_turn(
        self,
        message: Optional[str] = None,
        session_id: Optional[str] = None,
        resume_action: Optional[str] = None,
    ) -> dict[str, Any]:
        current_session_id = session_id or str(uuid4())
        config = {"configurable": {"thread_id": current_session_id}}

        # 在图运行前，记录当前最后一条消息的 ID 作为界标（baseline）。
        # 该界标是最安全的锚点，因为后续即使发生了压缩，也只会清除更古老的历史，不会清除本界标。
        baseline_msg_id = None
        try:
            old_state = self._graph.get_state(config)
            old_msgs = old_state.values.get("messages", []) if old_state and hasattr(old_state, "values") else []
            if old_msgs and hasattr(old_msgs[-1], "id"):
                baseline_msg_id = old_msgs[-1].id
        except Exception:
            pass

        if resume_action:
            self._graph.invoke(Command(resume=resume_action), config=config)
        elif message:
            self._graph.invoke({"messages": [HumanMessage(content=message)]}, config=config)
        else:
            raise ValueError("Must provide either message or resume_action.")

        state = self._graph.get_state(config)
        
        player_data = None
        combat_data = None
        if hasattr(state, "values"):
            player = state.values.get("player")
            if player:
                player_data = player.model_dump() if hasattr(player, "model_dump") else dict(player)
            combat = state.values.get("combat")
            if combat:
                combat_data = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat)

        return {
            "reply": self._extract_new_reply(state, baseline_msg_id),
            "plan": None,
            "session_id": current_session_id,
            "pending_action": self._get_pending_action(state),
            "player": player_data,
            "combat": combat_data,
        }

    def _get_pending_action(self, state: Any) -> Optional[dict]:
        """抓取由于交互工具而被主流程暂挂（Interrupt）的行为等待标记"""
        if state.tasks and state.tasks[0].interrupts:
            return state.tasks[0].interrupts[0].value
        return None

    def _extract_new_reply(self, state: Any, baseline_msg_id: str | None) -> str:
        """根据 invoke 开始前预存的界标 ID（baseline_msg_id），提取本回合产生的新 AI 回复。
        因为即使对话产生压缩，旧消息的切除也不会波及到前一回合的结尾，这样就能避免：
        1. 由于压缩导致使用 index 下标定位截断数组出错的情况。
        2. 工具调用时生成的空 AI 消息因纯倒序查询而被跳过、导致复读老旧历史的问题。"""
        all_messages = state.values.get("messages", [])

        # 定位图结构运行前的那条界线：
        start_idx = 0
        if baseline_msg_id:
            for i, msg in enumerate(all_messages):
                if getattr(msg, "id", None) == baseline_msg_id:
                    # 我们要提取的是这条界标「之后」产生的所有新消息
                    start_idx = i + 1
                    break

        new_messages = all_messages[start_idx:]

        # 遍历所有的新的 AI Message，组装内容：
        reply_parts: list[str] = []
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
