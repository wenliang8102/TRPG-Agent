"""SQLite -> PostgreSQL 迁移脚本集成测试；默认跳过，设置 TRPG_TEST_POSTGRES_URL 后运行。"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import psycopg
import pytest


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

POSTGRES_URL = os.getenv("TRPG_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TRPG_TEST_POSTGRES_URL is not set")

from scripts.migrate_sqlite_to_postgres import initialize_official_postgres_checkpoint, migrate  # noqa: E402


SESSION_IDS = ("mig-it-session-1", "mig-it-session-2")


def _table_exists(conn: psycopg.Connection, table: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
    return bool(row and row[0])


def _delete_migration_test_rows(postgres_url: str) -> None:
    """只清理本测试前缀的数据，避免影响人工迁移验证库。"""
    with psycopg.connect(postgres_url) as conn:
        if _table_exists(conn, "app_chat_sessions"):
            conn.execute("DELETE FROM app_chat_sessions WHERE id LIKE 'mig-it-%'")
        if _table_exists(conn, "episodic_memory"):
            conn.execute("DELETE FROM episodic_memory WHERE session_id LIKE 'mig-it-%'")
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            if _table_exists(conn, table):
                conn.execute(f"DELETE FROM {table} WHERE thread_id LIKE 'mig-it-%'")


def _build_sqlite_source(sqlite_path: Path) -> None:
    """构造最小真实 SQLite 源库，覆盖 app 表和 checkpoint 统计路径。"""
    with sqlite3.connect(sqlite_path) as conn:
        conn.execute(
            """
            CREATE TABLE app_chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                preview TEXT NOT NULL DEFAULT '',
                message_count INTEGER NOT NULL DEFAULT 0,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE episodic_memory (
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
        conn.execute("CREATE TABLE checkpoints(thread_id TEXT NOT NULL, checkpoint TEXT NOT NULL)")
        conn.execute("CREATE TABLE writes(thread_id TEXT NOT NULL, value TEXT NOT NULL)")

        conn.execute(
            """
            INSERT INTO app_chat_sessions(id, title, preview, message_count, created_at_ms, updated_at_ms)
            VALUES
                ('mig-it-session-1', '洞口调查', '你发现了新鲜脚印。', 1, 1000, 2000),
                ('mig-it-session-2', '林地伏击', '短弓声从树后响起。', 2, 1100, 2100)
            """
        )
        conn.execute(
            """
            INSERT INTO episodic_memory(session_id, turn_id, record_kind, payload_json, created_at)
            VALUES
                ('mig-it-session-1', 'turn-1', 'turn_summary', '{"summary": "发现脚印。"}', '2026-05-16T10:00:00'),
                ('mig-it-session-2', 'turn-1', 'turn_summary', '{"summary": "遭遇伏击。"}', '2026-05-16T10:01:00')
            """
        )
        conn.execute("INSERT INTO checkpoints(thread_id, checkpoint) VALUES ('mig-it-session-1', '{}')")
        conn.execute("INSERT INTO writes(thread_id, value) VALUES ('mig-it-session-1', '{}')")


def _count_rows(postgres_url: str, table: str, column: str) -> int:
    with psycopg.connect(postgres_url) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE 'mig-it-%'").fetchone()[0])


def test_migration_script_dry_run_and_app_table_migration(tmp_path: Path):
    """阶段 5 验收：迁移脚本报告准确，app 数据可重复写入，checkpoint 默认不强迁。"""
    assert POSTGRES_URL is not None
    sqlite_path = tmp_path / "context_memory.sqlite3"
    _build_sqlite_source(sqlite_path)
    _delete_migration_test_rows(POSTGRES_URL)

    try:
        asyncio.run(initialize_official_postgres_checkpoint(POSTGRES_URL))

        dry_report = migrate(sqlite_path, POSTGRES_URL, dry_run=True)
        assert dry_report.failures == []
        assert dry_report.sessions_seen == 2
        assert dry_report.sessions_migrated == 0
        assert dry_report.episodic_seen == 2
        assert dry_report.episodic_migrated == 0
        assert dry_report.sqlite_checkpoint_rows == 2
        assert "old LangGraph checkpoint runtime state intentionally skipped" in dry_report.checkpoint_skipped_reason
        assert _count_rows(POSTGRES_URL, "app_chat_sessions", "id") == 0
        assert _count_rows(POSTGRES_URL, "episodic_memory", "session_id") == 0

        report = migrate(sqlite_path, POSTGRES_URL, dry_run=False)
        assert report.failures == []
        assert report.sessions_seen == 2
        assert report.sessions_migrated == 2
        assert report.episodic_seen == 2
        assert report.episodic_migrated == 2
        assert report.sqlite_checkpoint_rows == 2
        assert report.checkpoint_migrated == 0
        assert "old LangGraph checkpoint runtime state intentionally skipped" in report.checkpoint_skipped_reason

        with psycopg.connect(POSTGRES_URL) as conn:
            session_rows = conn.execute(
                """
                SELECT id, title, preview, message_count, created_at_ms, updated_at_ms
                FROM app_chat_sessions
                WHERE id LIKE 'mig-it-%'
                ORDER BY id
                """
            ).fetchall()
            memory_rows = conn.execute(
                """
                SELECT session_id, turn_id, record_kind, payload_json
                FROM episodic_memory
                WHERE session_id LIKE 'mig-it-%'
                ORDER BY session_id
                """
            ).fetchall()

        assert session_rows == [
            ("mig-it-session-1", "洞口调查", "你发现了新鲜脚印。", 1, 1000, 2000),
            ("mig-it-session-2", "林地伏击", "短弓声从树后响起。", 2, 1100, 2100),
        ]
        assert memory_rows == [
            ("mig-it-session-1", "turn-1", "turn_summary", '{"summary": "发现脚印。"}'),
            ("mig-it-session-2", "turn-1", "turn_summary", '{"summary": "遭遇伏击。"}'),
        ]
    finally:
        _delete_migration_test_rows(POSTGRES_URL)
