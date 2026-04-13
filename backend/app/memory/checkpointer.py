"""Checkpoint factory backed by LangGraph official AsyncSqliteSaver."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


_CHECKPOINTER: AsyncSqliteSaver | None = None
_CHECKPOINTER_LOCK = asyncio.Lock()

async def get_checkpointer(db_path: str) -> AsyncSqliteSaver:
    """在事件循环内惰性初始化单例 checkpointer，避免同步/异步接口混用。"""
    global _CHECKPOINTER

    if _CHECKPOINTER is not None:
        return _CHECKPOINTER

    async with _CHECKPOINTER_LOCK:
        if _CHECKPOINTER is not None:
            return _CHECKPOINTER

        db_file = Path(db_path)
        if not db_file.is_absolute():
            db_file = Path.cwd() / db_file
        db_file.parent.mkdir(parents=True, exist_ok=True)

        conn = await aiosqlite.connect(str(db_file))
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA synchronous = NORMAL")
        await conn.commit()

        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        _CHECKPOINTER = saver
        return saver


async def close_checkpointer() -> None:
    """在应用关闭时主动释放 SQLite 异步连接，避免进程悬挂。"""
    global _CHECKPOINTER

    if _CHECKPOINTER is None:
        return

    async with _CHECKPOINTER_LOCK:
        if _CHECKPOINTER is None:
            return

        await _CHECKPOINTER.conn.close()
        _CHECKPOINTER = None

