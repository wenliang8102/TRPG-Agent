"""协调图流转与持久化执行的服务层。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any, Optional
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from app.adventures.models import AdventureState
from app.adventures.navigation import normalize_adventure_state
from app.adventures.runtime import (
    AdventureDirector,
    LLMAdventureDirector,
    AdventureRuntimeUpdate,
    adjudicate_and_apply_adventure_progress,
    adjudicate_and_apply_pre_turn_adventure_progress,
    build_adventure_node_frame_message,
)
from app.config.settings import settings
from app.graph.builder import build_graph
from app.graph.constants import ROUTER_NODE
from app.memory.checkpointer import close_checkpointer, get_checkpointer
from app.memory.context_assembler import (
    ADVENTURE_NODE_FRAME_MESSAGE_PREFIX,
    is_adventure_node_frame_message,
    is_internal_system_human_message,
    is_runtime_state_message,
)
from app.services.session_store import purge_chat_session_data, touch_chat_session
from app.services.tools._helpers import compute_ac
from app.utils.agent_trace import (
    trace_adventure_runtime_failed,
    trace_adventure_runtime_update,
    trace_chat_error,
    trace_chat_request,
    trace_chat_result,
)


_CHAT_SESSION_SERVICE: ChatSessionService | None = None
_CHAT_SESSION_SERVICE_LOCK = asyncio.Lock()


class ChatSessionService:
    """借助原生 Thread-based Checkpointer 负责包装、路由以及发起 Graph 推理调用。"""

    _STATE_UPDATE_FIELDS = (
        "player",
        "combat",
        "scene_units",
        "dead_units",
        "departed_units",
        "space",
        "adventure",
    )

    def __init__(
        self,
        graph: Any,
        adventure_director: AdventureDirector | None = None,
    ) -> None:
        self._graph = graph
        self._adventure_director = adventure_director
        self._default_adventure_director: AdventureDirector | None = None

    def _graph_config(self, session_id: str) -> dict[str, Any]:
        """统一声明 LangGraph 运行参数；工具串行化后复杂场景需要更多图步数。"""
        return {
            "configurable": {"thread_id": session_id},
            "recursion_limit": settings.graph_recursion_limit,
        }

    async def process_turn(
        self,
        message: Optional[str] = None,
        session_id: Optional[str] = None,
        resume_action: Optional[str] = None,
        reaction_response: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        current_session_id = session_id or str(uuid4())
        config = self._graph_config(current_session_id)
        old_state = None

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
        reward_notifications: list[dict[str, Any]] = []
        if message and not resume_action and reaction_response is None:
            pre_turn_update = await self._apply_pre_turn_adventure_runtime(config, current_session_id, message)
            if pre_turn_update:
                reward_notifications.extend(pre_turn_update.player_notifications)

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
        post_turn_update = await self._apply_adventure_runtime(config, current_session_id, state, list(state.values.get("messages", [])))
        if post_turn_update:
            reward_notifications.extend(post_turn_update.player_notifications)
        state = await self._graph.aget_state(config)
        
        state_payload = self._project_state_update_payload(state.values, include_absent=True) if hasattr(state, "values") else {}

        reply = self._append_reward_announcement(self._extract_reply_from_messages(new_messages), reward_notifications)
        pending_action = self._get_pending_action(state)
        trace_chat_result(
            current_session_id,
            entrypoint="sync",
            reply=reply,
            pending_action=pending_action,
            new_message_count=len(new_messages),
        )
        await touch_chat_session(session_id=current_session_id, message=message, reply=reply)
        return {
            "reply": reply,
            "plan": None,
            "session_id": current_session_id,
            "pending_action": pending_action,
            "player": state_payload.get("player"),
            "combat": state_payload.get("combat"),
            "space": state_payload.get("space"),
            "scene_units": state_payload.get("scene_units"),
            "dead_units": state_payload.get("dead_units"),
            "departed_units": state_payload.get("departed_units"),
            "adventure": state_payload.get("adventure"),
        }

    async def end_player_controlled_turn(self, *, session_id: str, actor_id: str) -> dict[str, Any]:
        """前端结束回合按钮的结构化入口；不经过主 Agent 自然语言裁定。"""
        from app.services.tools.combat_tools import next_turn

        config = self._graph_config(session_id)
        state = await self._graph.aget_state(config)
        baseline_msg_id = self._last_message_id(state)
        values = dict(state.values) if state and hasattr(state, "values") else {}
        combat = self._state_value_to_dict(values.get("combat"))
        player = self._state_value_to_dict(values.get("player"))
        if not combat:
            raise ValueError("当前不在战斗中，不能结束回合。")
        if combat.get("current_actor_id") != actor_id:
            raise ValueError(f"当前行动者是 {combat.get('current_actor_id')}，不能结束 {actor_id} 的回合。")
        if not self._is_player_controlled_actor(actor_id, combat, player):
            raise ValueError("只有玩家单位或友方单位可以通过前端按钮结束回合。")

        result = next_turn.invoke({
            "name": next_turn.name,
            "args": {"state": values},
            "id": "frontend-end-turn",
            "type": "tool_call",
        })
        update = dict(result.update or {}) if isinstance(result, Command) else {}
        if not update:
            raise ValueError("结束回合失败：工具没有返回状态更新。")

        direct_message = self._first_message_content(update.get("messages", []))
        state_update = self._direct_end_turn_state_update(update, actor_id, direct_message)
        events = self._events_from_tool_update(update)
        self._apply_local_state_update(values, state_update)
        if hasattr(self._graph, "aupdate_state"):
            await self._graph.aupdate_state(config, state_update, as_node=ROUTER_NODE)

        values = await self._wake_combat_agent_after_direct_turn(
            config=config,
            values=values,
            events=events,
            ended_actor_id=actor_id,
        )
        state = await self._graph.aget_state(config)
        state_payload = self._project_state_update_payload(state.values, include_absent=True) if hasattr(state, "values") else {}
        message = self._extract_reply_from_messages(self._extract_new_messages(state, baseline_msg_id)) or direct_message
        pending_action = self._get_pending_action(state)
        await touch_chat_session(session_id=session_id, message="[前端:结束回合]", reply=message)
        return {
            "session_id": session_id,
            "message": message,
            "events": events,
            "pending_action": pending_action,
            "player": state_payload.get("player"),
            "combat": state_payload.get("combat"),
            "space": state_payload.get("space"),
            "scene_units": state_payload.get("scene_units"),
            "dead_units": state_payload.get("dead_units"),
            "departed_units": state_payload.get("departed_units"),
            "adventure": state_payload.get("adventure"),
        }

    async def end_player_controlled_turn_stream(self, *, session_id: str, actor_id: str) -> AsyncGenerator[str, None]:
        """结束回合按钮的 SSE 入口；按钮动作确定，后续非玩家回合逐节点推送。"""
        from app.services.tools.combat_tools import next_turn

        config = self._graph_config(session_id)
        state = await self._graph.aget_state(config)
        baseline_msg_id = self._last_message_id(state)
        values = dict(state.values) if state and hasattr(state, "values") else {}
        combat = self._state_value_to_dict(values.get("combat"))
        player = self._state_value_to_dict(values.get("player"))
        if not combat:
            yield self._sse_event("error", {"message": "当前不在战斗中，不能结束回合。"})
            return
        if combat.get("current_actor_id") != actor_id:
            yield self._sse_event("error", {"message": f"当前行动者是 {combat.get('current_actor_id')}，不能结束 {actor_id} 的回合。"})
            return
        if not self._is_player_controlled_actor(actor_id, combat, player):
            yield self._sse_event("error", {"message": "只有玩家单位或友方单位可以通过前端按钮结束回合。"})
            return

        result = next_turn.invoke({
            "name": next_turn.name,
            "args": {"state": values},
            "id": "frontend-end-turn",
            "type": "tool_call",
        })
        update = dict(result.update or {}) if isinstance(result, Command) else {}
        if not update:
            yield self._sse_event("error", {"message": "结束回合失败：工具没有返回状态更新。"})
            return

        direct_message = self._first_message_content(update.get("messages", []))
        state_update = self._direct_end_turn_state_update(update, actor_id, direct_message)
        self._apply_local_state_update(values, state_update)
        if hasattr(self._graph, "aupdate_state"):
            await self._graph.aupdate_state(config, state_update, as_node=ROUTER_NODE)

        state_delta = self._project_state_update_payload(state_update)
        if state_delta:
            yield self._sse_event("state_update", state_delta)
        for event in self._events_from_tool_update(update):
            yield self._json_event_to_sse(event)

        combat = self._state_value_to_dict(values.get("combat"))
        player = self._state_value_to_dict(values.get("player"))
        actor = self._current_combat_actor(combat, player)
        if self._needs_agent_controlled_turn(actor):
            handoff = self._build_direct_turn_handoff_message(actor_id, actor, combat)
            async for chunk in self._graph.astream({"messages": [handoff]}, config=config, stream_mode="updates"):
                for node_output in chunk.values():
                    if not isinstance(node_output, dict):
                        continue
                    if "pending_reaction" in node_output:
                        yield self._sse_event("pending_action", self._pending_action_from_reaction(node_output.get("pending_reaction")))
                    state_delta = self._project_state_update_payload(node_output)
                    if state_delta:
                        yield self._sse_event("state_update", state_delta)
                    for event in self._events_from_graph_node_output(node_output):
                        yield self._json_event_to_sse(event)

        state = await self._graph.aget_state(config)
        state_payload = self._project_state_update_payload(state.values, include_absent=True) if hasattr(state, "values") else {}
        yield self._sse_event("state_update", state_payload)
        pending_action = self._get_pending_action(state)
        new_messages = self._extract_new_messages(state, baseline_msg_id)
        message = self._extract_reply_from_messages(new_messages) or direct_message
        await touch_chat_session(session_id=session_id, message="[前端:结束回合]", reply=message)
        yield self._sse_event("pending_action", pending_action)
        yield self._sse_event("done", {"session_id": session_id})

    def _is_player_controlled_actor(self, actor_id: str, combat: dict, player: dict | None) -> bool:
        """玩家本人和友方单位允许前端直接结束回合；敌方仍由 Agent/执行器接管。"""
        if player and actor_id == player.get("id"):
            return True
        actor = (combat.get("participants") or {}).get(actor_id) or {}
        return actor.get("side") == "ally"

    def _direct_end_turn_state_update(self, update: dict[str, Any], actor_id: str, result_text: str) -> dict[str, Any]:
        """直连按钮没有 AI tool_call 前置，写成内部流程帧比悬空 ToolMessage 更稳定。"""
        state_update = dict(update)
        state_update["messages"] = [HumanMessage(content=(
            "[系统:前端结束回合]\n"
            f"{actor_id} 已通过前端按钮结束回合；后端已执行 next_turn。\n"
            f"next_turn: {result_text or '未返回文本。'}"
        ))]
        return state_update

    async def _wake_combat_agent_after_direct_turn(
        self,
        *,
        config: dict[str, Any],
        values: dict[str, Any],
        events: list[dict[str, Any]],
        ended_actor_id: str,
    ) -> dict[str, Any]:
        """按钮只做确定的结束回合；后续怪物/NPC 决策必须交还主 Agent。"""
        combat = self._state_value_to_dict(values.get("combat"))
        player = self._state_value_to_dict(values.get("player"))
        actor = self._current_combat_actor(combat, player)
        if not self._needs_agent_controlled_turn(actor):
            return values

        handoff = self._build_direct_turn_handoff_message(ended_actor_id, actor, combat)
        async for chunk in self._graph.astream({"messages": [handoff]}, config=config, stream_mode="updates"):
            for node_output in chunk.values():
                if not isinstance(node_output, dict):
                    continue
                events.extend(self._events_from_graph_node_output(node_output))

        state = await self._graph.aget_state(config)
        return dict(state.values) if state and hasattr(state, "values") else values

    def _build_direct_turn_handoff_message(self, ended_actor_id: str, actor: dict, combat: dict) -> HumanMessage:
        """把前端按钮造成的流程事实写入上下文，并要求主 Agent 接回非玩家回合。"""
        actor_id = str(actor.get("id") or combat.get("current_actor_id") or "")
        actor_name = str(actor.get("name") or actor_id)
        return HumanMessage(content=(
            "[系统:战斗流程接管]\n"
            f"当前行动者是 {actor_name} [ID:{actor_id}]。\n"
            "请主 Agent 先决定战术意图，再按需委托战斗执行器；后台没有替你执行该单位动作。\n"
            f"上一行动者 {ended_actor_id} 已通过前端按钮结束回合。"
        ))

    def _json_event_to_sse(self, event: dict[str, Any]) -> str:
        """把非 SSE 事件数组中的项目转换为同名 SSE 事件。"""
        event_type = str(event.get("type") or "")
        if event_type == "dice_roll":
            return self._sse_event("dice_roll", event.get("payload") or {})
        if event_type == "combat_action":
            return self._sse_event("combat_action", {
                "content": event.get("content", ""),
                "hp_changes": event.get("hp_changes", []) or [],
            })
        if event_type == "tool_message":
            return self._sse_event("tool_message", {"content": event.get("content", "")})
        return self._sse_event("assistant_message", {"content": event.get("content", "")})

    def _current_combat_actor(self, combat: dict, player: dict | None) -> dict:
        """获取当前行动者；玩家在 player 字段，其他单位在 participants。"""
        actor_id = str(combat.get("current_actor_id") or "")
        if player and actor_id == player.get("id"):
            return dict(player)
        actor = (combat.get("participants") or {}).get(actor_id) or {}
        return dict(actor)

    def _needs_agent_controlled_turn(self, actor: dict) -> bool:
        """敌方和 AI 托管友方需要唤醒主 Agent 决策，不能由服务层直接执行。"""
        if not actor or actor.get("hp", 0) <= 0:
            return False
        if actor.get("side") == "enemy":
            return True
        if actor.get("side") != "ally":
            return False
        control_mode = str(actor.get("control_mode") or actor.get("controlled_by") or "").lower()
        return bool(actor.get("autopilot") or actor.get("llm_controlled") or control_mode in {"ai", "llm", "agent"})

    def _apply_local_state_update(self, values: dict[str, Any], update: dict[str, Any]) -> None:
        """本地同步 Command.update，方便连续自动流程读取最新状态。"""
        for key, value in update.items():
            if key == "messages":
                values[key] = [*list(values.get(key, [])), *list(value or [])]
            else:
                values[key] = value

    def _events_from_tool_update(self, update: dict[str, Any]) -> list[dict[str, Any]]:
        """把直连工具结果转成前端可复用的事件数组。"""
        events: list[dict[str, Any]] = []
        hp_changes = list(update.get("hp_changes", []) or [])
        for message in update.get("messages", []) or []:
            if self._is_hidden_tool_message(message):
                continue
            content = str(getattr(message, "content", ""))
            attack_roll = self._extract_attack_roll_payload(message)
            if attack_roll:
                events.append({
                    "type": "dice_roll",
                    "payload": self._build_dice_roll_event_payload(attack_roll, kind="attack"),
                })
            if hp_changes:
                events.append({"type": "combat_action", "content": content, "hp_changes": hp_changes})
                hp_changes = []
            elif content:
                events.append({"type": "tool_message", "content": content})
        if hp_changes:
            events.append({"type": "combat_action", "content": "", "hp_changes": hp_changes})
        return events

    def _events_from_combat_events(self, combat_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把执行器 combat_events 转成 JSON 事件，供非 SSE 接口复用。"""
        events: list[dict[str, Any]] = []
        for combat_event in combat_events or []:
            attack_roll = combat_event.get("attack_roll")
            if isinstance(attack_roll, dict):
                events.append({
                    "type": "dice_roll",
                    "payload": self._build_dice_roll_event_payload(attack_roll, kind="attack"),
                })
            events.append({
                "type": "combat_action",
                "content": combat_event.get("content", ""),
                "hp_changes": combat_event.get("hp_changes", []) or [],
            })
        return events

    def _events_from_graph_node_output(self, node_output: dict[str, Any]) -> list[dict[str, Any]]:
        """复用流式分支的展示规则，让按钮直连后的 Agent 结果仍能驱动前端。"""
        events = self._events_from_combat_events(node_output.get("combat_events", []) or [])
        hp_changes = list(node_output.get("hp_changes", []) or [])

        for msg in node_output.get("messages", []) or []:
            if isinstance(msg, AIMessage) and msg.content:
                if self._is_internal_tool_prelude(msg, node_output):
                    continue
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if content.strip():
                    events.append({"type": "assistant_message", "content": content})
                continue

            if isinstance(msg, ToolMessage):
                if self._is_hidden_tool_message(msg):
                    continue
                attack_roll = self._extract_attack_roll_payload(msg)
                if attack_roll:
                    events.append({
                        "type": "dice_roll",
                        "payload": self._build_dice_roll_event_payload(attack_roll, kind="attack"),
                    })
                if hp_changes:
                    events.append({"type": "combat_action", "content": msg.content, "hp_changes": hp_changes})
                    hp_changes = []
                elif msg.content:
                    events.append({"type": "tool_message", "content": msg.content})
                continue

            if isinstance(msg, HumanMessage) and isinstance(msg.content, str) and msg.content.startswith("[系统:"):
                if (
                    is_runtime_state_message(msg)
                    or is_adventure_node_frame_message(msg)
                    or msg.content.startswith("[系统:战斗执行器]")
                    or msg.content.startswith("[系统:前端结束回合]")
                    or msg.content.startswith("[系统:战斗流程接管]")
                ):
                    continue
                events.append({"type": "combat_action", "content": msg.content, "hp_changes": hp_changes})
                hp_changes = []

        if hp_changes:
            events.append({"type": "combat_action", "content": "", "hp_changes": hp_changes})
        return events

    def _is_internal_tool_prelude(self, msg: AIMessage, node_output: dict[str, Any] | None = None) -> bool:
        """隐藏只用于推动工具链的简短预备语，保留真正战果和叙事回复。"""
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            return False
        node_output = node_output or {}
        if node_output.get("hp_changes") or node_output.get("combat_events"):
            return False
        hidden_workflow_tools = {
            "manage_space",
            "manage_scene_units",
            "spawn_monsters",
            "spawn_ally",
            "prepare_combat_start",
            "delegate_combat_turn",
            "prepare_combat_end",
        }
        return all(call.get("name") in hidden_workflow_tools for call in tool_calls if isinstance(call, dict))

    def _first_message_content(self, messages: list[Any]) -> str:
        """把结构化动作产生的首条工具消息转成前端战报文本。"""
        if not messages:
            return ""
        return str(getattr(messages[0], "content", messages[0]))

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

    def _last_message_id(self, state: Any) -> str | None:
        """记录按钮操作前的消息界标，便于结束后抽取本轮 Agent 回复。"""
        messages = state.values.get("messages", []) if state and hasattr(state, "values") else []
        return getattr(messages[-1], "id", None) if messages else None

    def _extract_reply_from_messages(self, new_messages: list[Any]) -> str:
        """只拼接本轮真正对用户可见的 AI 文本回复。"""
        reply_parts: list[str] = []
        for msg in new_messages:
            if isinstance(msg, HumanMessage) and is_internal_system_human_message(msg):
                continue
            if isinstance(msg, AIMessage) and msg.content:
                if self._is_internal_tool_prelude(msg):
                    continue
                if isinstance(msg.content, str):
                    reply_parts.append(msg.content)
                elif isinstance(msg.content, list):
                    for part in msg.content:
                        if isinstance(part, str):
                            reply_parts.append(part)
                        elif isinstance(part, dict) and "text" in part:
                            reply_parts.append(part["text"])

        return "\n\n".join(reply_parts).strip()

    def _append_reward_announcement(self, reply: str, player_notifications: list[dict[str, Any]]) -> str:
        """把本轮奖励事件转成最终可见文本，不回灌给主模型。"""
        announcement = self._build_reward_announcement(player_notifications)
        if not announcement or announcement in reply:
            return reply
        return f"{reply}\n\n{announcement}".strip() if reply else announcement

    def _build_reward_announcement(self, player_notifications: list[dict[str, Any]]) -> str:
        """只把 runtime 返回的奖励事件转成玩家可见文本。"""
        xp_rewards = [notification for notification in player_notifications if notification.get("kind") == "xp_granted"]
        if not xp_rewards:
            return ""

        lines: list[str] = []
        for reward in xp_rewards:
            amount = int(reward.get("amount", 0) or 0)
            current_xp = reward.get("current_xp")
            description = str(reward.get("description", "")).strip()
            line = f"【经验奖励】你获得 {amount} XP，已计入角色卡"
            if current_xp is not None:
                line += f"，当前 XP {current_xp}"
            line += "。"
            if description:
                line += description
            lines.append(line)
        return "\n\n".join(lines)

    def _snapshot_state(self, state: Any) -> dict[str, Any]:
        """将关键状态投影成纯 Python 结构，避免后台任务持有可变对象引用。"""
        values = state.values if state and hasattr(state, "values") else {}
        return {
            "phase": values.get("phase"),
            "adventure": self._state_value_to_dict(values.get("adventure")),
            "conversation_summary": values.get("conversation_summary", ""),
            "active_combat_message_start": values.get("active_combat_message_start"),
            "combat_archives": self._state_value_to_dict(values.get("combat_archives", [])),
            "player": self._state_value_to_dict(values.get("player")),
            "combat": self._state_value_to_dict(values.get("combat")),
            "scene_units": self._mapping_state_to_dict(values.get("scene_units")),
            "dead_units": self._mapping_state_to_dict(values.get("dead_units")),
            "departed_units": self._mapping_state_to_dict(values.get("departed_units")),
            "space": self._state_value_to_dict(values.get("space")),
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
        if isinstance(value, list):
            return [self._state_value_to_dict(item) for item in value]
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "items"):
            return {key: self._state_value_to_dict(item) for key, item in value.items()}
        return value

    def _project_state_update_payload(self, values: dict[str, Any], *, include_absent: bool = False) -> dict[str, Any]:
        """把图状态投影成前端状态事件；增量事件只包含本节点真实写回的字段。"""
        payload: dict[str, Any] = {}
        for field in self._STATE_UPDATE_FIELDS:
            if not include_absent and field not in values:
                continue

            value = values.get(field)
            if field == "adventure":
                payload[field] = normalize_adventure_state(self._state_value_to_dict(value)) if value else None
            elif field == "player":
                player_data = self._state_value_to_dict(value)
                if player_data:
                    player_data["ac"] = compute_ac(player_data)
                payload[field] = player_data
            elif field == "combat":
                combat_data = self._state_value_to_dict(value)
                if combat_data:
                    for unit in combat_data.get("participants", {}).values():
                        unit["ac"] = compute_ac(unit)
                payload[field] = combat_data
            elif field in {"scene_units", "dead_units", "departed_units"}:
                payload[field] = self._mapping_state_to_dict(value) if value is not None else None
            else:
                payload[field] = self._state_value_to_dict(value)
        return payload

    async def _apply_runtime_context(self, config: dict[str, Any], session_id: str) -> None:
        """在图执行前注入稳定运行上下文，避免旧热摘要继续扰动模型前缀。"""
        if not hasattr(self._graph, "aupdate_state"):
            return

        existing_state = await self._graph.aget_state(config)
        adventure = self._current_or_default_adventure(existing_state)
        state_update = {
            "session_id": session_id,
            "episodic_context": [],
            "adventure": adventure,
        }
        state_update.update(self._adventure_node_frame_update(existing_state, adventure))
        await self._graph.aupdate_state(
            config,
            state_update,
            as_node=ROUTER_NODE,
        )

    def _current_or_default_adventure(self, state: Any) -> dict[str, Any]:
        """新会话默认进入 Lost Mine 起点，已有会话保持原进度。"""
        if state and hasattr(state, "values") and state.values.get("adventure"):
            return normalize_adventure_state(self._state_value_to_dict(state.values["adventure"]))
        return AdventureState().model_dump()

    async def _apply_adventure_runtime(
        self,
        config: dict[str, Any],
        session_id: str,
        state: Any,
        recent_messages: list[Any],
    ) -> AdventureRuntimeUpdate | None:
        """主图之后运行后台冒险裁定；不插入主对话消息，避免破坏 KC 前缀。"""
        if not recent_messages or not hasattr(self._graph, "aupdate_state") or not state or not hasattr(state, "values"):
            return None
        if state.values.get("phase") == "combat":
            return None
        try:
            update = adjudicate_and_apply_adventure_progress(
                self._state_value_to_dict(state.values),
                recent_messages=recent_messages,
                director=self._get_adventure_director(),
                session_id=session_id,
            )
        except Exception as exc:
            trace_adventure_runtime_failed(session_id, error=exc)
            return None
        trace_adventure_runtime_update(
            session_id,
            applied=update.applied,
            decision=update.decision,
            adventure_update=update.adventure,
            state_update=update.state_update,
            player_notifications=update.player_notifications,
        )
        state_update = dict(update.state_update)
        if update.adventure:
            state_update["adventure"] = update.adventure
            state_update.update(self._adventure_node_frame_update(state, update.adventure))
        if state_update:
            await self._graph.aupdate_state(config, state_update, as_node=ROUTER_NODE)
        return update

    async def _apply_pre_turn_adventure_runtime(
        self,
        config: dict[str, Any],
        session_id: str,
        player_message: str,
    ) -> AdventureRuntimeUpdate | None:
        """主 LLM 回复前先推进明确出口，避免叙事先跑、书签后追。"""
        if not hasattr(self._graph, "aupdate_state"):
            return None
        state = await self._graph.aget_state(config)
        if not state or not hasattr(state, "values") or state.values.get("phase") == "combat":
            return None
        try:
            update = adjudicate_and_apply_pre_turn_adventure_progress(
                self._state_value_to_dict(state.values),
                player_message=player_message,
                director=self._get_adventure_director(),
                session_id=session_id,
            )
        except Exception as exc:
            trace_adventure_runtime_failed(session_id, error=exc)
            return None
        trace_adventure_runtime_update(
            session_id,
            applied=update.applied,
            decision=update.decision,
            adventure_update=update.adventure,
            state_update=update.state_update,
            player_notifications=update.player_notifications,
        )
        state_update = dict(update.state_update)
        if update.adventure:
            state_update["adventure"] = update.adventure
            state_update.update(self._adventure_node_frame_update(state, update.adventure))
        if state_update:
            await self._graph.aupdate_state(config, state_update, as_node=ROUTER_NODE)
        return update

    def _adventure_node_frame_update(self, state: Any, adventure: dict[str, Any]) -> dict[str, Any]:
        """节点事实是低频大块上下文；切换节点时追加帧，避免每轮替换破坏 KC 前缀。"""
        active_node_id = str(adventure.get("active_node_id", "") or "")
        if not active_node_id:
            return {}
        messages = list(state.values.get("messages", [])) if state and hasattr(state, "values") else []
        if self._latest_adventure_node_frame_id(messages) == active_node_id:
            return {}
        return {"messages": [build_adventure_node_frame_message(active_node_id)]}

    def _latest_adventure_node_frame_id(self, messages: list[Any]) -> str:
        """只看最近一个节点帧；回访同节点时允许在新位置再次追加。"""
        for message in reversed(messages):
            if not is_adventure_node_frame_message(message):
                continue
            content = str(getattr(message, "content", ""))
            raw = content.removeprefix(ADVENTURE_NODE_FRAME_MESSAGE_PREFIX).strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return ""
            return str(payload.get("node_id", "") or "")
        return ""

    def _get_adventure_director(self) -> AdventureDirector:
        """懒加载后台 director，避免每轮重复初始化 LLM 客户端。"""
        if self._adventure_director is not None:
            return self._adventure_director
        if self._default_adventure_director is None:
            self._default_adventure_director = LLMAdventureDirector()
        return self._default_adventure_director

    async def aclose(self) -> None:
        """保留异步关闭接口，方便 FastAPI 生命周期统一调用。"""
        return None

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

    def _build_dice_roll_event_payload(self, roll_data: dict[str, Any], *, kind: str = "check") -> dict[str, Any]:
        """统一给前端骰子卡片补足展示字段，避免各流式分支格式漂移。"""
        raw_roll = roll_data["raw_roll"]
        final_total = roll_data.get("final_total", roll_data.get("hit_total", raw_roll))
        modifier = roll_data.get("modifier", roll_data.get("attack_bonus", final_total - raw_roll))
        target = roll_data.get("target_ac") or roll_data.get("dc")
        attack_name = roll_data.get("attack_name") or roll_data.get("atk_name_display")
        title = attack_name or roll_data.get("reason") or ("Attack Roll" if kind == "attack" else "D20 Check")

        return {
            "kind": kind,
            "title": title,
            "raw_roll": raw_roll,
            "modifier": modifier,
            "final_total": final_total,
            "target": target,
            "target_label": "AC" if kind == "attack" else "DC",
            "formula": roll_data.get("formula", "1d20"),
            "advantage": roll_data.get("advantage", "normal"),
        }

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

    def _stream_combat_event(self, combat_event: dict[str, Any]) -> list[str]:
        """把执行器聚合出的战斗事件还原成前端既有骰子/血条事件。"""
        events: list[str] = []
        attack_roll = combat_event.get("attack_roll")
        if isinstance(attack_roll, dict):
            events.append(self._sse_event("dice_roll", self._build_dice_roll_event_payload(attack_roll, kind="attack")))
        events.append(self._sse_event("combat_action", {
            "content": combat_event.get("content", ""),
            "hp_changes": combat_event.get("hp_changes", []) or [],
        }))
        return events

    async def process_turn_stream(
        self,
        message: Optional[str] = None,
        session_id: Optional[str] = None,
        resume_action: Optional[str] = None,
        reaction_response: Optional[dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        """以 SSE 事件流的方式推送图执行过程中的每一步结果。"""
        current_session_id = session_id or str(uuid4())
        config = self._graph_config(current_session_id)

        old_state = await self._graph.aget_state(config)
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
        reward_notifications: list[dict[str, Any]] = []
        if message and reaction_response is None and resume_action is None:
            pre_turn_update = await self._apply_pre_turn_adventure_runtime(config, current_session_id, message)
            if pre_turn_update:
                reward_notifications.extend(pre_turn_update.player_notifications)

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

                state_delta = self._project_state_update_payload(node_output)
                if state_delta:
                    yield self._sse_event("state_update", state_delta)

                for combat_event in node_output.get("combat_events", []) or []:
                    for event in self._stream_combat_event(combat_event):
                        yield event

                # 提取消息增量
                new_messages = node_output.get("messages", [])
                hp_changes = node_output.get("hp_changes", [])

                for msg in new_messages:
                    if isinstance(msg, AIMessage) and msg.content:
                        if self._is_internal_tool_prelude(msg, node_output):
                            continue
                        # 战斗流里很多给玩家看的旁白和工具调用会落在同一条 AIMessage 上，不能因为带 tool_calls 就吞掉文本。
                        content = msg.content if isinstance(msg.content, str) else str(msg.content)
                        # 只过滤模型工具调用时吐出的纯空白占位，避免前端生成空气泡。
                        if content.strip():
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
                                    yield self._sse_event("dice_roll", self._build_dice_roll_event_payload(roll_data))
                            except Exception:
                                pass

                        attack_roll = self._extract_attack_roll_payload(msg)
                        if attack_roll:
                            yield self._sse_event("dice_roll", self._build_dice_roll_event_payload(attack_roll, kind="attack"))

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
                        if (
                            is_runtime_state_message(msg)
                            or is_adventure_node_frame_message(msg)
                            or msg.content.startswith("[系统:战斗执行器]")
                        ):
                            continue
                        attack_roll = self._extract_attack_roll_payload(msg)
                        if attack_roll:
                            yield self._sse_event("dice_roll", self._build_dice_roll_event_payload(attack_roll, kind="attack"))
                            
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
        new_messages = self._extract_new_messages(state, baseline_msg_id)
        full_messages = list(state.values.get("messages", [])) if hasattr(state, "values") else new_messages
        post_turn_update = await self._apply_adventure_runtime(config, current_session_id, state, full_messages)
        if post_turn_update:
            reward_notifications.extend(post_turn_update.player_notifications)
        state = await self._graph.aget_state(config)

        state_payload = self._project_state_update_payload(state.values, include_absent=True) if hasattr(state, "values") else {}

        reward_announcement = self._build_reward_announcement(reward_notifications)
        if reward_announcement:
            yield self._sse_event("assistant_message", {"content": reward_announcement})

        yield self._sse_event("state_update", state_payload)

        pending = self._get_pending_action(state)
        reply = self._append_reward_announcement(self._extract_reply_from_messages(new_messages), reward_notifications)
        trace_chat_result(
            current_session_id,
            entrypoint="stream",
            reply=reply,
            pending_action=pending,
            new_message_count=len(new_messages),
        )
        await touch_chat_session(session_id=current_session_id, message=message, reply=reply)
        yield self._sse_event("pending_action", pending)

        yield self._sse_event("done", {"session_id": current_session_id})

    # ── 历史消息恢复 ──────────────────────────────────────────

    async def get_history(self, session_id: str, limit: int = 10) -> dict[str, Any]:
        """从 checkpointer 中恢复最近的对话消息，供前端初始化。"""
        config = self._graph_config(session_id)
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
                if self._is_internal_tool_prelude(msg):
                    continue
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                history.append({"role": "assistant", "content": content})
            elif isinstance(msg, HumanMessage) and not str(msg.content).startswith("[系统"):
                history.append({"role": "user", "content": msg.content})

        history.reverse()

        player_data = None
        combat_data = None
        space_data = None
        scene_units_data = None
        departed_units_data = None
        adventure_data = None
        if hasattr(state, "values"):
            adventure = state.values.get("adventure")
            if adventure:
                adventure_data = normalize_adventure_state(self._state_value_to_dict(adventure))
            player = state.values.get("player")
            if player:
                player_data = player.model_dump() if hasattr(player, "model_dump") else dict(player)
                player_data["ac"] = compute_ac(player_data)
            combat = state.values.get("combat")
            if combat:
                combat_data = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat)
                for unit in combat_data.get("participants", {}).values():
                    unit["ac"] = compute_ac(unit)
            space = state.values.get("space")
            if space:
                space_data = space.model_dump() if hasattr(space, "model_dump") else dict(space)
            scene_units = state.values.get("scene_units")
            if scene_units:
                scene_units_data = self._mapping_state_to_dict(scene_units)
            departed_units = state.values.get("departed_units")
            if departed_units:
                departed_units_data = self._mapping_state_to_dict(departed_units)

        return {
            "messages": history,
            "player": player_data,
            "combat": combat_data,
            "space": space_data,
            "scene_units": scene_units_data,
            "departed_units": departed_units_data,
            "adventure": adventure_data,
        }

    async def delete_session(self, session_id: str) -> dict[str, int]:
        """删除会话持久化数据。"""
        return await purge_chat_session_data(session_id)


async def get_chat_session_service() -> ChatSessionService:
    """在首个请求到达时初始化图与异步 checkpointer。"""
    global _CHAT_SESSION_SERVICE

    if _CHAT_SESSION_SERVICE is not None:
        return _CHAT_SESSION_SERVICE

    async with _CHAT_SESSION_SERVICE_LOCK:
        if _CHAT_SESSION_SERVICE is not None:
            return _CHAT_SESSION_SERVICE

        graph = build_graph(checkpointer=await get_checkpointer(settings))
        _CHAT_SESSION_SERVICE = ChatSessionService(
            graph=graph,
        )
        return _CHAT_SESSION_SERVICE


async def delete_chat_session(session_id: str) -> dict[str, int]:
    """删除会话时只在已有服务中等待后台任务，避免为纯清理操作初始化完整图。"""
    if _CHAT_SESSION_SERVICE is not None:
        return await _CHAT_SESSION_SERVICE.delete_session(session_id)
    return await purge_chat_session_data(session_id)


def reset_cached_adventure_director() -> None:
    """模型配置热切换后，丢弃持有旧 LLM 客户端的后台裁定器。"""
    if _CHAT_SESSION_SERVICE is not None:
        _CHAT_SESSION_SERVICE._default_adventure_director = None


async def close_chat_session_service() -> None:
    """关闭持久化资源并清理 service 单例。"""
    global _CHAT_SESSION_SERVICE

    if _CHAT_SESSION_SERVICE is not None:
        await _CHAT_SESSION_SERVICE.aclose()

    _CHAT_SESSION_SERVICE = None
    await close_checkpointer()
