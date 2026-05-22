"""统一解析后端持久化文件路径。"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import settings


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def resolve_memory_db_path(db_path: str | Path | None = None) -> Path:
    """把记忆数据库固定到 backend 根目录下，避免启动目录不同生成多份 SQLite。"""
    resolved = Path(db_path or settings.memory_db_path)
    if not resolved.is_absolute():
        resolved = BACKEND_ROOT / resolved
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved
