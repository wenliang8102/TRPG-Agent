from pathlib import Path

import pytest

from app.config.settings import Settings
from app.memory.checkpointer import close_checkpointer, get_checkpointer


@pytest.fixture
def anyio_backend() -> str:
    """这组测试只验证 asyncio 路径，避免 AnyIO 切到 trio 后卡住 SQLite/Graph 句柄。"""
    return "asyncio"


@pytest.mark.anyio
async def test_sqlite_checkpointer_remains_explicit_fallback(tmp_path: Path):
    """SQLite 仅作为显式 fallback 保留，方便读取旧归档或做轻量隔离测试。"""
    db_path = tmp_path / "memory.sqlite3"

    try:
        saver = await get_checkpointer(str(db_path))

        assert saver is not None
        assert db_path.exists()
    finally:
        await close_checkpointer()


@pytest.mark.anyio
async def test_postgres_checkpointer_requires_database_url(monkeypatch):
    """PostgreSQL 模式下 URL 为空要快速失败，避免运行数据位置不明确。"""
    monkeypatch.setenv("TRPG_DATABASE_BACKEND", "postgres")
    monkeypatch.setenv("TRPG_DATABASE_URL", "")
    settings = Settings()

    with pytest.raises(ValueError, match="TRPG_DATABASE_URL"):
        await get_checkpointer(settings)

    await close_checkpointer()
