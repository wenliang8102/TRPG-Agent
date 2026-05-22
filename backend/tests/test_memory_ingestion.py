import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import psycopg

from app.memory.episodic_store import EpisodicStore
from app.memory.ingestion import MemoryIngestionPipeline
from app.services.session_store import purge_chat_session_data


class _FakeStore:
    def __init__(self):
        self.records = []

    async def append_record(self, **kwargs):
        self.records.append(kwargs)

    async def close(self):
        return None


class _FakeLLMService:
    def __init__(self, response: str | None = None, error: Exception | None = None):
        self._response = response or ""
        self._error = error
        self.calls: list[dict[str, str]] = []

    def invoke_summary(self, summary_input: str, *, system_prompt: str) -> str:
        self.calls.append({"summary_input": summary_input, "system_prompt": system_prompt})
        if self._error is not None:
            raise self._error
        return self._response


class MemoryIngestionPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_keeps_stable_events_and_filters_volatile_combat_fields(self):
        store = _FakeStore()
        pipeline = MemoryIngestionPipeline(store)

        await pipeline.ingest(
            session_id="demo",
            turn_id="turn-1",
            old_state={
                "player": {
                    "name": "英雄",
                    "role_class": "法师",
                    "resources": {"spell_slot_lv1": 2},
                    "conditions": [],
                },
                "combat": None,
                "dead_units": {},
            },
            new_state={
                "player": {
                    "name": "英雄",
                    "role_class": "法师",
                    "resources": {"spell_slot_lv1": 1},
                    "conditions": [{"id": "shield_active"}],
                    "hp": 7,
                    "movement_left": 0,
                    "action_available": False,
                },
                "combat": {"round": 1, "initiative_order": ["player_hero", "goblin_1"], "participants": {"goblin_1": {}}},
                "dead_units": {},
            },
            new_messages=[
                ToolMessage(content="英雄施放护盾术。", tool_call_id="call_1", name="cast_spell"),
                AIMessage(content="护盾展开，挡住了下一次攻击。", tool_calls=[]),
            ],
            reply="护盾展开，挡住了下一次攻击。",
        )

        kinds = [record["record_kind"] for record in store.records]
        self.assertEqual(["turn_messages", "stable_events", "turn_summary"], kinds)

        stable_events = store.records[1]["payload"]["events"]
        event_types = [event["type"] for event in stable_events]
        self.assertIn("resource_update", event_types)
        self.assertIn("condition_update", event_types)
        self.assertIn("combat_started", event_types)

        serialized = str(stable_events)
        self.assertNotIn("movement_left", serialized)
        self.assertNotIn("action_available", serialized)
        self.assertNotIn("initiative_order", serialized)
        self.assertNotIn("hp", serialized)

        turn_summary = store.records[2]["payload"]["summary"]
        self.assertIn("资源变更", turn_summary)
        self.assertIn("主持人回应", turn_summary)

    async def test_fetch_recent_summaries_only_reads_turn_summary_records(self):
        with TemporaryDirectory() as temp_dir:
            store = EpisodicStore(str(Path(temp_dir) / "episodic.sqlite3"))

            await store.append_record(
                session_id="demo",
                turn_id="turn-1",
                record_kind="turn_summary",
                payload={"summary": "主持人回应：你们已经发现地牢密门。"},
            )
            await store.append_record(
                session_id="demo",
                turn_id="turn-2",
                record_kind="stable_events",
                payload={
                    "events": [
                        {
                            "type": "resource_update",
                            "changes": [{"key": "spell_slot_lv1", "old": 2, "new": 1}],
                        }
                    ]
                },
            )
            await store.append_record(
                session_id="demo",
                turn_id="turn-3",
                record_kind="turn_summary",
                payload={"summary": "主持人回应：哥布林已经举起短弓。"},
            )

            summaries = await store.fetch_recent_summaries("demo")
            await store.close()

        self.assertEqual(
            [
                "主持人回应：你们已经发现地牢密门。",
                "主持人回应：哥布林已经举起短弓。",
            ],
            summaries,
        )

    async def test_combat_end_turn_summary_prefers_end_combat_tool_result(self):
        store = _FakeStore()
        pipeline = MemoryIngestionPipeline(store)

        await pipeline.ingest(
            session_id="demo",
            turn_id="turn-2",
            old_state={
                "player": {
                    "name": "英雄",
                    "resources": {"spell_slot_lv1": 2},
                    "conditions": [],
                },
                "combat": {"round": 3, "participants": {"goblin_1": {}}},
                "dead_units": {},
            },
            new_state={
                "player": {
                    "name": "英雄",
                    "resources": {"spell_slot_lv1": 1},
                    "conditions": [{"id": "shield_active"}],
                },
                "combat": None,
                "dead_units": {"goblin_1": {}, "goblin_2": {}},
            },
            new_messages=[
                ToolMessage(content="共进行了 3 回合。 存活: 英雄 倒下: Goblin, Goblin", tool_call_id="call_1", name="end_combat"),
                AIMessage(content="你甩掉剑上的血迹，确认周围暂时安全。", tool_calls=[]),
            ],
            reply="你甩掉剑上的血迹，确认周围暂时安全。",
        )

        turn_summary = store.records[2]["payload"]["summary"]
        self.assertIn("战斗摘要：共进行了 3 回合。 存活: 英雄 倒下: Goblin, Goblin", turn_summary)
        self.assertNotIn("主持人回应", turn_summary)
        self.assertNotIn("工具结果", turn_summary)

    async def test_ingest_uses_model_to_compress_turn_summary_without_repeating_hud_state(self):
        store = _FakeStore()
        fake_llm = _FakeLLMService(response="英雄击败哥布林并消耗 1 个 1 环法术位，现场暂时安全。")

        with TemporaryDirectory() as temp_dir:
            pipeline = MemoryIngestionPipeline(store, llm_service=fake_llm, trace_dir=Path(temp_dir))

            await pipeline.ingest(
                session_id="demo",
                turn_id="turn-3",
                old_state={
                    "phase": "combat",
                    "player": {
                        "name": "英雄",
                        "resources": {"spell_slot_lv1": 2},
                        "conditions": [],
                    },
                    "combat": {"round": 2, "participants": {"goblin_1": {}}},
                    "dead_units": {},
                },
                new_state={
                    "phase": "exploration",
                    "player": {
                        "name": "英雄",
                        "resources": {"spell_slot_lv1": 1},
                        "conditions": [{"id": "shield_active"}],
                        "hp": 7,
                        "movement_left": 0,
                        "action_available": False,
                    },
                    "combat": None,
                    "dead_units": {"goblin_1": {}},
                },
                new_messages=[
                    ToolMessage(content="共进行了 2 回合。 存活: 英雄 倒下: Goblin", tool_call_id="call_1", name="end_combat"),
                    AIMessage(content="你确认周围暂时安全。", tool_calls=[]),
                ],
                reply="你确认周围暂时安全。",
            )

        self.assertEqual("英雄击败哥布林并消耗 1 个 1 环法术位，现场暂时安全。", store.records[2]["payload"]["summary"])
        self.assertEqual(1, len(fake_llm.calls))
        self.assertIn("不要重复 HUD 已提供的当前玩家状态", fake_llm.calls[0]["system_prompt"])
        self.assertIn("原始有效信息三成左右", fake_llm.calls[0]["system_prompt"])
        self.assertIn("combat_end_summary", fake_llm.calls[0]["summary_input"])
        self.assertNotIn("combat_archive_summary", fake_llm.calls[0]["summary_input"])
        self.assertNotIn("movement_left", fake_llm.calls[0]["summary_input"])
        self.assertNotIn("action_available", fake_llm.calls[0]["summary_input"])

    async def test_long_end_combat_tool_result_keeps_detail_budget(self):
        store = _FakeStore()
        pipeline = MemoryIngestionPipeline(store)
        long_summary = "共进行了 4 回合。" + ("巴伦守住洞口，伊莲用魔法飞弹压制哥布林。" * 40)

        await pipeline.ingest(
            session_id="demo",
            turn_id="turn-long-combat",
            old_state={
                "combat": {"round": 4, "participants": {"goblin_1": {}}},
                "player": {"name": "巴伦", "resources": {}, "conditions": []},
                "dead_units": {},
            },
            new_state={
                "combat": None,
                "player": {"name": "巴伦", "resources": {}, "conditions": []},
                "dead_units": {"goblin_1": {}},
            },
            new_messages=[ToolMessage(content=long_summary, tool_call_id="call_end", name="end_combat")],
            reply="战斗结束。",
        )

        turn_summary = store.records[2]["payload"]["summary"]
        self.assertGreater(len(turn_summary), 240)
        self.assertIn("伊莲用魔法飞弹压制哥布林", turn_summary)

    async def test_ingest_falls_back_to_rule_summary_when_model_summary_fails(self):
        store = _FakeStore()
        fake_llm = _FakeLLMService(error=RuntimeError("summary failed"))

        with TemporaryDirectory() as temp_dir:
            pipeline = MemoryIngestionPipeline(store, llm_service=fake_llm, trace_dir=Path(temp_dir))

            await pipeline.ingest(
                session_id="demo",
                turn_id="turn-4",
                old_state={
                    "phase": "exploration",
                    "player": {"name": "英雄", "resources": {}, "conditions": []},
                    "combat": None,
                    "dead_units": {},
                },
                new_state={
                    "phase": "exploration",
                    "player": {"name": "英雄", "resources": {}, "conditions": []},
                    "combat": None,
                    "dead_units": {},
                },
                new_messages=[AIMessage(content="石门背后传来锁链拖动声。", tool_calls=[])],
                reply="石门背后传来锁链拖动声。",
            )

        turn_summary = store.records[1]["payload"]["summary"]
        self.assertEqual("主持人回应：石门背后传来锁链拖动声。", turn_summary)


