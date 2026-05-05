import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.chat_session_service import ChatSessionService


class FakeGraph:
    def __init__(self, result: dict):
        self.result = result
        self.last_input = None
        self.last_config = None
        self.values = {"messages": []}
        self.tasks = []
        self.state_updates = []

    async def ainvoke(self, graph_input, config):
        self.last_input = graph_input
        self.last_config = config
        self.values = self.result
        return self.result

    async def aget_state(self, config):
        self.last_config = config
        return SimpleNamespace(values=self.values, tasks=self.tasks)

    async def aupdate_state(self, config, values):
        self.last_config = config
        self.state_updates.append(values)
        self.values = {**self.values, **values}


class FakeMemoryPipeline:
    def __init__(self):
        self.calls = []
        self.called = asyncio.Event()

    async def ingest(self, **kwargs):
        self.calls.append(kwargs)
        self.called.set()

    async def close(self):
        return None


class FakeEpisodicStore:
    def __init__(self, summaries=None):
        self.summaries = summaries or []
        self.calls = []

    async def fetch_recent_summaries(self, session_id: str, limit: int = 4):
        self.calls.append({"session_id": session_id, "limit": limit})
        return list(self.summaries)


class ChatSessionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_turn_returns_last_non_tool_ai_message(self):
        graph = FakeGraph(
            {
                "messages": [
                    AIMessage(content="", tool_calls=[{"name": "mock_lookup", "args": {"query": "beijing"}, "id": "call_1"}]),
                    AIMessage(content="查询完成。", tool_calls=[]),
                ]
            }
        )
        service = ChatSessionService(graph=graph)

        result = await service.process_turn(message="查一下北京", session_id="s1")

        self.assertEqual("查询完成。", result["reply"])
        self.assertEqual("s1", result["session_id"])
        self.assertIsNone(result["plan"])
        self.assertEqual("s1", graph.last_config["configurable"]["thread_id"])
        self.assertEqual(80, graph.last_config["recursion_limit"])
        self.assertEqual("查一下北京", graph.last_input["messages"][0].content)

    async def test_process_turn_schedules_memory_ingestion_without_changing_reply(self):
        graph = FakeGraph({"messages": [AIMessage(content="好的。", tool_calls=[])]})
        memory_pipeline = FakeMemoryPipeline()
        service = ChatSessionService(graph=graph, memory_pipeline=memory_pipeline)

        result = await service.process_turn(message="继续", session_id="s-memory")
        await asyncio.wait_for(memory_pipeline.called.wait(), timeout=1)
        await service.aclose()

        self.assertEqual("好的。", result["reply"])
        self.assertEqual("s-memory", memory_pipeline.calls[0]["session_id"])
        self.assertEqual("好的。", memory_pipeline.calls[0]["reply"])
        self.assertEqual(1, len(memory_pipeline.calls[0]["new_messages"]))

    async def test_process_turn_injects_recent_episodic_context_before_graph_run(self):
        graph = FakeGraph({"messages": [AIMessage(content="继续推进。", tool_calls=[])]})
        episodic_store = FakeEpisodicStore(["玩家已经发现密门。", "上一轮消耗了一个 1 环法术位。"])
        service = ChatSessionService(graph=graph, episodic_store=episodic_store)

        result = await service.process_turn(message="继续", session_id="episodic-demo")

        self.assertEqual("继续推进。", result["reply"])
        self.assertEqual("episodic-demo", episodic_store.calls[0]["session_id"])
        self.assertEqual("episodic-demo", graph.state_updates[0]["session_id"])
        self.assertEqual(
            ["玩家已经发现密门。", "上一轮消耗了一个 1 环法术位。"],
            graph.state_updates[0]["episodic_context"],
        )

    async def test_process_turn_generates_session_id_when_missing(self):
        graph = FakeGraph({"messages": [AIMessage(content="ok", tool_calls=[])]})
        service = ChatSessionService(graph=graph)

        result = await service.process_turn(message="hello")

        UUID(result["session_id"])
        self.assertEqual(result["session_id"], graph.last_config["configurable"]["thread_id"])

    @patch("app.services.chat_session_service.trace_chat_result")
    @patch("app.services.chat_session_service.trace_chat_request")
    async def test_process_turn_records_trace_events(self, mock_trace_request, mock_trace_result):
        graph = FakeGraph({"messages": [AIMessage(content="继续推进。", tool_calls=[])]})
        service = ChatSessionService(graph=graph)

        result = await service.process_turn(message="继续", session_id="trace-demo")

        self.assertEqual("继续推进。", result["reply"])
        mock_trace_request.assert_called_once()
        mock_trace_result.assert_called_once()
        self.assertEqual("trace-demo", mock_trace_request.call_args.args[0])
        self.assertEqual("trace-demo", mock_trace_result.call_args.args[0])

    async def test_get_history_keeps_original_transcript_without_tool_placeholders(self):
        graph = FakeGraph({"messages": []})
        graph.values = {
            "messages": [
                HumanMessage(content="我攻击哥布林"),
                AIMessage(content="", tool_calls=[{"name": "attack_action", "args": {"attacker_id": "player_hero"}, "id": "call_1"}]),
                ToolMessage(content="Goblin 使用 [Scimitar] 攻击 英雄!\n英雄 HP: 18 → 13", tool_call_id="call_1", name="attack_action"),
                HumanMessage(content="[系统:怪物行动]\n你放弃了反应。"),
                AIMessage(content="哥布林被你逼退了半步。", tool_calls=[]),
            ]
        }
        service = ChatSessionService(graph=graph)

        history = await service.get_history(session_id="demo", limit=10)

        self.assertEqual(
            [
                {"role": "user", "content": "我攻击哥布林"},
                {"role": "assistant", "content": "哥布林被你逼退了半步。"},
            ],
            history["messages"],
        )
        self.assertFalse(any("[工具:" in item["content"] for item in history["messages"]))
        self.assertFalse(any("状态快照" in item["content"] for item in history["messages"]))

    async def test_get_history_keeps_ai_text_that_also_triggered_tools(self):
        graph = FakeGraph({"messages": []})
        graph.values = {
            "messages": [
                HumanMessage(content="继续战斗"),
                AIMessage(
                    content="战斗开始！让我先为哥布林1执行攻击。",
                    tool_calls=[{"name": "attack_action", "args": {"attacker_id": "goblin_1"}, "id": "call_1"}],
                ),
                ToolMessage(content="Goblin 1 使用 [Scimitar] 攻击 预设-法师!\n未命中！", tool_call_id="call_1"),
                AIMessage(
                    content="哥布林1失手了，现在轮到哥布林2行动。",
                    tool_calls=[{"name": "next_turn", "args": {}, "id": "call_2"}],
                ),
            ]
        }
        service = ChatSessionService(graph=graph)

        history = await service.get_history(session_id="demo", limit=10)

        self.assertEqual(
            [
                {"role": "user", "content": "继续战斗"},
                {"role": "assistant", "content": "战斗开始！让我先为哥布林1执行攻击。"},
                {"role": "assistant", "content": "哥布林1失手了，现在轮到哥布林2行动。"},
            ],
            history["messages"],
        )


if __name__ == "__main__":
    unittest.main()
