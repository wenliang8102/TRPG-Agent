"""将本地 SQLite 持久化数据迁移到 PostgreSQL。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import psycopg


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.memory.checkpointer import close_checkpointer, get_checkpointer  # noqa: E402
from app.utils.asyncio_policy import configure_windows_selector_event_loop_policy  # noqa: E402


SESSION_TABLE = "app_chat_sessions"
EPISODIC_TABLE = "episodic_memory"
SQLITE_CHECKPOINT_TABLES = ("checkpoints", "writes")
POSTGRES_CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


@dataclass(slots=True)
class MigrationReport:
    """记录迁移结果，方便人工判断哪些数据已进入 PostgreSQL。"""

    sqlite_path: Path
    postgres_url: str
    sessions_seen: int = 0
    sessions_migrated: int = 0
    episodic_seen: int = 0
    episodic_migrated: int = 0
    sqlite_checkpoint_rows: int = 0
    checkpoint_migrated: int = 0
    checkpoint_skipped_reason: str = ""
    failures: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "SQLite -> PostgreSQL migration report",
            f"source_sqlite={self.sqlite_path}",
            f"target_postgres={mask_database_url(self.postgres_url)}",
            f"sessions_seen={self.sessions_seen}",
            f"sessions_migrated={self.sessions_migrated}",
            f"episodic_memory_seen={self.episodic_seen}",
            f"episodic_memory_migrated={self.episodic_migrated}",
            f"sqlite_checkpoint_rows={self.sqlite_checkpoint_rows}",
            f"checkpoint_migrated={self.checkpoint_migrated}",
            f"checkpoint_skipped_reason={self.checkpoint_skipped_reason or 'none'}",
        ]
        if self.failures:
            lines.append("failures:")
            lines.extend(f"- {item}" for item in self.failures)
        else:
            lines.append("failures=none")
        return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate TRPG-Agent app-level SQLite data into PostgreSQL.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=os.getenv("TRPG_MEMORY_DB_PATH", "backend/data/context_memory.sqlite3"),
        help="源 SQLite 文件路径，默认读取 TRPG_MEMORY_DB_PATH 或 backend/data/context_memory.sqlite3。",
    )
    parser.add_argument(
        "--postgres-url",
        default=os.getenv("TRPG_DATABASE_URL"),
        help="目标 PostgreSQL URL，默认读取 TRPG_DATABASE_URL。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查与统计 app 层数据，不迁移 app 表数据；仍会按官方 saver 初始化 checkpoint schema。",
    )
    return parser.parse_args()


def mask_database_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.password:
        return url
    username = parts.username or ""
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{username}:***@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def resolve_sqlite_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def postgres_table_exists(conn: psycopg.Connection, table: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
    return bool(row and row[0])


async def initialize_official_postgres_checkpoint(postgres_url: str) -> None:
    """先让 LangGraph 官方 saver 创建 checkpoint schema，避免脚本手写内部结构。"""
    configure_windows_selector_event_loop_policy()
    saver = await get_checkpointer(_settings_for_postgres(postgres_url))
    if saver is None:
        raise RuntimeError("failed to initialize official Postgres checkpointer")
    await close_checkpointer()


def _settings_for_postgres(postgres_url: str):
    return SimpleNamespace(
        database_backend="postgres",
        database_url=postgres_url,
        memory_db_path="",
    )


def ensure_app_tables(conn: psycopg.Connection) -> None:
    """创建当前 app 层真实表结构，不在迁移阶段改变业务语义。"""
    conn.execute(
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
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {EPISODIC_TABLE} (
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
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_episodic_memory_session_created ON {EPISODIC_TABLE}(session_id, created_at DESC, id DESC)"
    )


def migrate_sessions(sqlite_conn: sqlite3.Connection, pg_conn: psycopg.Connection, report: MigrationReport, dry_run: bool) -> None:
    """迁移会话元数据；重复执行时按 id 更新最新值。"""
    if not sqlite_table_exists(sqlite_conn, SESSION_TABLE):
        return

    rows = sqlite_conn.execute(
        f"""
        SELECT id, title, preview, message_count, created_at_ms, updated_at_ms
        FROM {SESSION_TABLE}
        """
    ).fetchall()
    report.sessions_seen = len(rows)
    if dry_run:
        return

    with pg_conn.cursor() as cursor:
        for row in rows:
            cursor.execute(
                f"""
                INSERT INTO {SESSION_TABLE}(id, title, preview, message_count, created_at_ms, updated_at_ms)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(id)
                DO UPDATE SET
                    title = excluded.title,
                    preview = excluded.preview,
                    message_count = excluded.message_count,
                    created_at_ms = excluded.created_at_ms,
                    updated_at_ms = excluded.updated_at_ms
                """,
                row,
            )
            report.sessions_migrated += 1


def migrate_episodic_memory(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg.Connection,
    report: MigrationReport,
    dry_run: bool,
) -> None:
    """迁移 append-only 情节记忆，保留 turn_id 与 record_kind 的覆盖语义。"""
    if not sqlite_table_exists(sqlite_conn, EPISODIC_TABLE):
        return

    rows = sqlite_conn.execute(
        f"""
        SELECT session_id, turn_id, record_kind, payload_json, created_at
        FROM {EPISODIC_TABLE}
        ORDER BY id ASC
        """
    ).fetchall()
    report.episodic_seen = len(rows)
    if dry_run:
        return

    with pg_conn.cursor() as cursor:
        for row in rows:
            cursor.execute(
                f"""
                INSERT INTO {EPISODIC_TABLE}(session_id, turn_id, record_kind, payload_json, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT(session_id, turn_id, record_kind)
                DO UPDATE SET
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                row,
            )
            report.episodic_migrated += 1


