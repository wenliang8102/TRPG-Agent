import sqlite3
import unittest
from os import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.services.session_store import (
    create_chat_session,
    list_chat_sessions,
    purge_chat_session_data,
    touch_chat_session,
)
from app.utils.agent_trace import resolve_trace_file
from app.utils.storage_paths import BACKEND_ROOT, resolve_memory_db_path


class SessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_relative_memory_db_path_is_resolved_from_backend_root(self):
        old_cwd = Path.cwd()
        with TemporaryDirectory() as temp_dir:
            try:
                chdir(temp_dir)

                resolved = resolve_memory_db_path("data/test-context.sqlite3")
            finally:
                chdir(old_cwd)

        self.assertEqual(BACKEND_ROOT / "data" / "test-context.sqlite3", resolved)

    async def test_session_metadata_is_created_and_updated(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.sqlite3"

            session = await create_chat_session(db_path=db_path)
            await touch_chat_session(
                session_id=session["id"],
                message="调查洞口",
                reply="你发现了新鲜脚印。",
                db_path=db_path,
            )

            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE checkpoints(thread_id TEXT, checkpoint TEXT)")
            conn.execute("INSERT INTO checkpoints VALUES (?, ?)", (session["id"], "{}"))
            conn.commit()
            conn.close()

            sessions = await list_chat_sessions(db_path=db_path)

        self.assertEqual(session["id"], sessions[0]["id"])
        self.assertEqual("调查洞口", sessions[0]["title"])
        self.assertEqual("你发现了新鲜脚印。", sessions[0]["preview"])
        self.assertEqual(1, sessions[0]["messageCount"])

    async def test_purge_session_removes_checkpoint_memory_and_trace_only_for_target(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.sqlite3"
            trace_dir = Path(temp_dir) / "agent_traces"
            target_trace = resolve_trace_file("target", trace_dir)
            other_trace = resolve_trace_file("other", trace_dir)
            target_trace.write_text("target trace", encoding="utf-8")
            other_trace.write_text("other trace", encoding="utf-8")

            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE checkpoints(thread_id TEXT, checkpoint TEXT)")
            conn.execute("CREATE TABLE writes(thread_id TEXT, value TEXT)")
            conn.execute("CREATE TABLE episodic_memory(session_id TEXT, payload_json TEXT)")
            conn.execute("CREATE TABLE app_chat_sessions(id TEXT PRIMARY KEY, title TEXT, preview TEXT, message_count INTEGER, created_at_ms INTEGER, updated_at_ms INTEGER)")
            for value in ("target", "other"):
                conn.execute("INSERT INTO checkpoints VALUES (?, ?)", (value, "{}"))
                conn.execute("INSERT INTO writes VALUES (?, ?)", (value, "{}"))
                conn.execute("INSERT INTO episodic_memory VALUES (?, ?)", (value, "{}"))
                conn.execute("INSERT INTO app_chat_sessions VALUES (?, 't', '', 0, 1, 1)", (value,))
            conn.commit()
            conn.close()

            with patch("app.services.session_store.resolve_trace_file", lambda session_id: resolve_trace_file(session_id, trace_dir)):
                result = await purge_chat_session_data("target", db_path=db_path)

            conn = sqlite3.connect(db_path)
            target_count = sum(
                conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = 'target'").fetchone()[0]
                for table, column in (
                    ("checkpoints", "thread_id"),
                    ("writes", "thread_id"),
                    ("episodic_memory", "session_id"),
                    ("app_chat_sessions", "id"),
                )
            )
            other_count = sum(
                conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = 'other'").fetchone()[0]
                for table, column in (
                    ("checkpoints", "thread_id"),
                    ("writes", "thread_id"),
                    ("episodic_memory", "session_id"),
                    ("app_chat_sessions", "id"),
                )
            )
            conn.close()

            self.assertEqual(0, target_count)
            self.assertEqual(4, other_count)
            self.assertEqual({"deletedRows": 4, "deletedTraceFiles": 1}, result)
            self.assertFalse(target_trace.exists())
            self.assertTrue(other_trace.exists())

    async def test_list_sessions_prunes_orphan_metadata_without_deleting_trace_files(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.sqlite3"
            trace_dir = Path(temp_dir) / "agent_traces"
            live_trace = resolve_trace_file("live", trace_dir)
            orphan_trace = resolve_trace_file("orphan", trace_dir)
            live_trace.write_text("live trace", encoding="utf-8")
            orphan_trace.write_text("orphan trace", encoding="utf-8")

            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE checkpoints(thread_id TEXT, checkpoint TEXT)")
            conn.execute("CREATE TABLE app_chat_sessions(id TEXT PRIMARY KEY, title TEXT, preview TEXT, message_count INTEGER, created_at_ms INTEGER, updated_at_ms INTEGER)")
            conn.execute("INSERT INTO checkpoints VALUES ('live', '{}')")
            conn.execute("INSERT INTO app_chat_sessions VALUES ('live', 'live title', '', 1, 1, 2)")
            conn.execute("INSERT INTO app_chat_sessions VALUES ('orphan', 'orphan title', '', 1, 1, 3)")
            conn.commit()
            conn.close()

            with patch("app.services.session_store.resolve_trace_file", lambda session_id: resolve_trace_file(session_id, trace_dir)):
                sessions = await list_chat_sessions(db_path=db_path)

            conn = sqlite3.connect(db_path)
            remaining_ids = [
                row[0]
                for row in conn.execute("SELECT id FROM app_chat_sessions ORDER BY id").fetchall()
            ]
            conn.close()

            self.assertEqual(["live"], [session["id"] for session in sessions])
            self.assertEqual(["live"], remaining_ids)
            self.assertTrue(live_trace.exists())
            self.assertTrue(orphan_trace.exists())

    async def test_list_sessions_preserves_trace_files_without_live_sessions(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.sqlite3"
            trace_dir = Path(temp_dir) / "agent_traces"
            stale_trace = resolve_trace_file("stale", trace_dir)
            stale_trace.write_text("stale trace", encoding="utf-8")

            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE checkpoints(thread_id TEXT, checkpoint TEXT)")
            conn.execute("CREATE TABLE app_chat_sessions(id TEXT PRIMARY KEY, title TEXT, preview TEXT, message_count INTEGER, created_at_ms INTEGER, updated_at_ms INTEGER)")
            conn.commit()
            conn.close()

            with patch("app.services.session_store.resolve_trace_file", lambda session_id: resolve_trace_file(session_id, trace_dir)):
                sessions = await list_chat_sessions(db_path=db_path)

            self.assertEqual([], sessions)
            self.assertTrue(stale_trace.exists())

    async def test_list_sessions_prunes_orphan_sqlite_storage(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.sqlite3"

            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE checkpoints(thread_id TEXT, checkpoint TEXT)")
            conn.execute("CREATE TABLE writes(thread_id TEXT, value TEXT)")
            conn.execute("CREATE TABLE episodic_memory(session_id TEXT, payload_json TEXT)")
            conn.execute("CREATE TABLE app_chat_sessions(id TEXT PRIMARY KEY, title TEXT, preview TEXT, message_count INTEGER, created_at_ms INTEGER, updated_at_ms INTEGER)")
            conn.execute("INSERT INTO checkpoints VALUES ('live', '{}')")
            conn.execute("INSERT INTO writes VALUES ('live', '{}')")
            conn.execute("INSERT INTO episodic_memory VALUES ('live', '{}')")
            conn.execute("INSERT INTO app_chat_sessions VALUES ('live', 'live title', '', 1, 1, 2)")
            conn.execute("INSERT INTO checkpoints VALUES ('orphan', '{}')")
            conn.execute("INSERT INTO writes VALUES ('orphan', '{}')")
            conn.execute("INSERT INTO episodic_memory VALUES ('orphan', '{}')")
            conn.commit()
            conn.close()

            sessions = await list_chat_sessions(db_path=db_path)

            conn = sqlite3.connect(db_path)
            checkpoint_ids = [
                row[0]
                for row in conn.execute("SELECT thread_id FROM checkpoints ORDER BY thread_id").fetchall()
            ]
            write_ids = [
                row[0]
                for row in conn.execute("SELECT thread_id FROM writes ORDER BY thread_id").fetchall()
            ]
            episodic_ids = [
                row[0]
                for row in conn.execute("SELECT session_id FROM episodic_memory ORDER BY session_id").fetchall()
            ]
            conn.close()

            self.assertEqual(["live"], [session["id"] for session in sessions])
            self.assertEqual(["live"], checkpoint_ids)
            self.assertEqual(["live"], write_ids)
            self.assertEqual(["live"], episodic_ids)


if __name__ == "__main__":
    unittest.main()
