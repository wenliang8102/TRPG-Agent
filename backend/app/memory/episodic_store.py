"""会话级情节记忆存储。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiosqlite
import psycopg

from app.config.settings import settings
from app.utils.asyncio_policy import configure_windows_selector_event_loop_policy


configure_windows_selector_event_loop_policy()

from app.utils.storage_paths import resolve_memory_db_path


class EpisodicStore:
    """会话级 append-only 记忆表，按配置落到 SQLite 或 PostgreSQL。"""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        database_backend: str | None = None,
        database_url: str | None = None,
    ) -> None:
        self._backend = database_backend or ("sqlite" if db_path is not None else settings.database_backend)
        self._db_path = db_path or settings.memory_db_path
        self._database_url = database_url or settings.database_url
        self._conn: aiosqlite.Connection | psycopg.AsyncConnection | None = None
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        """惰性初始化连接与表结构，避免在热路径提前占用资源。"""
        if self._conn is not None:
            return

        async with self._lock:
            if self._conn is not None:
                return

            if self._backend == "postgres":
                self._conn = await self._setup_postgres()
                return

            db_file = resolve_memory_db_path(self._db_path)

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

    async def _setup_postgres(self) -> psycopg.AsyncConnection:
        """PostgreSQL 表结构沿用现有记忆语义，不在迁移时改字段含义。"""
        if not self._database_url:
            raise ValueError("TRPG_DATABASE_URL is required when TRPG_DATABASE_BACKEND=postgres.")

        conn = await psycopg.AsyncConnection.connect(self._database_url)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodic_memory (
                id BIGSERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                record_kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
                UNIQUE(session_id, turn_id, record_kind)
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodic_memory_session_created ON episodic_memory(session_id, created_at DESC, id DESC)"
        )
        await conn.commit()
        return conn

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

        if self._backend == "postgres":
            await self._append_postgres_record(session_id=session_id, turn_id=turn_id, record_kind=record_kind, payload=payload)
            return

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

    async def _append_postgres_record(
        self,
        *,
        session_id: str,
        turn_id: str,
        record_kind: str,
        payload: dict[str, Any],
    ) -> None:
        assert isinstance(self._conn, psycopg.AsyncConnection)
        await self._conn.execute(
            """
            INSERT INTO episodic_memory(session_id, turn_id, record_kind, payload_json)
            VALUES (%s, %s, %s, %s)
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

        if self._backend == "postgres":
            return await self._fetch_postgres_recent_records(session_id, limit)

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

    async def _fetch_postgres_recent_records(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        assert isinstance(self._conn, psycopg.AsyncConnection)
        cursor = await self._conn.execute(
            """
            SELECT turn_id, record_kind, payload_json, created_at
            FROM episodic_memory
            WHERE session_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (session_id, limit),
        )
        rows = await cursor.fetchall()
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

        if self._backend == "postgres":
            rows = await self._fetch_postgres_recent_summary_rows(session_id, limit)
            return self._summaries_from_rows(rows)

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

        return self._summaries_from_rows(rows)

    async def _fetch_postgres_recent_summary_rows(self, session_id: str, limit: int) -> list[tuple]:
        assert isinstance(self._conn, psycopg.AsyncConnection)
        cursor = await self._conn.execute(
            """
            SELECT payload_json
            FROM episodic_memory
            WHERE session_id = %s AND record_kind = 'turn_summary'
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (session_id, limit),
        )
        return await cursor.fetchall()

    def _summaries_from_rows(self, rows: list[tuple]) -> list[str]:
        summaries: list[str] = []
        for row in reversed(rows):
            payload = json.loads(row[0])
            summary = str(payload.get("summary", "")).strip()
            if summary:
                summaries.append(summary)
        return summaries

    async def close(self) -> None:
        """关闭独立连接，避免测试或热重载时遗留句柄。"""
        if self._conn is None:
            return

        async with self._lock:
            if self._conn is None:
                return

            await self._conn.close()
            self._conn = None
