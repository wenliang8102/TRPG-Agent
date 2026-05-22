"""后台记忆摄取管线。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.config.settings import settings
from app.memory.episodic_store import EpisodicStore
from app.services.llm_service import LLMService
from app.utils.agent_trace import fail_llm_trace, finish_llm_trace, start_llm_trace
from app.utils.logger import logger


class MemoryIngestionPipeline:
    """将单轮对话沉淀为粗粒度的情节记忆，避免把高频战斗态长期化。"""

    def __init__(
        self,
        episodic_store: EpisodicStore,
        llm_service: LLMService | None = None,
        *,
        trace_dir: str | Path | None = None,
    ) -> None:
        self._episodic_store = episodic_store
        self._llm_service = llm_service
        self._trace_dir = trace_dir

    async def ingest(
        self,
        *,
        session_id: str,
        turn_id: str,
        old_state: dict[str, Any],
        new_state: dict[str, Any],
        new_messages: list[BaseMessage],
        reply: str,
    ) -> None:
        """把一轮对话拆成消息记录、稳定事件与派生摘要三个层次。"""
        normalized_messages = self._normalize_messages(new_messages)
        stable_events = self._extract_stable_events(old_state, new_state)
        combat_summary = self._extract_combat_end_summary(normalized_messages, stable_events)
        rule_turn_summary = self._build_turn_summary(normalized_messages, stable_events, reply, combat_summary)
        turn_summary = await self._compress_turn_summary(
            session_id=session_id,
            turn_id=turn_id,
            phase=str(new_state.get("phase") or old_state.get("phase") or "memory_ingestion"),
            normalized_messages=normalized_messages,
            stable_events=stable_events,
            reply=reply,
            combat_summary=combat_summary,
            rule_turn_summary=rule_turn_summary,
        )

        if not normalized_messages and not stable_events and not turn_summary:
            return

        if normalized_messages:
            await self._episodic_store.append_record(
                session_id=session_id,
                turn_id=turn_id,
                record_kind="turn_messages",
                payload={"messages": normalized_messages},
            )

        if stable_events:
            await self._episodic_store.append_record(
                session_id=session_id,
                turn_id=turn_id,
                record_kind="stable_events",
                payload={"events": stable_events},
            )

        if turn_summary:
            await self._episodic_store.append_record(
                session_id=session_id,
                turn_id=turn_id,
                record_kind="turn_summary",
                payload={"summary": turn_summary},
            )

    async def close(self) -> None:
        await self._episodic_store.close()

    async def _compress_turn_summary(
        self,
        *,
        session_id: str,
        turn_id: str,
        phase: str,
        normalized_messages: list[dict[str, Any]],
        stable_events: list[dict[str, Any]],
        reply: str,
        combat_summary: str,
        rule_turn_summary: str,
    ) -> str:
        if not self._should_use_model_summary(normalized_messages, stable_events, reply, combat_summary):
            return rule_turn_summary

        if not settings.memory_summary_enabled or self._llm_service is None:
            return rule_turn_summary

        system_prompt = self._build_summary_system_prompt()
        summary_input = self._build_summary_input(
            normalized_messages=normalized_messages,
            stable_events=stable_events,
            reply=reply,
            combat_summary=combat_summary,
            rule_turn_summary=rule_turn_summary,
        )

        invocation_id, started_at = start_llm_trace(
            session_id,
            mode="memory_summary",
            phase=phase,
            system_prompt=system_prompt,
            hud_text="",
            runtime_state_text="",
            messages=[HumanMessage(content=summary_input)],
            tools=[],
            trace_dir=self._trace_dir,
        )

        started_perf = asyncio.get_running_loop().time()
        try:
            summary_text = await asyncio.to_thread(
                self._llm_service.invoke_summary,
                summary_input,
                system_prompt=system_prompt,
            )
            normalized_summary = self._normalize_model_summary(summary_text)
            finish_llm_trace(
                session_id,
                invocation_id=invocation_id,
                started_at=started_at,
                duration_ms=(asyncio.get_running_loop().time() - started_perf) * 1000,
                mode="memory_summary",
                phase=phase,
                response=AIMessage(content=normalized_summary),
                trace_dir=self._trace_dir,
            )
            return normalized_summary
        except Exception as exc:
            fail_llm_trace(
                session_id,
                invocation_id=invocation_id,
                started_at=started_at,
                duration_ms=(asyncio.get_running_loop().time() - started_perf) * 1000,
                mode="memory_summary",
                phase=phase,
                error=exc,
                trace_dir=self._trace_dir,
            )
            logger.exception("Memory summary compression failed for session {} turn {}", session_id, turn_id)
            return rule_turn_summary

    def _should_use_model_summary(
        self,
        normalized_messages: list[dict[str, Any]],
        stable_events: list[dict[str, Any]],
        reply: str,
        combat_summary: str,
    ) -> bool:
        if stable_events or combat_summary:
            return True
        if reply.strip():
            return True
        return bool(normalized_messages)

    def _build_summary_system_prompt(self) -> str:
        return (
            "你负责把单轮 TRPG 交互压缩成供后续模型使用的近期情节记忆。\n"
            "输出自然中文短段落，不要项目符号，不要解释，不要加‘摘要’或‘总结’前缀。\n"
            "只保留对后续叙事仍有价值的稳定事实：剧情推进、关系或立场变化、关键发现、重要承诺、未解决风险、战斗结果、持久资源消耗、持久状态变化。\n"
            "不要重复 HUD 已提供的当前玩家状态；不要描述当前 HP、临时 HP、AC、先攻、行动经济、移动力、当前回合、即时站位。\n"
            "不要复述逐次掷骰、逐条工具调用、细碎伤害数字，避免把短期战报污染成长期记忆。\n"
            "如果本轮主要是战斗结束，优先总结战斗结果、关键经过、角色表现、资源与状态后果，以及战后场景线索。\n"
            "战斗结束摘要可以更厚，尽量保留原始有效信息三成左右，避免后续模型误以为刚才的战斗没有发生。\n"
            "如果没有值得长期保留的新信息，返回空字符串。"
        )

    def _build_summary_input(
        self,
        *,
        normalized_messages: list[dict[str, Any]],
        stable_events: list[dict[str, Any]],
        reply: str,
        combat_summary: str,
        rule_turn_summary: str,
    ) -> str:
        payload = {
            "stable_events": stable_events,
            "combat_end_summary": combat_summary,
            "assistant_reply": reply.strip(),
            "recent_messages": normalized_messages[-6:],
            "rule_summary_draft": rule_turn_summary,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _normalize_model_summary(self, summary_text: str) -> str:
        summary = summary_text.strip().strip('"').strip()
        if not summary or summary.lower() in {"none", "null", "n/a"}:
            return ""

        for prefix in ("摘要：", "总结：", "近期情节记忆："):
            if summary.startswith(prefix):
                summary = summary[len(prefix):].strip()

        if len(summary) > 1200:
            summary = summary[:1197].rstrip("，。； ") + "..."
        return summary

    def _normalize_messages(self, new_messages: list[BaseMessage]) -> list[dict[str, Any]]:
        """只保留后续检索有意义的消息骨架，不把整段原始上下文再复制一遍。"""
        normalized: list[dict[str, Any]] = []
        for message in new_messages:
            role, kind = self._message_role_and_kind(message)
            content = self._summarize_message(message).strip()
            if not content and kind != "assistant_tool_call":
                continue

            item: dict[str, Any] = {
                "role": role,
                "kind": kind,
                "content": content,
            }
            if isinstance(message, ToolMessage) and getattr(message, "name", None):
                item["tool_name"] = message.name
            if isinstance(message, AIMessage) and message.tool_calls:
                item["tool_calls"] = [tool_call.get("name", "") for tool_call in message.tool_calls]
            normalized.append(item)
        return normalized

    def _extract_stable_events(self, old_state: dict[str, Any], new_state: dict[str, Any]) -> list[dict[str, Any]]:
        """只记录稳定且可复用的状态变化，排除 HP/先攻/动作经济等高频瞬时态。"""
        events: list[dict[str, Any]] = []

        old_player = old_state.get("player") or {}
        new_player = new_state.get("player") or {}
        old_combat = old_state.get("combat")
        new_combat = new_state.get("combat")

        if not old_player and new_player:
            events.append(
                {
                    "type": "player_profile_loaded",
                    "player_name": new_player.get("name", ""),
                    "role_class": new_player.get("role_class", ""),
                    "level": new_player.get("level", 1),
                }
            )

        resource_changes = self._diff_mapping(old_player.get("resources", {}), new_player.get("resources", {}))
        if resource_changes:
            events.append(
                {
                    "type": "resource_update",
                    "player_name": new_player.get("name", old_player.get("name", "")),
                    "changes": resource_changes,
                }
            )

        condition_changes = self._diff_condition_ids(old_player.get("conditions", []), new_player.get("conditions", []))
        if condition_changes:
            events.append(
                {
                    "type": "condition_update",
                    "player_name": new_player.get("name", old_player.get("name", "")),
                    **condition_changes,
                }
            )

        if old_combat is None and new_combat is not None:
            events.append(
                {
                    "type": "combat_started",
                    "round": new_combat.get("round", 1),
                    "participant_count": len(new_combat.get("participants", {})) + (1 if new_player else 0),
                }
            )

        if old_combat is not None and new_combat is None:
            events.append(
                {
                    "type": "combat_ended",
                    "rounds": old_combat.get("round", 0),
                    "dead_unit_count": len((new_state.get("dead_units") or {}).keys()),
                }
            )

        return events

    def _build_turn_summary(
        self,
        normalized_messages: list[dict[str, Any]],
        stable_events: list[dict[str, Any]],
        reply: str,
        combat_summary: str,
    ) -> str:
        """用低成本规则生成可读摘要，先替代旧的阻塞式 LLM 摘要。"""
        lines: list[str] = []
        for event in stable_events:
            if event["type"] == "player_profile_loaded":
                lines.append(
                    f"载入角色 {event.get('player_name') or '玩家'}（{event.get('role_class') or '未知职业'}，{event.get('level', 1)} 级）。"
                )
            elif event["type"] == "resource_update":
                changes = ", ".join(
                    f"{change['key']}: {change['old']} -> {change['new']}" for change in event.get("changes", [])
                )
                if changes:
                    lines.append(f"资源变更：{changes}。")
            elif event["type"] == "condition_update":
                added = ", ".join(event.get("added", []))
                removed = ", ".join(event.get("removed", []))
                parts = []
                if added:
                    parts.append(f"获得状态 {added}")
                if removed:
                    parts.append(f"解除状态 {removed}")
                if parts:
                    lines.append("；".join(parts) + "。")
            elif event["type"] == "combat_started":
                lines.append(f"战斗开始，参与单位 {event.get('participant_count', 0)} 名。")
            elif event["type"] == "combat_ended":
                lines.append(f"战斗结束，共持续 {event.get('rounds', 0)} 回合。")

        if combat_summary:
            lines.append(f"战斗摘要：{combat_summary}")
            return " ".join(lines[:4]).strip()

        if reply:
            lines.append(f"主持人回应：{reply}")

        for message in normalized_messages[-3:]:
            content = message.get("content", "")
            if not content:
                continue
            if message["kind"] == "tool_result":
                lines.append(f"工具结果：{content}")
            elif message["kind"] == "system":
                lines.append(f"系统播报：{content}")

        if not lines and reply:
            return f"主持人回应：{reply}"

        return " ".join(lines[:4]).strip()

    def _extract_combat_end_summary(
        self,
        normalized_messages: list[dict[str, Any]],
        stable_events: list[dict[str, Any]],
    ) -> str:
        """战斗结束记忆来自本轮工具结果，不再依赖战斗归档字段。"""
        if not any(event["type"] == "combat_ended" for event in stable_events):
            return ""

        for message in reversed(normalized_messages):
            if message.get("kind") == "tool_result" and message.get("tool_name") == "end_combat":
                return str(message.get("content", "")).strip()[:1200]

        return ""

    def _message_role_and_kind(self, message: BaseMessage) -> tuple[str, str]:
        if isinstance(message, ToolMessage):
            return "tool", "tool_result"
        if isinstance(message, AIMessage) and message.tool_calls:
            return "assistant", "assistant_tool_call"
        if isinstance(message, AIMessage):
            return "assistant", "assistant"
        if isinstance(message, HumanMessage) and isinstance(message.content, str) and message.content.startswith("[系统:"):
            return "system", "system"
        return "user", "user"

    def _summarize_message(self, message: BaseMessage) -> str:
        content = self._message_content_to_text(getattr(message, "content", "")).strip()
        if not content:
            return ""

        if isinstance(message, ToolMessage):
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            preview = " | ".join(lines[:3])
            limit = 1200 if getattr(message, "name", "") == "end_combat" else 180
            return preview[:limit]

        if isinstance(message, HumanMessage) and content.startswith("[系统:"):
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            head = lines[0] if lines else "[系统]"
            body = " | ".join(lines[1:3])
            return f"{head} {body}".strip()

        return content[:240]

    def _message_content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
            return "\n".join(parts)
        return str(content)

    def _diff_mapping(self, old_mapping: dict[str, Any], new_mapping: dict[str, Any]) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        keys = sorted(set(old_mapping.keys()) | set(new_mapping.keys()))
        for key in keys:
            old_value = old_mapping.get(key)
            new_value = new_mapping.get(key)
            if old_value == new_value:
                continue
            changes.append({"key": key, "old": old_value, "new": new_value})
        return changes

    def _diff_condition_ids(self, old_conditions: list[Any], new_conditions: list[Any]) -> dict[str, list[str]]:
        old_ids = {self._condition_id(item) for item in old_conditions if self._condition_id(item)}
        new_ids = {self._condition_id(item) for item in new_conditions if self._condition_id(item)}
        added = sorted(new_ids - old_ids)
        removed = sorted(old_ids - new_ids)
        return {"added": added, "removed": removed} if added or removed else {}

    def _condition_id(self, condition: Any) -> str:
        if isinstance(condition, dict):
            return str(condition.get("id", ""))
        if hasattr(condition, "model_dump"):
            dumped = condition.model_dump()
            return str(dumped.get("id", ""))
        return str(getattr(condition, "id", ""))