def count_sqlite_checkpoint_rows(sqlite_conn: sqlite3.Connection) -> int:
    total = 0
    for table in SQLITE_CHECKPOINT_TABLES:
        if sqlite_table_exists(sqlite_conn, table):
            total += int(sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return total


def evaluate_checkpoint_migration(sqlite_conn: sqlite3.Connection, pg_conn: psycopg.Connection, report: MigrationReport) -> None:
    """统计 checkpoint 规模并确认目标 schema 已由官方 saver 初始化。"""
    report.sqlite_checkpoint_rows = count_sqlite_checkpoint_rows(sqlite_conn)
    if report.sqlite_checkpoint_rows == 0:
        report.checkpoint_skipped_reason = "source has no checkpoint rows"
        return

    missing_pg_tables = [table for table in POSTGRES_CHECKPOINT_TABLES if not postgres_table_exists(pg_conn, table)]
    if missing_pg_tables:
        report.checkpoint_skipped_reason = f"target checkpoint schema missing tables: {', '.join(missing_pg_tables)}"
        return

    report.checkpoint_skipped_reason = (
        "old LangGraph checkpoint runtime state intentionally skipped; "
        "new PostgreSQL sessions will use the official Postgres saver"
    )


def migrate(sqlite_path: Path, postgres_url: str, *, dry_run: bool) -> MigrationReport:
    report = MigrationReport(sqlite_path=sqlite_path, postgres_url=postgres_url)
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite source not found: {sqlite_path}")

    sqlite_conn = sqlite3.connect(sqlite_path)
    try:
        with psycopg.connect(postgres_url) as pg_conn:
            ensure_app_tables(pg_conn)
            migrate_sessions(sqlite_conn, pg_conn, report, dry_run)
            migrate_episodic_memory(sqlite_conn, pg_conn, report, dry_run)
            evaluate_checkpoint_migration(sqlite_conn, pg_conn, report)
            if dry_run:
                pg_conn.rollback()
            else:
                pg_conn.commit()
    except Exception as exc:
        report.failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        sqlite_conn.close()

    return report


async def async_main() -> int:
    args = parse_args()
    if not args.postgres_url:
        print("TRPG_DATABASE_URL or --postgres-url is required.", file=sys.stderr)
        return 2

    sqlite_path = resolve_sqlite_path(args.sqlite_path)
    await initialize_official_postgres_checkpoint(args.postgres_url)
    report = migrate(sqlite_path, args.postgres_url, dry_run=args.dry_run)
    print(report.render())
    return 1 if report.failures else 0


def main() -> int:
    configure_windows_selector_event_loop_policy()
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
