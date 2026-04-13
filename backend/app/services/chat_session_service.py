"""协调图流转与持久化执行的服务层。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any, Optional
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from app.config.settings import settings
from app.graph.builder import build_graph
from app.graph.constants import ASSISTANT_NODE, MONSTER_COMBAT_NODE, TOOL_NODE
from app.memory.checkpointer import close_checkpointer, get_checkpointer


_CHAT_SESSION_SERVICE: ChatSessionService | None = None
_CHAT_SESSION_SERVICE_LOCK = asyncio.Lock()


class ChatSessionService:
    """借助原生 Thread-based Checkpointer 负责包装、路由以及发起 Graph 推理调用。"""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def process_turn(
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
            old_state = await self._graph.aget_state(config)
            old_msgs = old_state.values.get("messages", []) if old_state and hasattr(old_state, "values") else []
            if old_msgs and hasattr(old_msgs[-1], "id"):
                baseline_msg_id = old_msgs[-1].id
        except Exception:
            pass

        if resume_action:
            await self._graph.ainvoke(Command(resume=resume_action), config=config)
        elif message:
            await self._graph.ainvoke({"messages": [HumanMessage(content=message)]}, config=config)
        else:
            raise ValueError("Must provide either message or resume_action.")

        state = await self._graph.aget_state(config)
        
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

    # ── SSE 流式推送 ───────────────────────────────────────────

    def _sse_event(self, event_type: str, data: dict) -> str:
        """格式化单条 SSE 事件"""
        return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def process_turn_stream(
        self,
        message: Optional[str] = None,
        session_id: Optional[str] = None,
        resume_action: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """以 SSE 事件流的方式推送图执行过程中的每一步结果。"""
        current_session_id = session_id or str(uuid4())
        config = {"configurable": {"thread_id": current_session_id}}

        if resume_action:
            graph_input = Command(resume=resume_action)
        elif message:
            graph_input = {"messages": [HumanMessage(content=message)]}
        else:
            yield self._sse_event("error", {"message": "Must provide either message or resume_action."})
            return

        # 使用 astream(stream_mode="updates") 逐节点获取 state 增量
        async for chunk in self._graph.astream(graph_input, config=config, stream_mode="updates"):
            for node_name, node_output in chunk.items():
                if not isinstance(node_output, dict):
                    continue

                # 提取消息增量
                new_messages = node_output.get("messages", [])
                hp_changes = node_output.get("hp_changes", [])

                for msg in new_messages:
                    if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                        content = msg.content if isinstance(msg.content, str) else str(msg.content)
                        yield self._sse_event("assistant_message", {"content": content})

                    elif isinstance(msg, ToolMessage):
                        payload: dict = {"content": msg.content}
                        
                        # 拦截掷骰子工具投出的自然值（普通的掷骰请求在 content 格式中解析，如果是带有 artifact 的也能取）
                        if msg.name == "request_dice_roll":
                            try:
                                roll_data = json.loads(msg.content)
                                if "raw_roll" in roll_data:
                                    yield self._sse_event("dice_roll", {
                                        "raw_roll": roll_data["raw_roll"],
                                        "final_total": roll_data.get("final_total", roll_data["raw_roll"])
                                    })
                            except Exception:
                                pass

                        # 拦截携带 raw_roll artifact 的 ToolMessage (通常是攻击行动)
                        if hasattr(msg, "artifact") and isinstance(msg.artifact, dict) and "raw_roll" in msg.artifact:
                            yield self._sse_event("dice_roll", {
                                "raw_roll": msg.artifact["raw_roll"],
                                "final_total": msg.artifact["raw_roll"]
                            })

                        # 怪物战斗或攻击动作产生的 ToolMessage 携带 hp_changes
                        if hp_changes:
                            yield self._sse_event("combat_action", {
                                "content": msg.content,
                                "hp_changes": hp_changes,
                            })
                            hp_changes = []  # 已消费
                        else:
                            yield self._sse_event("tool_message", payload)

                    elif isinstance(msg, HumanMessage) and isinstance(msg.content, str) and msg.content.startswith("[系统:"):
                        # 拦截怪物系统消息中的掷骰
                        if hasattr(msg, "artifact") and isinstance(msg.artifact, dict) and "raw_roll" in msg.artifact:
                            yield self._sse_event("dice_roll", {
                                "raw_roll": msg.artifact["raw_roll"],
                                "final_total": msg.artifact["raw_roll"]
                            })
                            
                        # 怪物行动的系统消息
                        yield self._sse_event("combat_action", {
                            "content": msg.content,
                            "hp_changes": hp_changes,
                        })
                        hp_changes = []

                # 若有未消费的 hp_changes（如怪物行动节点），独立发送
                if hp_changes:
                    yield self._sse_event("combat_action", {
                        "content": "",
                        "hp_changes": hp_changes,
                    })

        # 流结束后：获取最终状态，发送 state_update + pending_action + done
        state = await self._graph.aget_state(config)

        player_data = None
        combat_data = None
        scene_units_data = None
        dead_units_data = None
        if hasattr(state, "values"):
            player = state.values.get("player")
            if player:
                player_data = player.model_dump() if hasattr(player, "model_dump") else dict(player)
            combat = state.values.get("combat")
            if combat:
                combat_data = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat)
            scene_units = state.values.get("scene_units")
            if scene_units:
                scene_units_data = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()} if hasattr(scene_units, "items") else scene_units
            dead_units = state.values.get("dead_units")
            if dead_units:
                dead_units_data = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in dead_units.items()} if hasattr(dead_units, "items") else dead_units

        yield self._sse_event("state_update", {
            "player": player_data,
            "combat": combat_data,
            "scene_units": scene_units_data,
            "dead_units": dead_units_data,
        })

        pending = self._get_pending_action(state)
        if pending:
            yield self._sse_event("pending_action", pending)

        yield self._sse_event("done", {"session_id": current_session_id})

    # ── 历史消息恢复 ──────────────────────────────────────────

    async def get_history(self, session_id: str, limit: int = 10) -> dict[str, Any]:
        """从 checkpointer 中恢复最近的对话消息，供前端初始化。"""
        config = {"configurable": {"thread_id": session_id}}
        try:
            state = await self._graph.aget_state(config)
        except Exception:
            return {"messages": [], "player": None, "combat": None}

        all_messages = state.values.get("messages", []) if hasattr(state, "values") else []

        # 倒序提取 AIMessage 和 HumanMessage（跳过 ToolMessage/系统消息）
        history: list[dict] = []
        for msg in reversed(all_messages):
            if len(history) >= limit:
                break
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                history.append({"role": "assistant", "content": content})
            elif isinstance(msg, HumanMessage) and not str(msg.content).startswith("[系统"):
                history.append({"role": "user", "content": msg.content})

        history.reverse()

        player_data = None
        combat_data = None
        if hasattr(state, "values"):
            player = state.values.get("player")
            if player:
                player_data = player.model_dump() if hasattr(player, "model_dump") else dict(player)
            combat = state.values.get("combat")
            if combat:
                combat_data = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat)

        return {"messages": history, "player": player_data, "combat": combat_data}


async def get_chat_session_service() -> ChatSessionService:
    """在首个请求到达时初始化图与异步 checkpointer。"""
    global _CHAT_SESSION_SERVICE

    if _CHAT_SESSION_SERVICE is not None:
        return _CHAT_SESSION_SERVICE

    async with _CHAT_SESSION_SERVICE_LOCK:
        if _CHAT_SESSION_SERVICE is not None:
            return _CHAT_SESSION_SERVICE

        graph = build_graph(checkpointer=await get_checkpointer(settings.memory_db_path))
        _CHAT_SESSION_SERVICE = ChatSessionService(graph=graph)
        return _CHAT_SESSION_SERVICE


async def close_chat_session_service() -> None:
    """关闭持久化资源并清理 service 单例。"""
    global _CHAT_SESSION_SERVICE

    _CHAT_SESSION_SERVICE = None
    await close_checkpointer()
