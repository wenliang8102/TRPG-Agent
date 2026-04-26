"""后台记忆摄取管线。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.memory.episodic_store import EpisodicStore


class MemoryIngestionPipeline:
    """将单轮对话沉淀为粗粒度的情节记忆，避免把高频战斗态长期化。"""

    def __init__(self, episodic_store: EpisodicStore) -> None:
        self._episodic_store = episodic_store

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
        turn_summary = self._build_turn_summary(normalized_messages, stable_events, reply)

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
            return preview[:180]

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