"""SSE 流式行为回归测试。"""

from __future__ import annotations

import asyncio
import json

from langchain_core.messages import HumanMessage

from app.services.chat_session_service import ChatSessionService


class _FakeState:
    def __init__(self, values: dict):
        self.values = values


class _FakeGraph:
    def __init__(self, initial_state: _FakeState, chunks: list[dict], final_state: _FakeState):
        self._states = [initial_state, final_state]
        self._chunks = chunks
        self._aget_state_calls = 0

    async def aget_state(self, config):
        index = min(self._aget_state_calls, len(self._states) - 1)
        self._aget_state_calls += 1
        return self._states[index]

    async def astream(self, graph_input, config=None, stream_mode=None):
        assert stream_mode == "updates"
        for chunk in self._chunks:
            yield chunk


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
            "monster_combat_node": {
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