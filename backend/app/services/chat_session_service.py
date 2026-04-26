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
from app.memory.checkpointer import close_checkpointer, get_checkpointer
from app.memory.episodic_store import EpisodicStore
from app.memory.ingestion import MemoryIngestionPipeline
from app.utils.agent_trace import trace_chat_error, trace_chat_request, trace_chat_result
from app.utils.logger import logger


_CHAT_SESSION_SERVICE: ChatSessionService | None = None
_CHAT_SESSION_SERVICE_LOCK = asyncio.Lock()


class ChatSessionService:
    """借助原生 Thread-based Checkpointer 负责包装、路由以及发起 Graph 推理调用。"""

    def __init__(
        self,
        graph: Any,
        memory_pipeline: MemoryIngestionPipeline | None = None,
        episodic_store: EpisodicStore | None = None,
    ) -> None:
        self._graph = graph
        self._memory_pipeline = memory_pipeline
        self._episodic_store = episodic_store
        self._memory_tasks: dict[str, asyncio.Task[None]] = {}

    async def process_turn(
        self,
        message: Optional[str] = None,
        session_id: Optional[str] = None,
        resume_action: Optional[str] = None,
        reaction_response: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        current_session_id = session_id or str(uuid4())
        config = {"configurable": {"thread_id": current_session_id}}
        old_state = None
        old_snapshot: dict[str, Any] = {}

        # 在图运行前，记录当前最后一条消息的 ID 作为界标（baseline）。
        # 该界标是最安全的锚点，因为后续即使发生了压缩，也只会清除更古老的历史，不会清除本界标。
        baseline_msg_id = None
        try:
            old_state = await self._graph.aget_state(config)
            old_snapshot = self._snapshot_state(old_state)
            old_msgs = old_state.values.get("messages", []) if old_state and hasattr(old_state, "values") else []
            if old_msgs and hasattr(old_msgs[-1], "id"):
                baseline_msg_id = old_msgs[-1].id
        except Exception:
            pass

        pending_before_run = self._get_pending_action(old_state) if old_state else None
        trace_chat_request(
            current_session_id,
            entrypoint="sync",
            message=message,
            resume_action=resume_action,
            reaction_response=reaction_response,
            pending_before_run=pending_before_run,
        )
        if message and pending_before_run and not resume_action and reaction_response is None:
            trace_chat_error(
                current_session_id,
                entrypoint="sync",
                error="Must resolve the pending action before sending a new message.",
            )
            raise ValueError("Must resolve the pending action before sending a new message.")

        await self._apply_runtime_context(config, current_session_id)

        if reaction_response is not None:
            await self._graph.ainvoke({"reaction_choice": reaction_response}, config=config)
        elif resume_action:
            await self._graph.ainvoke(Command(resume=resume_action), config=config)
        elif message:
            await self._graph.ainvoke({"messages": [HumanMessage(content=message)]}, config=config)
        else:
            trace_chat_error(
                current_session_id,
                entrypoint="sync",
                error="Must provide either message, resume_action, or reaction_response.",
            )
            raise ValueError("Must provide either message, resume_action, or reaction_response.")

        state = await self._graph.aget_state(config)
        new_messages = self._extract_new_messages(state, baseline_msg_id)
        new_snapshot = self._snapshot_state(state)
        
        player_data = None
        combat_data = None
        if hasattr(state, "values"):
            player = state.values.get("player")
            if player:
                player_data = player.model_dump() if hasattr(player, "model_dump") else dict(player)
            combat = state.values.get("combat")
            if combat:
                combat_data = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat)

        reply = self._extract_reply_from_messages(new_messages)
        pending_action = self._get_pending_action(state)
        trace_chat_result(
            current_session_id,
            entrypoint="sync",
            reply=reply,
            pending_action=pending_action,
            new_message_count=len(new_messages),
        )
        self._schedule_memory_ingestion(
            session_id=current_session_id,
            old_snapshot=old_snapshot,
            new_snapshot=new_snapshot,
            new_messages=new_messages,
            reply=reply,
        )

        return {
            "reply": reply,
            "plan": None,
            "session_id": current_session_id,
            "pending_action": pending_action,
            "player": player_data,
            "combat": combat_data,
        }

    def _get_pending_action(self, state: Any) -> Optional[dict]:
        """抓取由于交互工具而被主流程暂挂（Interrupt）的行为等待标记"""
        if state and hasattr(state, "values"):
            pending_action = self._pending_action_from_reaction(state.values.get("pending_reaction"))
            if pending_action is not None:
                return pending_action
        tasks = getattr(state, "tasks", None)
        if tasks and tasks[0].interrupts:
            return state.tasks[0].interrupts[0].value
        return None

    def _pending_action_from_reaction(self, pending_reaction: Any) -> Optional[dict]:
        """把 pending_reaction 统一投影成前端消费的 pending_action 结构。"""
        if not pending_reaction:
            return None

        pending = pending_reaction.model_dump() if hasattr(pending_reaction, "model_dump") else dict(pending_reaction)
        attack_roll = dict(pending.get("attack_roll", {}))
        return {
            "type": "reaction_prompt",
            "trigger": pending.get("trigger", "on_hit"),
            "attacker": pending.get("attacker_name", ""),
            "attacker_id": pending.get("attacker_id", ""),
            "target": pending.get("target_name", ""),
            "target_id": pending.get("target_id", ""),
            "available_reactions": pending.get("available_reactions", []),
            "attack_roll": {
                "raw_roll": attack_roll.get("raw_roll", attack_roll.get("natural", 0)),
                "attack_bonus": attack_roll.get("attack_bonus", 0),
                "final_total": attack_roll.get("hit_total", 0),
                "hit_total": attack_roll.get("hit_total", 0),
                "target_ac": attack_roll.get("target_ac", 10),
                "attack_name": attack_roll.get("atk_name_display", ""),
            },
            "hit_roll": attack_roll.get("hit_total", 0),
            "current_ac": attack_roll.get("target_ac", 10),
        }

    def _extract_new_messages(self, state: Any, baseline_msg_id: str | None) -> list[Any]:
        """根据 invoke 前的界标抽取本轮新增消息，供回复提取和后台记忆复用。"""
        all_messages = state.values.get("messages", [])

        # 定位图结构运行前的那条界线：
        start_idx = 0
        if baseline_msg_id:
            for i, msg in enumerate(all_messages):
                if getattr(msg, "id", None) == baseline_msg_id:
                    # 我们要提取的是这条界标「之后」产生的所有新消息
                    start_idx = i + 1
                    break

        return list(all_messages[start_idx:])

    def _extract_reply_from_messages(self, new_messages: list[Any]) -> str:
        """只拼接本轮真正对用户可见的 AI 文本回复。"""
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

    def _snapshot_state(self, state: Any) -> dict[str, Any]:
        """将关键状态投影成纯 Python 结构，避免后台任务持有可变对象引用。"""
        values = state.values if state and hasattr(state, "values") else {}
        return {
            "phase": values.get("phase"),
            "conversation_summary": values.get("conversation_summary", ""),
            "player": self._state_value_to_dict(values.get("player")),
            "combat": self._state_value_to_dict(values.get("combat")),
            "scene_units": self._mapping_state_to_dict(values.get("scene_units")),
            "dead_units": self._mapping_state_to_dict(values.get("dead_units")),
            "pending_reaction": self._state_value_to_dict(values.get("pending_reaction")),
        }

    def _mapping_state_to_dict(self, value: Any) -> dict[str, Any]:
        if not value or not hasattr(value, "items"):
            return {}
        return {
            key: self._state_value_to_dict(item)
            for key, item in value.items()
        }

    def _state_value_to_dict(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "items"):
            return {key: self._state_value_to_dict(item) for key, item in value.items()}
        return value

    def _schedule_memory_ingestion(
        self,
        *,
        session_id: str,
        old_snapshot: dict[str, Any],
        new_snapshot: dict[str, Any],
        new_messages: list[Any],
        reply: str,
    ) -> None:
        """把记忆摄取移到后台串行执行，避免阻塞当前回合响应。"""
        if self._memory_pipeline is None:
            return

        turn_id = getattr(new_messages[-1], "id", None) or f"turn:{uuid4()}"
        previous_task = self._memory_tasks.get(session_id)

        async def _run_ingestion() -> None:
            if previous_task is not None:
                try:
                    await previous_task
                except Exception:
                    logger.exception(f"Previous memory ingestion failed for session {session_id}")

            await self._memory_pipeline.ingest(
                session_id=session_id,
                turn_id=str(turn_id),
                old_state=old_snapshot,
                new_state=new_snapshot,
                new_messages=list(new_messages),
                reply=reply,
            )

        task = asyncio.create_task(_run_ingestion(), name=f"memory-ingest:{session_id}")
        self._memory_tasks[session_id] = task

        def _cleanup(done_task: asyncio.Task[None]) -> None:
            try:
                done_task.result()
            except Exception:
                logger.exception(f"Memory ingestion failed for session {session_id}")

            if self._memory_tasks.get(session_id) is done_task:
                self._memory_tasks.pop(session_id, None)

        task.add_done_callback(_cleanup)

    async def _apply_runtime_context(self, config: dict[str, Any], session_id: str) -> None:
        """在图执行前注入派生上下文，统一覆盖普通消息、resume 与 reaction 三种入口。"""
        if not hasattr(self._graph, "aupdate_state"):
            return

        episodic_context = await self._load_episodic_context(session_id)
        await self._graph.aupdate_state(
            config,
            {
                "session_id": session_id,
                "episodic_context": episodic_context,
            },
        )

    async def _load_episodic_context(self, session_id: str) -> list[str]:
        """读取最近的回合摘要，作为热路径的长期情节记忆输入。"""
        if self._episodic_store is None:
            return []

        if hasattr(self._episodic_store, "fetch_recent_context_blocks"):
            context_blocks = await self._episodic_store.fetch_recent_context_blocks(session_id)
            return [block[:300] for block in context_blocks if block]

        summaries = await self._episodic_store.fetch_recent_summaries(session_id, limit=4)
        return [summary[:300] for summary in summaries if summary]

    async def aclose(self) -> None:
        """关闭后台记忆资源，避免测试或热重载遗留挂起任务。"""
        tasks = list(self._memory_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._memory_tasks.clear()

        if self._memory_pipeline is not None:
            await self._memory_pipeline.close()

    # ── SSE 流式推送 ───────────────────────────────────────────

    def _extract_attack_roll_payload(self, msg: Any) -> Optional[dict[str, Any]]:
        """从消息标准字段中提取攻击命中检定载荷，兼容旧 artifact 写法。"""
        if hasattr(msg, "additional_kwargs") and isinstance(msg.additional_kwargs, dict):
            attack_roll = msg.additional_kwargs.get("attack_roll")
            if isinstance(attack_roll, dict) and attack_roll.get("emit_dice_roll") is False:
                return None
            if isinstance(attack_roll, dict) and "raw_roll" in attack_roll:
                return attack_roll

        if hasattr(msg, "artifact") and isinstance(msg.artifact, dict) and "raw_roll" in msg.artifact:
            raw_roll = msg.artifact["raw_roll"]
            return {
                "raw_roll": raw_roll,
                "final_total": msg.artifact.get("final_total", raw_roll),
                "attack_bonus": msg.artifact.get("attack_bonus", 0),
            }

        return None

    def _is_hidden_tool_message(self, msg: Any) -> bool:
        """内部 ToolMessage 仅用于满足 ToolNode 约束，不应直接透传到前端聊天流。"""
        return bool(
            hasattr(msg, "additional_kwargs")
            and isinstance(msg.additional_kwargs, dict)
            and msg.additional_kwargs.get("hidden_from_ui")
        )

    def _sse_event(self, event_type: str, data: Any) -> str:
        """格式化单条 SSE 事件"""
        return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def process_turn_stream(
        self,
        message: Optional[str] = None,
        session_id: Optional[str] = None,
        resume_action: Optional[str] = None,
        reaction_response: Optional[dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        """以 SSE 事件流的方式推送图执行过程中的每一步结果。"""
        current_session_id = session_id or str(uuid4())
        config = {"configurable": {"thread_id": current_session_id}}

        old_state = await self._graph.aget_state(config)
        old_snapshot = self._snapshot_state(old_state)
        old_messages = old_state.values.get("messages", []) if hasattr(old_state, "values") else []
        baseline_msg_id = getattr(old_messages[-1], "id", None) if old_messages else None
        pending_before_run = self._get_pending_action(old_state)
        trace_chat_request(
            current_session_id,
            entrypoint="stream",
            message=message,
            resume_action=resume_action,
            reaction_response=reaction_response,
            pending_before_run=pending_before_run,
        )
        if message and pending_before_run and not resume_action and reaction_response is None:
            trace_chat_error(
                current_session_id,
                entrypoint="stream",
                error="Must resolve the pending action before sending a new message.",
            )
            yield self._sse_event("error", {"message": "Must resolve the pending action before sending a new message."})
            return

        await self._apply_runtime_context(config, current_session_id)

        if reaction_response is not None:
            graph_input = {"reaction_choice": reaction_response}
        elif resume_action:
            graph_input = Command(resume=resume_action)
        elif message:
            graph_input = {"messages": [HumanMessage(content=message)]}
        else:
            trace_chat_error(
                current_session_id,
                entrypoint="stream",
                error="Must provide either message, resume_action, or reaction_response.",
            )
            yield self._sse_event("error", {"message": "Must provide either message, resume_action, or reaction_response."})
            return

        # 使用 astream(stream_mode="updates") 逐节点获取 state 增量
        async for chunk in self._graph.astream(graph_input, config=config, stream_mode="updates"):
            for node_name, node_output in chunk.items():
                if not isinstance(node_output, dict):
                    continue

                # reaction 解析后尽早推送 pending_action 变化，避免前端必须等整条流结束才关弹框。
                if "pending_reaction" in node_output:
                    yield self._sse_event("pending_action", self._pending_action_from_reaction(node_output.get("pending_reaction")))

                # 提取消息增量
                new_messages = node_output.get("messages", [])
                hp_changes = node_output.get("hp_changes", [])

                for msg in new_messages:
                    if isinstance(msg, AIMessage) and msg.content:
                        # 战斗流里很多给玩家看的旁白和工具调用会落在同一条 AIMessage 上，不能因为带 tool_calls 就吞掉文本。
                        content = msg.content if isinstance(msg.content, str) else str(msg.content)
                        yield self._sse_event("assistant_message", {"content": content})

                    elif isinstance(msg, ToolMessage):
                        if self._is_hidden_tool_message(msg):
                            continue

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

                        attack_roll = self._extract_attack_roll_payload(msg)
                        if attack_roll:
                            yield self._sse_event("dice_roll", {
                                "raw_roll": attack_roll["raw_roll"],
                                "final_total": attack_roll.get("final_total", attack_roll["raw_roll"]),
                                "attack_bonus": attack_roll.get("attack_bonus", 0),
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
                        attack_roll = self._extract_attack_roll_payload(msg)
                        if attack_roll:
                            yield self._sse_event("dice_roll", {
                                "raw_roll": attack_roll["raw_roll"],
                                "final_total": attack_roll.get("final_total", attack_roll["raw_roll"]),
                                "attack_bonus": attack_roll.get("attack_bonus", 0),
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
        new_messages = self._extract_new_messages(state, baseline_msg_id)
        reply = self._extract_reply_from_messages(new_messages)
        trace_chat_result(
            current_session_id,
            entrypoint="stream",
            reply=reply,
            pending_action=pending,
            new_message_count=len(new_messages),
        )
        yield self._sse_event("pending_action", pending)

        self._schedule_memory_ingestion(
            session_id=current_session_id,
            old_snapshot=old_snapshot,
            new_snapshot=self._snapshot_state(state),
            new_messages=new_messages,
            reply=reply,
        )

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
            if isinstance(msg, AIMessage) and msg.content:
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
        episodic_store = EpisodicStore(settings.memory_db_path)
        memory_pipeline = MemoryIngestionPipeline(episodic_store)
        _CHAT_SESSION_SERVICE = ChatSessionService(
            graph=graph,
            memory_pipeline=memory_pipeline,
            episodic_store=episodic_store,
        )
        return _CHAT_SESSION_SERVICE


async def close_chat_session_service() -> None:
    """关闭持久化资源并清理 service 单例。"""
    global _CHAT_SESSION_SERVICE

    if _CHAT_SESSION_SERVICE is not None:
        await _CHAT_SESSION_SERVICE.aclose()

    _CHAT_SESSION_SERVICE = None
    await close_checkpointer()
