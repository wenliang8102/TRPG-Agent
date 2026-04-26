"""会话级情节记忆存储。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite


class EpisodicStore:
    """基于 SQLite 的 append-only 会话记忆表。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        """惰性初始化连接与表结构，避免在热路径提前占用资源。"""
        if self._conn is not None:
            return

        async with self._lock:
            if self._conn is not None:
                return

            db_file = Path(self._db_path)
            if not db_file.is_absolute():
                db_file = Path.cwd() / db_file
            db_file.parent.mkdir(parents=True, exist_ok=True)

            conn = await aiosqlite.connect(str(db_file))
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA synchronous = NORMAL")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodic_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    record_kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(session_id, turn_id, record_kind)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodic_memory_session_created ON episodic_memory(session_id, created_at DESC, id DESC)"
            )
            await conn.commit()
            self._conn = conn

    async def append_record(
        self,
        *,
        session_id: str,
        turn_id: str,
        record_kind: str,
        payload: dict[str, Any],
    ) -> None:
        """为一个回合写入单条派生记忆，重复 turn_id 会覆盖同类记录。"""
        await self.setup()
        assert self._conn is not None

        await self._conn.execute(
            """
            INSERT INTO episodic_memory(session_id, turn_id, record_kind, payload_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, turn_id, record_kind)
            DO UPDATE SET payload_json = excluded.payload_json
            """,
            (session_id, turn_id, record_kind, json.dumps(payload, ensure_ascii=False)),
        )
        await self._conn.commit()

    async def fetch_recent_records(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """按时间倒序读取最近的情节记忆，供后续上下文装配使用。"""
        await self.setup()
        assert self._conn is not None

        cursor = await self._conn.execute(
            """
            SELECT turn_id, record_kind, payload_json, created_at
            FROM episodic_memory
            WHERE session_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {
                "turn_id": row[0],
                "record_kind": row[1],
                "payload": json.loads(row[2]),
                "created_at": row[3],
            }
            for row in rows
        ]

    async def fetch_recent_summaries(self, session_id: str, limit: int = 4) -> list[str]:
        """按时间读取最近的 turn summary，供热路径拼装长期情节记忆。"""
        await self.setup()
        assert self._conn is not None

        cursor = await self._conn.execute(
            """
            SELECT payload_json
            FROM episodic_memory
            WHERE session_id = ? AND record_kind = 'turn_summary'
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()

        summaries: list[str] = []
        for row in reversed(rows):
            payload = json.loads(row[0])
            summary = str(payload.get("summary", "")).strip()
            if summary:
                summaries.append(summary)
        return summaries

    async def fetch_recent_context_blocks(
        self,
        session_id: str,
        *,
        summary_limit: int = 4,
        stable_event_limit: int = 4,
        record_limit: int = 16,
        max_blocks: int = 6,
    ) -> list[str]:
        """混合读取近期摘要与稳定事件，供热路径直接注入系统提示。"""
        records = await self.fetch_recent_records(session_id, limit=record_limit)
        return self._build_context_blocks(
            records,
            summary_limit=summary_limit,
            stable_event_limit=stable_event_limit,
            max_blocks=max_blocks,
        )

    def _build_context_blocks(
        self,
        records: list[dict[str, Any]],
        *,
        summary_limit: int = 4,
        stable_event_limit: int = 4,
        max_blocks: int = 6,
    ) -> list[str]:
        """把原始记录压成少量高密度上下文块，避免热路径提示膨胀。"""
        entries: list[dict[str, Any]] = []
        sequence = 0
        for record in reversed(records):
            kind = record.get("record_kind", "")
            payload = record.get("payload", {})

            if kind == "stable_events":
                for event in payload.get("events", []):
                    text = self._format_stable_event(event)
                    if not text:
                        continue
                    entries.append({"sequence": sequence, "kind": "stable_event", "text": text})
                    sequence += 1
                continue

            if kind == "turn_summary":
                summary = str(payload.get("summary", "")).strip()
                if summary:
                    entries.append({"sequence": sequence, "kind": "turn_summary", "text": summary})
                    sequence += 1

        recent_summaries = [entry for entry in entries if entry["kind"] == "turn_summary"][-summary_limit:]
        recent_events = [entry for entry in entries if entry["kind"] == "stable_event"][-stable_event_limit:]
        selected_sequences = {entry["sequence"] for entry in recent_summaries + recent_events}
        selected_entries = [entry for entry in entries if entry["sequence"] in selected_sequences]
        selected_entries = selected_entries[-max_blocks:]
        return [str(entry["text"])[:300] for entry in selected_entries]

    def _format_stable_event(self, event: dict[str, Any]) -> str:
        """把稳定事件转成适合系统提示的简洁自然语言。"""
        event_type = event.get("type")
        if event_type == "player_profile_loaded":
            return (
                f"已载入角色 {event.get('player_name') or '玩家'}"
                f"（{event.get('role_class') or '未知职业'}，{event.get('level', 1)} 级）。"
            )

        if event_type == "resource_update":
            changes = event.get("changes", [])
            if not changes:
                return ""
            rendered = ", ".join(
                f"{change.get('key')}: {change.get('old')} -> {change.get('new')}" for change in changes
            )
            return f"资源更新：{rendered}。"

        if event_type == "condition_update":
            added = ", ".join(event.get("added", []))
            removed = ", ".join(event.get("removed", []))
            parts: list[str] = []
            if added:
                parts.append(f"获得状态 {added}")
            if removed:
                parts.append(f"解除状态 {removed}")
            if not parts:
                return ""
            actor_name = event.get("player_name") or "角色"
            return f"{actor_name}：{'；'.join(parts)}。"

        if event_type == "combat_started":
            return f"战斗开始，参与单位 {event.get('participant_count', 0)} 名。"

        if event_type == "combat_ended":
            return (
                f"战斗结束，共持续 {event.get('rounds', 0)} 回合，"
                f"死亡单位 {event.get('dead_unit_count', 0)} 个。"
            )

        return ""

    async def close(self) -> None:
        """关闭独立连接，避免测试或热重载时遗留句柄。"""
        if self._conn is None:
            return

        async with self._lock:
            if self._conn is None:
                return

            await self._conn.close()
            self._conn = None