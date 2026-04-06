"""Checkpoint factory backed by LangGraph official SqliteSaver."""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


@lru_cache(maxsize=1)
def get_checkpointer(db_path: str) -> SqliteSaver:
    db_file = Path(db_path)
    if not db_file.is_absolute():
        db_file = Path.cwd() / db_file
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    
    saver = SqliteSaver(conn)
    saver.setup()
    return saver

