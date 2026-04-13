import unittest
from types import SimpleNamespace
from uuid import UUID

from langchain_core.messages import AIMessage

from app.services.chat_session_service import ChatSessionService


class FakeGraph:
    def __init__(self, result: dict):
        self.result = result
        self.last_input = None
        self.last_config = None
        self.values = {"messages": []}
        self.tasks = []

    async def ainvoke(self, graph_input, config):
        self.last_input = graph_input
        self.last_config = config
        self.values = self.result
        return self.result

    async def aget_state(self, config):
        self.last_config = config
        return SimpleNamespace(values=self.values, tasks=self.tasks)


class ChatSessionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_turn_returns_last_non_tool_ai_message(self):
        graph = FakeGraph(
            {
                "messages": [
                    AIMessage(content="", tool_calls=[{"name": "weather", "args": {"city": "beijing"}, "id": "call_1"}]),
                    AIMessage(content="北京今天晴，22C。", tool_calls=[]),
                ]
            }
        )
        service = ChatSessionService(graph=graph)

        result = await service.process_turn(message="北京天气怎么样", session_id="s1")

        self.assertEqual("北京今天晴，22C。", result["reply"])
        self.assertEqual("s1", result["session_id"])
        self.assertIsNone(result["plan"])
        self.assertEqual("s1", graph.last_config["configurable"]["thread_id"])
        self.assertEqual("北京天气怎么样", graph.last_input["messages"][0].content)

    async def test_process_turn_generates_session_id_when_missing(self):
        graph = FakeGraph({"messages": [AIMessage(content="ok", tool_calls=[])]})
        service = ChatSessionService(graph=graph)

        result = await service.process_turn(message="hello")

        UUID(result["session_id"])
        self.assertEqual(result["session_id"], graph.last_config["configurable"]["thread_id"])


if __name__ == "__main__":
    unittest.main()
