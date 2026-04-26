"""SSE 流式行为回归测试。"""

from __future__ import annotations

import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.chat_session_service import ChatSessionService


class _FakeState:
    def __init__(self, values: dict):
        self.values = values


class _FakeGraph:
    def __init__(self, initial_state: _FakeState, chunks: list[dict], final_state: _FakeState):
        self._states = [initial_state, final_state]
        self._chunks = chunks
        self._aget_state_calls = 0
        self.state_updates = []

    async def aget_state(self, config):
        index = min(self._aget_state_calls, len(self._states) - 1)
        self._aget_state_calls += 1
        return self._states[index]

    async def astream(self, graph_input, config=None, stream_mode=None):
        assert stream_mode == "updates"
        for chunk in self._chunks:
            yield chunk

    async def aupdate_state(self, config, values):
        self.state_updates.append(values)


class _BlockingMemoryPipeline:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def ingest(self, **kwargs):
        self.started.set()
        await self.release.wait()

    async def close(self):
        return None


class _FakeEpisodicStore:
    def __init__(self, summaries=None):
        self.summaries = summaries or []

    async def fetch_recent_summaries(self, session_id: str, limit: int = 4):
        return list(self.summaries)


def _parse_sse_event(raw_event: str) -> tuple[str, object]:
    lines = [line for line in raw_event.strip().splitlines() if line]
    event_name = lines[0].split(": ", 1)[1]
    event_data = json.loads(lines[1].split(": ", 1)[1])
    return event_name, event_data


def test_reaction_stream_emits_pending_action_clear_before_combat_action():
    initial_state = _FakeState({
        "pending_reaction": {
            "attacker_id": "goblin_1",
            "attacker_name": "Goblin",
            "target_id": "player_预设-法师",
            "target_name": "预设-法师",
            "available_reactions": [{"spell_id": "shield", "name_cn": "护盾术", "min_slot": 1}],
            "attack_roll": {"raw_roll": 12, "attack_bonus": 4, "hit_total": 16, "target_ac": 12},
        }
    })
    final_state = _FakeState({"pending_reaction": None})
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[{
            "resolve_reaction_node": {
                "pending_reaction": None,
                "messages": [HumanMessage(content="[系统:怪物行动]\n你放弃了反应。")],
                "hp_changes": [],
            }
        }],
    )
    service = ChatSessionService(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", reaction_response={"spell_id": None})]

    raw_events = asyncio.run(_collect_events())
    parsed_events = [_parse_sse_event(event) for event in raw_events]
    event_names = [name for name, _ in parsed_events]

    first_pending_index = event_names.index("pending_action")
    combat_action_index = event_names.index("combat_action")
    assert first_pending_index < combat_action_index
    assert parsed_events[first_pending_index][1] is None


def test_stream_ignores_attack_roll_payload_marked_as_non_visual():
    initial_state = _FakeState({})
    final_state = _FakeState({})
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[{
            "combat_resolution_node": {
                "messages": [HumanMessage(
                    content="[系统:怪物行动]\nGoblin 处于麻痹状态，无法行动！",
                    additional_kwargs={
                        "attack_roll": {
                            "raw_roll": 12,
                            "final_total": 16,
                            "attack_bonus": 4,
                            "emit_dice_roll": False,
                        }
                    },
                )],
                "hp_changes": [],
            }
        }],
    )
    service = ChatSessionService(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="test")]

    raw_events = asyncio.run(_collect_events())
    event_names = [_parse_sse_event(event)[0] for event in raw_events]

    assert "combat_action" in event_names
    assert "dice_roll" not in event_names


