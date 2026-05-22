"""会话生命周期与持久化清理。"""

from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

import aiosqlite
import psycopg

from app.config.settings import settings
from app.utils.asyncio_policy import configure_windows_selector_event_loop_policy
from app.utils.agent_trace import resolve_trace_file
from app.utils.storage_paths import resolve_memory_db_path


configure_windows_selector_event_loop_policy()

SESSION_TABLE = "app_chat_sessions"
POSTGRES_CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
POSTGRES_SESSION_STORAGE_TABLES = (*POSTGRES_CHECKPOINT_TABLES, "episodic_memory")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _quote_identifier(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) + chr(34))}"'


def _build_title(message: str | None) -> str:
    text = (message or "").strip()
    if not text:
        return "新的冒险"
    return text[:24]


def _build_preview(message: str | None, reply: str | None) -> str:
    text = (reply or message or "").strip()
    return text[:120]


async def _table_exists(conn: aiosqlite.Connection, table: str) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None


async def _postgres_table_exists(conn: psycopg.AsyncConnection, table: str) -> bool:
    cursor = await conn.execute("SELECT to_regclass(%s)", (table,))
    row = await cursor.fetchone()
    return bool(row and row[0])


async def _has_thread_checkpoint(conn: aiosqlite.Connection, session_id: str) -> bool:
    """历史页只恢复 checkpoint 中存在的会话；元数据孤儿不能继续展示。"""
    if not await _table_exists(conn, "checkpoints"):
        return False

    cursor = await conn.execute(
        "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1",
        (session_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None


async def _postgres_has_thread_checkpoint(conn: psycopg.AsyncConnection, session_id: str) -> bool:
    """PostgreSQL 下仍以官方 checkpoint 表作为可恢复会话依据。"""
    if not await _postgres_table_exists(conn, "checkpoints"):
        return False

    cursor = await conn.execute(
        "SELECT 1 FROM checkpoints WHERE thread_id = %s LIMIT 1",
        (session_id,),
    )
    return await cursor.fetchone() is not None


def _delete_trace_file(session_id: str) -> int:
    trace_file = resolve_trace_file(session_id)
    if not trace_file.exists():
        return 0

    trace_file.unlink()
    return 1


def _delete_orphan_trace_files(live_session_ids: list[str]) -> int:
    """trace 是会话派生产物；没有可恢复会话支撑的旧 trace 不应继续参与导出。"""
    trace_dir = resolve_trace_file("__trace_dir_probe__").parent
    live_trace_names = {resolve_trace_file(session_id).name for session_id in live_session_ids}
    deleted_count = 0

    for trace_file in trace_dir.glob("*.jsonl"):
        if trace_file.name in live_trace_names:
            continue

        trace_file.unlink()
        deleted_count += 1

    return deleted_count


async def ensure_session_table(db_path: str | Path | None = None) -> None:
    """用一张轻量元数据表记录会话入口，不介入 LangGraph 的官方 checkpoint 表。"""
    if db_path is None and settings.database_backend == "postgres":
        await _postgres_ensure_session_table()
        return

    db_file = resolve_memory_db_path(db_path)
    async with aiosqlite.connect(str(db_file)) as conn:
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SESSION_TABLE} (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                preview TEXT NOT NULL DEFAULT '',
                message_count INTEGER NOT NULL DEFAULT 0,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        await conn.commit()


async def _postgres_ensure_session_table() -> None:
    """PostgreSQL 复用当前会话元数据结构，避免迁移时改变前端历史列表语义。"""
    if not settings.database_url:
        raise ValueError("TRPG_DATABASE_URL is required when TRPG_DATABASE_BACKEND=postgres.")

    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SESSION_TABLE} (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                preview TEXT NOT NULL DEFAULT '',
                message_count INTEGER NOT NULL DEFAULT 0,
                created_at_ms BIGINT NOT NULL,
                updated_at_ms BIGINT NOT NULL
            )
            """
        )


async def create_chat_session(title: str | None = None, db_path: str | Path | None = None) -> dict:
    """创建一个空白会话 ID，让前端显式切到干净的 thread。"""
    await ensure_session_table(db_path)
    now = _now_ms()
    session_id = str(uuid4())
    resolved_title = title.strip() if title and title.strip() else "新的冒险"

    if db_path is None and settings.database_backend == "postgres":
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            await conn.execute(
                f"""
                INSERT INTO {SESSION_TABLE}(id, title, preview, message_count, created_at_ms, updated_at_ms)
                VALUES (%s, %s, '', 0, %s, %s)
                """,
                (session_id, resolved_title, now, now),
            )
        return {
            "id": session_id,
            "title": resolved_title,
            "preview": "",
            "messageCount": 0,
            "createdAt": now,
            "lastMessageAt": now,
        }

    db_file = resolve_memory_db_path(db_path)

    async with aiosqlite.connect(str(db_file)) as conn:
        await conn.execute(
            f"""
            INSERT INTO {SESSION_TABLE}(id, title, preview, message_count, created_at_ms, updated_at_ms)
            VALUES (?, ?, '', 0, ?, ?)
            """,
            (session_id, resolved_title, now, now),
        )
        await conn.commit()

    return {
        "id": session_id,
        "title": resolved_title,
        "preview": "",
        "messageCount": 0,
        "createdAt": now,
        "lastMessageAt": now,
    }


async def touch_chat_session(
    *,
    session_id: str,
    message: str | None,
    reply: str | None,
    db_path: str | Path | None = None,
) -> None:
    """在完成一轮交互后更新会话摘要，避免历史页依赖 trace 或 checkpoint 内部结构。"""
    await ensure_session_table(db_path)
    now = _now_ms()
    preview = _build_preview(message, reply)

    if db_path is None and settings.database_backend == "postgres":
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            cursor = await conn.execute(
                f"SELECT title, message_count FROM {SESSION_TABLE} WHERE id = %s",
                (session_id,),
            )
            row = await cursor.fetchone()

            if row is None:
                await conn.execute(
                    f"""
                    INSERT INTO {SESSION_TABLE}(id, title, preview, message_count, created_at_ms, updated_at_ms)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (session_id, _build_title(message), preview, 1 if message else 0, now, now),
                )
            else:
                title, message_count = row
                next_title = _build_title(message) if title == "新的冒险" and message else title
                await conn.execute(
                    f"""
                    UPDATE {SESSION_TABLE}
                    SET title = %s, preview = %s, message_count = %s, updated_at_ms = %s
                    WHERE id = %s
                    """,
                    (next_title, preview, int(message_count) + (1 if message else 0), now, session_id),
                )
        return

    db_file = resolve_memory_db_path(db_path)

    async with aiosqlite.connect(str(db_file)) as conn:
        cursor = await conn.execute(
            f"SELECT title, message_count FROM {SESSION_TABLE} WHERE id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()

        if row is None:
            await conn.execute(
                f"""
                INSERT INTO {SESSION_TABLE}(id, title, preview, message_count, created_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, _build_title(message), preview, 1 if message else 0, now, now),
            )
        else:
            title, message_count = row
            next_title = _build_title(message) if title == "新的冒险" and message else title
            await conn.execute(
                f"""
                UPDATE {SESSION_TABLE}
                SET title = ?, preview = ?, message_count = ?, updated_at_ms = ?
                WHERE id = ?
                """,
                (next_title, preview, int(message_count) + (1 if message else 0), now, session_id),
            )

        await conn.commit()


async def list_chat_sessions(db_path: str | Path | None = None) -> list[dict]:
    """按最近活动时间列出可恢复会话，并顺手清理旧测试或删除残留的元数据孤儿。"""
    await ensure_session_table(db_path)
    if db_path is None and settings.database_backend == "postgres":
        rows = await _postgres_list_live_session_rows()
        return _format_session_rows(rows)

    db_file = resolve_memory_db_path(db_path)

    async with aiosqlite.connect(str(db_file)) as conn:
        cursor = await conn.execute(
            f"""
            SELECT id, title, preview, message_count, created_at_ms, updated_at_ms
            FROM {SESSION_TABLE}
            ORDER BY updated_at_ms DESC
            """
        )
        rows = await cursor.fetchall()
        await cursor.close()

        live_rows = []
        orphan_session_ids = []
        for row in rows:
            session_id = row[0]
            if await _has_thread_checkpoint(conn, session_id):
                live_rows.append(row)
            else:
                orphan_session_ids.append(session_id)

        live_session_ids = [row[0] for row in live_rows]
        for session_id in orphan_session_ids:
            await conn.execute(f"DELETE FROM {SESSION_TABLE} WHERE id = ?", (session_id,))

        deleted_storage_rows = await prune_orphan_session_storage(conn, live_session_ids)
        if orphan_session_ids or deleted_storage_rows:
            await conn.commit()

    return _format_session_rows(live_rows)


async def _postgres_list_live_session_rows() -> list[tuple]:
    if not settings.database_url:
        raise ValueError("TRPG_DATABASE_URL is required when TRPG_DATABASE_BACKEND=postgres.")

    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        cursor = await conn.execute(
            f"""
            SELECT id, title, preview, message_count, created_at_ms, updated_at_ms
            FROM {SESSION_TABLE}
            ORDER BY updated_at_ms DESC
            """
        )
        rows = await cursor.fetchall()

        live_rows = []
        orphan_session_ids = []
        for row in rows:
            session_id = row[0]
            if await _postgres_has_thread_checkpoint(conn, session_id):
                live_rows.append(row)
            else:
                orphan_session_ids.append(session_id)

        live_session_ids = [row[0] for row in live_rows]
        for session_id in orphan_session_ids:
            await conn.execute(f"DELETE FROM {SESSION_TABLE} WHERE id = %s", (session_id,))

        await _postgres_prune_orphan_session_storage(conn, live_session_ids)

    return live_rows


def _format_session_rows(rows: list[tuple]) -> list[dict]:
    return [
        {
            "id": row[0],
            "title": row[1],
            "preview": row[2],
            "messageCount": row[3],
            "createdAt": row[4],
            "lastMessageAt": row[5],
        }
        for row in rows
    ]


async def prune_orphan_session_storage(conn: aiosqlite.Connection, live_session_ids: list[str]) -> int:
    """以历史元数据为会话白名单，清理无法从前端恢复的 SQLite 残留。"""
    deleted_rows = 0
    placeholders = ", ".join("?" for _ in live_session_ids)

    if await _table_exists(conn, "checkpoints"):
        sql = "DELETE FROM checkpoints" if not live_session_ids else f"DELETE FROM checkpoints WHERE thread_id NOT IN ({placeholders})"
        result = await conn.execute(sql, tuple(live_session_ids))
        deleted_rows += result.rowcount if result.rowcount > 0 else 0

    if await _table_exists(conn, "writes"):
        sql = "DELETE FROM writes" if not live_session_ids else f"DELETE FROM writes WHERE thread_id NOT IN ({placeholders})"
        result = await conn.execute(sql, tuple(live_session_ids))
        deleted_rows += result.rowcount if result.rowcount > 0 else 0

    if await _table_exists(conn, "episodic_memory"):
        sql = "DELETE FROM episodic_memory" if not live_session_ids else f"DELETE FROM episodic_memory WHERE session_id NOT IN ({placeholders})"
        result = await conn.execute(sql, tuple(live_session_ids))
        deleted_rows += result.rowcount if result.rowcount > 0 else 0

    return deleted_rows


async def _postgres_prune_orphan_session_storage(conn: psycopg.AsyncConnection, live_session_ids: list[str]) -> int:
    """PostgreSQL 清理逻辑只碰会话派生表，trace 仍交给文件系统路径处理。"""
    deleted_rows = 0

    for table in POSTGRES_SESSION_STORAGE_TABLES:
        if not await _postgres_table_exists(conn, table):
            continue

        if live_session_ids:
            result = await conn.execute(
                f"DELETE FROM {table} WHERE thread_id <> ALL(%s)"
                if table in POSTGRES_CHECKPOINT_TABLES
                else f"DELETE FROM {table} WHERE session_id <> ALL(%s)",
                (live_session_ids,),
            )
        else:
            result = await conn.execute(f"DELETE FROM {table}")
        deleted_rows += result.rowcount if result.rowcount > 0 else 0

    return deleted_rows


async def purge_chat_session_data(session_id: str, db_path: str | Path | None = None) -> dict[str, int]:
    """按会话 ID 清理所有持久化记录，避免旧 thread、派生记忆和 trace 互相污染。"""
    if db_path is None and settings.database_backend == "postgres":
        deleted_rows = await _postgres_purge_chat_session_data(session_id)
        deleted_trace_files = _delete_trace_file(session_id)
        return {"deletedRows": deleted_rows, "deletedTraceFiles": deleted_trace_files}

    db_file = resolve_memory_db_path(db_path)
    deleted_rows = 0

    async with aiosqlite.connect(str(db_file)) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [row[0] for row in await cursor.fetchall()]
        await cursor.close()

        for table in tables:
            column_cursor = await conn.execute(f"PRAGMA table_info({_quote_identifier(table)})")
            columns = {row[1] for row in await column_cursor.fetchall()}
            await column_cursor.close()

            if table == SESSION_TABLE:
                result = await conn.execute(
                    f"DELETE FROM {SESSION_TABLE} WHERE id = ?",
                    (session_id,),
                )
            elif "thread_id" in columns:
                result = await conn.execute(
                    f"DELETE FROM {_quote_identifier(table)} WHERE thread_id = ?",
                    (session_id,),
                )
            elif "session_id" in columns:
                result = await conn.execute(
                    f"DELETE FROM {_quote_identifier(table)} WHERE session_id = ?",
                    (session_id,),
                )
            else:
                continue

            deleted_rows += result.rowcount if result.rowcount > 0 else 0

        await conn.commit()

    deleted_trace_files = _delete_trace_file(session_id)

    return {"deletedRows": deleted_rows, "deletedTraceFiles": deleted_trace_files}


async def _postgres_purge_chat_session_data(session_id: str) -> int:
    if not settings.database_url:
        raise ValueError("TRPG_DATABASE_URL is required when TRPG_DATABASE_BACKEND=postgres.")

    deleted_rows = 0
    async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        if await _postgres_table_exists(conn, SESSION_TABLE):
            result = await conn.execute(f"DELETE FROM {SESSION_TABLE} WHERE id = %s", (session_id,))
            deleted_rows += result.rowcount if result.rowcount > 0 else 0

        for table in POSTGRES_CHECKPOINT_TABLES:
            if await _postgres_table_exists(conn, table):
                result = await conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (session_id,))
                deleted_rows += result.rowcount if result.rowcount > 0 else 0

        if await _postgres_table_exists(conn, "episodic_memory"):
            result = await conn.execute("DELETE FROM episodic_memory WHERE session_id = %s", (session_id,))
            deleted_rows += result.rowcount if result.rowcount > 0 else 0

    return deleted_rows