@unittest.skipUnless(os.getenv("TRPG_TEST_POSTGRES_URL"), "TRPG_TEST_POSTGRES_URL is not set")
class PostgresEpisodicStoreIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database_url = os.environ["TRPG_TEST_POSTGRES_URL"]
        self.session_id = f"pg-memory-{uuid4()}"
        self.other_session_id = f"pg-memory-other-{uuid4()}"
        self.store = EpisodicStore(database_backend="postgres", database_url=self.database_url)

    async def asyncTearDown(self):
        await self.store.close()
        async with await psycopg.AsyncConnection.connect(self.database_url) as conn:
            await conn.execute("DELETE FROM episodic_memory WHERE session_id = %s", (self.session_id,))
            await conn.execute("DELETE FROM episodic_memory WHERE session_id = %s", (self.other_session_id,))

    async def test_postgres_store_writes_and_reads_session_memory(self):
        await self.store.append_record(
            session_id=self.session_id,
            turn_id="turn-1",
            record_kind="turn_summary",
            payload={"summary": "你们在洞口发现了新鲜脚印。"},
        )
        await self.store.append_record(
            session_id=self.session_id,
            turn_id="turn-2",
            record_kind="turn_summary",
            payload={"summary": "哥布林的短弓声从林中传来。"},
        )
        await self.store.append_record(
            session_id=self.other_session_id,
            turn_id="turn-1",
            record_kind="turn_summary",
            payload={"summary": "不应被读取。"},
        )

        summaries = await self.store.fetch_recent_summaries(self.session_id)
        records = await self.store.fetch_recent_records(self.session_id)

        self.assertEqual(["你们在洞口发现了新鲜脚印。", "哥布林的短弓声从林中传来。"], summaries)
        self.assertEqual(2, len(records))
        self.assertTrue(all(record["payload"]["summary"] != "不应被读取。" for record in records))

    async def test_purge_session_removes_postgres_episodic_memory(self):
        await self.store.append_record(
            session_id=self.session_id,
            turn_id="turn-1",
            record_kind="turn_summary",
            payload={"summary": "即将删除。"},
        )

        with (
            patch("app.services.session_store.settings.database_backend", "postgres"),
            patch("app.services.session_store.settings.database_url", self.database_url),
        ):
            result = await purge_chat_session_data(self.session_id)

        records = await self.store.fetch_recent_records(self.session_id)
        self.assertEqual([], records)
        self.assertGreaterEqual(result["deletedRows"], 1)


if __name__ == "__main__":
    unittest.main()