def test_stream_ignores_hidden_tool_message_but_keeps_pending_action():
    initial_state = _FakeState({})
    final_state = _FakeState({
        "pending_reaction": {
            "attacker_id": "goblin_1",
            "attacker_name": "Goblin",
            "target_id": "player_hero",
            "target_name": "英雄",
            "available_reactions": [{"spell_id": "shield", "name_cn": "护盾术", "min_slot": 1}],
            "attack_roll": {"raw_roll": 12, "attack_bonus": 4, "hit_total": 16, "target_ac": 12},
        }
    })
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[{
            "tool": {
                "pending_reaction": {
                    "attacker_id": "goblin_1",
                    "attacker_name": "Goblin",
                    "target_id": "player_hero",
                    "target_name": "英雄",
                    "available_reactions": [{"spell_id": "shield", "name_cn": "护盾术", "min_slot": 1}],
                    "attack_roll": {"raw_roll": 12, "attack_bonus": 4, "hit_total": 16, "target_ac": 12},
                },
                "messages": [ToolMessage(
                    content="Goblin 的攻击命中了 英雄，已进入反应判定，等待玩家选择。",
                    tool_call_id="call_1",
                    additional_kwargs={"hidden_from_ui": True},
                )],
            }
        }],
    )
    service = ChatSessionService(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="继续")]

    raw_events = asyncio.run(_collect_events())
    parsed_events = [_parse_sse_event(event) for event in raw_events]
    event_names = [name for name, _ in parsed_events]

    assert "pending_action" in event_names
    assert "tool_message" not in event_names
    assert "dice_roll" not in event_names


def test_stream_emits_done_without_waiting_for_memory_ingestion():
    initial_state = _FakeState({"messages": []})
    final_state = _FakeState({"messages": [AIMessage(content="处理完毕。", tool_calls=[])]})
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[{
            "assistant": {
                "messages": [AIMessage(content="处理完毕。", tool_calls=[])],
            }
        }],
    )
    memory_pipeline = _BlockingMemoryPipeline()
    service = ChatSessionService(graph, memory_pipeline=memory_pipeline)

    async def _collect_events():
        raw_events = [event async for event in service.process_turn_stream(session_id="demo", message="继续")]
        await asyncio.wait_for(memory_pipeline.started.wait(), timeout=1)
        memory_pipeline.release.set()
        await service.aclose()
        return raw_events

    raw_events = asyncio.run(_collect_events())
    event_names = [_parse_sse_event(event)[0] for event in raw_events]

    assert event_names[-1] == "done"


def test_stream_keeps_ai_text_even_when_message_contains_tool_calls():
    initial_state = _FakeState({"messages": []})
    final_state = _FakeState({"messages": []})
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[
            {
                "combat_assistant": {
                    "messages": [
                        AIMessage(
                            content="战斗开始！让我先为哥布林1执行攻击。",
                            tool_calls=[{"name": "attack_action", "args": {"attacker_id": "goblin_1"}, "id": "call_1"}],
                        )
                    ],
                }
            },
            {
                "tool": {
                    "messages": [
                        ToolMessage(
                            content="Goblin 1 使用 [Scimitar] 攻击 预设-法师!\n未命中！",
                            tool_call_id="call_1",
                        )
                    ],
                }
            },
            {
                "combat_assistant": {
                    "messages": [
                        AIMessage(
                            content="哥布林1失手了，现在轮到哥布林2行动。",
                            tool_calls=[{"name": "next_turn", "args": {}, "id": "call_2"}],
                        )
                    ],
                }
            },
        ],
    )
    service = ChatSessionService(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="继续战斗")]

    raw_events = asyncio.run(_collect_events())
    parsed_events = [_parse_sse_event(event) for event in raw_events]
    assistant_messages = [payload["content"] for name, payload in parsed_events if name == "assistant_message"]

    assert assistant_messages == [
        "战斗开始！让我先为哥布林1执行攻击。",
        "哥布林1失手了，现在轮到哥布林2行动。",
    ]


def test_stream_injects_recent_episodic_context_before_graph_stream():
    initial_state = _FakeState({"messages": []})
    final_state = _FakeState({"messages": []})
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[],
    )
    service = ChatSessionService(graph, episodic_store=_FakeEpisodicStore(["上一轮已经确认地板有陷阱。"]))

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="stream-demo", message="继续")]

    raw_events = asyncio.run(_collect_events())
    event_names = [_parse_sse_event(event)[0] for event in raw_events]

    assert graph.state_updates[0]["episodic_context"] == ["上一轮已经确认地板有陷阱。"]
    assert event_names[-1] == "done"