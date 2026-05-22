"""Checkpoint factory backed by LangGraph official checkpoint savers."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.utils.storage_paths import resolve_memory_db_path

from app.config.settings import Settings, settings
from app.utils.asyncio_policy import configure_windows_selector_event_loop_policy


configure_windows_selector_event_loop_policy()

_CHECKPOINTER: BaseCheckpointSaver | None = None
_CHECKPOINTER_CONTEXT: AbstractAsyncContextManager[Any] | None = None
_CHECKPOINTER_KEY: tuple[str, str] | None = None
_CHECKPOINTER_LOCK = asyncio.Lock()


async def get_checkpointer(config: Settings | str | None = None) -> BaseCheckpointSaver:
    """按配置惰性初始化官方 checkpointer，避免业务层感知具体数据库。"""
    global _CHECKPOINTER, _CHECKPOINTER_KEY

    backend, target = _resolve_checkpointer_target(config)
    key = (backend, target)

    if _CHECKPOINTER is not None and _CHECKPOINTER_KEY == key:
        return _CHECKPOINTER

    async with _CHECKPOINTER_LOCK:
        if _CHECKPOINTER is not None and _CHECKPOINTER_KEY == key:
            return _CHECKPOINTER

        if _CHECKPOINTER is not None:
            await _close_checkpointer_unlocked()

        saver = await _open_postgres_checkpointer(target) if backend == "postgres" else await _open_sqlite_checkpointer(target)
        _CHECKPOINTER = saver
        _CHECKPOINTER_KEY = key
        return saver


async def close_checkpointer() -> None:
    """在应用关闭时主动释放持久化连接，避免进程悬挂。"""

    if _CHECKPOINTER is None:
        return

    async with _CHECKPOINTER_LOCK:
        if _CHECKPOINTER is None:
            return

        await _close_checkpointer_unlocked()

def _resolve_checkpointer_target(config: Settings | str | None) -> tuple[str, str]:
    """兼容旧调用传 db_path，同时让新路径按 Settings 选择数据库后端。"""
    if isinstance(config, str):
        return "sqlite", config

    active_settings = config or settings
    if active_settings.database_backend == "postgres":
        if not active_settings.database_url:
            raise ValueError("TRPG_DATABASE_URL is required when TRPG_DATABASE_BACKEND=postgres.")
        return "postgres", active_settings.database_url
    return "sqlite", active_settings.memory_db_path


async def _open_sqlite_checkpointer(db_path: str) -> AsyncSqliteSaver:
    """SQLite 继续使用现有 WAL 配置，保证默认本地运行行为不变。"""
    db_file = Path(db_path)
    if not db_file.is_absolute():
        db_file = Path.cwd() / db_file
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(db_file))
    if not hasattr(conn, "is_alive"):
        # LangGraph 的新版 SQLite saver 会探测连接状态；当前锁定的 aiosqlite 版本尚未提供该方法。
        conn.is_alive = lambda: True  # type: ignore[attr-defined]
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA synchronous = NORMAL")
    await conn.commit()

    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return saver


async def _open_postgres_checkpointer(database_url: str) -> BaseCheckpointSaver:
    """PostgreSQL 使用 LangGraph 官方 saver，避免自己维护 checkpoint 表结构。"""
    global _CHECKPOINTER_CONTEXT

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    context = AsyncPostgresSaver.from_conn_string(database_url)
    saver = await context.__aenter__()
    await saver.setup()
    _CHECKPOINTER_CONTEXT = context
    return saver


async def _close_checkpointer_unlocked() -> None:
    """调用方已持有锁；根据 saver 来源释放对应资源。"""
    global _CHECKPOINTER, _CHECKPOINTER_CONTEXT, _CHECKPOINTER_KEY

    if _CHECKPOINTER_CONTEXT is not None:
        await _CHECKPOINTER_CONTEXT.__aexit__(None, None, None)
    elif isinstance(_CHECKPOINTER, AsyncSqliteSaver):
        await _CHECKPOINTER.conn.close()
        _CHECKPOINTER = None


    _CHECKPOINTER = None
    _CHECKPOINTER_CONTEXT = None
    _CHECKPOINTER_KEY = None
