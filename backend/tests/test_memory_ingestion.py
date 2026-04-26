import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.memory.episodic_store import EpisodicStore
from app.memory.ingestion import MemoryIngestionPipeline


class _FakeStore:
    def __init__(self):
        self.records = []

    async def append_record(self, **kwargs):
        self.records.append(kwargs)

    async def close(self):
        return None


class _HybridContextStore(EpisodicStore):
    def __init__(self, records):
        self._records = records

    async def fetch_recent_records(self, session_id: str, limit: int = 20):
        return list(self._records)


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

    async def test_hybrid_context_blocks_mix_recent_summaries_and_stable_events(self):
        store = _HybridContextStore([
            {
                "turn_id": "turn-3",
                "record_kind": "turn_summary",
                "payload": {"summary": "主持人回应：哥布林已经举起短弓。"},
                "created_at": "2026-04-26 10:03:00",
            },
            {
                "turn_id": "turn-2",
                "record_kind": "stable_events",
                "payload": {
                    "events": [
                        {
                            "type": "resource_update",
                            "changes": [{"key": "spell_slot_lv1", "old": 2, "new": 1}],
                        },
                        {
                            "type": "condition_update",
                            "player_name": "英雄",
                            "added": ["shield_active"],
                            "removed": [],
                        },
                    ]
                },
                "created_at": "2026-04-26 10:02:00",
            },
            {
                "turn_id": "turn-1",
                "record_kind": "turn_summary",
                "payload": {"summary": "主持人回应：你们已经发现地牢密门。"},
                "created_at": "2026-04-26 10:01:00",
            },
        ])

        blocks = await store.fetch_recent_context_blocks("demo")

        self.assertIn("主持人回应：你们已经发现地牢密门。", blocks)
        self.assertIn("资源更新：spell_slot_lv1: 2 -> 1。", blocks)
        self.assertIn("英雄：获得状态 shield_active。", blocks)
        self.assertIn("主持人回应：哥布林已经举起短弓。", blocks)


if __name__ == "__main__":
    unittest.main()