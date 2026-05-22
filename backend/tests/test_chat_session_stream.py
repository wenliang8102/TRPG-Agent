"""SSE 流式行为回归测试。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.adventures.runtime import AdventurePreTurnDecision, AdventureProgressDecision, AdventureRuntimeUpdate
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
        self.last_stream_config = None

    async def aget_state(self, config):
        index = min(self._aget_state_calls, len(self._states) - 1)
        self._aget_state_calls += 1
        return self._states[index]

    async def astream(self, graph_input, config=None, stream_mode=None):
        assert stream_mode == "updates"
        self.last_stream_config = config
        for chunk in self._chunks:
            yield chunk

    async def aupdate_state(self, config, values, as_node=None):
        self.state_updates.append(values)
        self._states[-1].values = {**self._states[-1].values, **values}


def _parse_sse_event(raw_event: str) -> tuple[str, object]:
    lines = [line for line in raw_event.strip().splitlines() if line]
    event_name = lines[0].split(": ", 1)[1]
    event_data = json.loads(lines[1].split(": ", 1)[1])
    return event_name, event_data


class _NoopDirector:
    def adjudicate_pre_turn(self, *, state, player_message, session_id=None):
        return AdventurePreTurnDecision()

    def adjudicate(self, *, state, recent_messages, session_id=None):
        return AdventureProgressDecision()


def _service(graph):
    return ChatSessionService(graph, adventure_director=_NoopDirector())


class _GuardrailDirector(_NoopDirector):
    def adjudicate(self, *, state, recent_messages, session_id=None):
        return AdventureProgressDecision(
            desync_detected=True,
            unsupported_claims=["冈德伦已被救出"],
            warning="回到当前节点。",
        )


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
    service = _service(graph)

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
    service = _service(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="test")]

    raw_events = asyncio.run(_collect_events())
    event_names = [_parse_sse_event(event)[0] for event in raw_events]

    assert "combat_action" in event_names
    assert "dice_roll" not in event_names


def test_stream_emits_dice_roll_card_payload_for_attack_roll():
    initial_state = _FakeState({})
    final_state = _FakeState({})
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[{
            "combat_resolution_node": {
                "messages": [HumanMessage(
                    content="[系统:怪物行动]\nGoblin 使用弯刀攻击。",
                    additional_kwargs={
                        "attack_roll": {
                            "raw_roll": 12,
                            "final_total": 16,
                            "attack_bonus": 4,
                            "target_ac": 14,
                            "attack_name": "Scimitar",
                        }
                    },
                )],
                "hp_changes": [],
            }
        }],
    )
    service = _service(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="test")]

    raw_events = asyncio.run(_collect_events())
    parsed_events = [_parse_sse_event(event) for event in raw_events]
    dice_events = [payload for name, payload in parsed_events if name == "dice_roll"]

    assert dice_events == [{
        "kind": "attack",
        "title": "Scimitar",
        "raw_roll": 12,
        "modifier": 4,
        "final_total": 16,
        "target": 14,
        "target_label": "AC",
        "formula": "1d20",
        "advantage": "normal",
    }]


def test_stream_replays_executor_combat_events_for_existing_animations():
    initial_state = _FakeState({})
    final_state = _FakeState({})
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[{
            "combat_executor": {
                "combat_events": [{
                    "content": "Goblin 挥出弯刀。",
                    "attack_roll": {
                        "raw_roll": 13,
                        "final_total": 17,
                        "attack_bonus": 4,
                        "target_ac": 16,
                        "attack_name": "Scimitar",
                    },
                    "hp_changes": [{"target_id": "player_hero", "old_hp": 18, "new_hp": 13}],
                }],
            }
        }],
    )
    service = _service(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="test")]

    parsed_events = [_parse_sse_event(event) for event in asyncio.run(_collect_events())]

    dice_events = [payload for name, payload in parsed_events if name == "dice_roll"]
    combat_events = [payload for name, payload in parsed_events if name == "combat_action"]
    assert dice_events[0]["title"] == "Scimitar"
    assert combat_events[0] == {
        "content": "Goblin 挥出弯刀。",
        "hp_changes": [{"target_id": "player_hero", "old_hp": 18, "new_hp": 13}],
    }


def test_stream_emits_state_update_before_followup_assistant_reply():
    initial_state = _FakeState({"messages": []})
    final_state = _FakeState({
        "messages": [AIMessage(content="地图已经铺开，你看见了敌人。", tool_calls=[])],
        "space": {
            "active_map_id": "road",
            "maps": {
                "road": {"id": "road", "name": "三猪小径", "width": 60, "height": 40, "grid_size": 5}
            },
            "placements": {},
        },
    })
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[
            {
                "tool": {
                    "space": {
                        "active_map_id": "road",
                        "maps": {
                            "road": {"id": "road", "name": "三猪小径", "width": 60, "height": 40, "grid_size": 5}
                        },
                        "placements": {},
                    },
                    "messages": [
                        ToolMessage(
                            content="已创建地图：三猪小径。",
                            tool_call_id="call_1",
                        )
                    ],
                }
            },
            {
                "assistant": {
                    "messages": [AIMessage(content="地图已经铺开，你看见了敌人。", tool_calls=[])],
                }
            },
        ],
    )
    service = _service(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="创建地图")]

    raw_events = asyncio.run(_collect_events())
    parsed_events = [_parse_sse_event(event) for event in raw_events]
    event_names = [name for name, _ in parsed_events]

    first_state_index = event_names.index("state_update")
    followup_reply_index = next(
        index
        for index, (name, payload) in enumerate(parsed_events)
        if name == "assistant_message" and payload["content"] == "地图已经铺开，你看见了敌人。"
    )
    assert first_state_index < followup_reply_index
    assert parsed_events[first_state_index][1]["space"]["active_map_id"] == "road"


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
    service = _service(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="继续")]

    raw_events = asyncio.run(_collect_events())
    parsed_events = [_parse_sse_event(event) for event in raw_events]
    event_names = [name for name, _ in parsed_events]

    assert "pending_action" in event_names
    assert "tool_message" not in event_names
    assert "dice_roll" not in event_names


def test_stream_emits_done_without_memory_ingestion():
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
    service = _service(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="继续")]

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
    service = _service(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="继续战斗")]

    raw_events = asyncio.run(_collect_events())
    parsed_events = [_parse_sse_event(event) for event in raw_events]
    assistant_messages = [payload["content"] for name, payload in parsed_events if name == "assistant_message"]

    assert assistant_messages == [
        "战斗开始！让我先为哥布林1执行攻击。",
        "哥布林1失手了，现在轮到哥布林2行动。",
    ]


def test_stream_hides_internal_workflow_preludes():
    initial_state = _FakeState({"messages": []})
    final_state = _FakeState({"messages": []})
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[
            {
                "assistant": {
                    "messages": [
                        AIMessage(
                            content="好的，我来布置剩余地精并提交开战计划。",
                            tool_calls=[{"name": "manage_space", "args": {}, "id": "call_1"}],
                        )
                    ],
                }
            },
            {
                "assistant": {
                    "messages": [AIMessage(content="灌木中箭矢破空，战斗真正开始。", tool_calls=[])],
                }
            },
        ],
    )
    service = _service(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="demo", message="继续")]

    parsed_events = [_parse_sse_event(event) for event in asyncio.run(_collect_events())]
    assistant_messages = [payload["content"] for name, payload in parsed_events if name == "assistant_message"]

    assert assistant_messages == ["灌木中箭矢破空，战斗真正开始。"]


def test_end_player_controlled_turn_stream_wakes_enemy_incrementally():
    initial_state = _FakeState({
        "messages": [],
        "phase": "combat",
        "player": {"id": "player_hero", "name": "英雄", "side": "player", "hp": 12, "max_hp": 12},
        "combat": {
            "round": 1,
            "current_actor_id": "player_hero",
            "initiative_order": ["player_hero", "goblin_1"],
            "participants": {"goblin_1": {"id": "goblin_1", "name": "Goblin", "side": "enemy", "hp": 7, "max_hp": 7}},
        },
    })
    final_state = _FakeState({
        "messages": [],
        "phase": "combat",
        "player": {"id": "player_hero", "name": "英雄", "side": "player", "hp": 12, "max_hp": 12},
        "combat": {
            "round": 1,
            "current_actor_id": "goblin_1",
            "initiative_order": ["player_hero", "goblin_1"],
            "participants": {"goblin_1": {"id": "goblin_1", "name": "Goblin", "side": "enemy", "hp": 7, "max_hp": 7}},
        },
    })
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[
            {
                "combat_executor": {
                    "combat_events": [{
                        "content": "Goblin 射出短弓。",
                        "attack_roll": {"raw_roll": 12, "attack_bonus": 4, "final_total": 16, "target_ac": 16, "attack_name": "Shortbow"},
                        "hp_changes": [{"target_id": "player_hero", "old_hp": 12, "new_hp": 8}],
                    }],
                }
            }
        ],
    )
    service = _service(graph)

    async def _collect_events():
        return [event async for event in service.end_player_controlled_turn_stream(session_id="demo", actor_id="player_hero")]

    parsed_events = [_parse_sse_event(event) for event in asyncio.run(_collect_events())]
    event_names = [name for name, _ in parsed_events]

    assert "dice_roll" in event_names
    assert "combat_action" in event_names
    assert event_names[-1] == "done"


def test_stream_clears_hot_episodic_context_before_graph_stream():
    initial_state = _FakeState({"messages": [], "episodic_context": ["旧热摘要不应继续注入。"]})
    final_state = _FakeState({"messages": []})
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[],
    )
    service = _service(graph)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="stream-demo", message="继续")]

    raw_events = asyncio.run(_collect_events())
    event_names = [_parse_sse_event(event)[0] for event in raw_events]

    assert graph.state_updates[0]["episodic_context"] == []
    assert graph.last_stream_config["recursion_limit"] == 80
    assert event_names[-1] == "done"


def test_stream_keeps_guardrail_internal_after_desynced_reply():
    initial_state = _FakeState({
        "messages": [],
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["lost_mine_start", "goblin_ambush"],
            "completed_node_ids": ["lost_mine_start"],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "pending_exit_option_ids": [],
        },
    })
    final_state = _FakeState({
        "messages": [AIMessage(content="你们已经在酒馆救出了冈德伦。", tool_calls=[])],
        "phase": "exploration",
        "adventure": initial_state.values["adventure"],
    })
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[{
            "assistant": {
                "messages": [AIMessage(content="你们已经在酒馆救出了冈德伦。", tool_calls=[])],
            }
        }],
    )
    service = ChatSessionService(graph, adventure_director=_GuardrailDirector())

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="guardrail-stream", message="继续")]

    raw_events = asyncio.run(_collect_events())
    parsed_events = [_parse_sse_event(event) for event in raw_events]
    assistant_messages = [payload["content"] for name, payload in parsed_events if name == "assistant_message"]

    assert assistant_messages == ["你们已经在酒馆救出了冈德伦。"]
    assert graph.state_updates[-1]["adventure_guardrail_warning"]["unsupported_claims"] == ["冈德伦已被救出"]


def test_stream_post_turn_director_receives_full_message_history():
    old_messages = [
        HumanMessage(content="我检查货车。"),
        AIMessage(content="货车仍在路边。", tool_calls=[]),
    ]
    final_messages = [
        *old_messages,
        HumanMessage(content="继续"),
        AIMessage(content="你沿小路前进。", tool_calls=[]),
    ]
    initial_state = _FakeState({"messages": old_messages, "phase": "exploration"})
    final_state = _FakeState({"messages": final_messages, "phase": "exploration"})
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[{
            "assistant": {
                "messages": [AIMessage(content="你沿小路前进。", tool_calls=[])],
            }
        }],
    )
    service = _service(graph)
    service._apply_pre_turn_adventure_runtime = AsyncMock(return_value=None)
    service._apply_adventure_runtime = AsyncMock(return_value=None)

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="director-history-stream", message="继续")]

    asyncio.run(_collect_events())

    passed_messages = service._apply_adventure_runtime.call_args.args[3]
    assert str(passed_messages[0].content).startswith("[系统:冒险节点帧]")


def test_stream_emits_reward_announcement_without_baking_it_into_model_prompt():
    initial_state = _FakeState({
        "messages": [],
        "phase": "combat",
    })
    final_state = _FakeState({
        "messages": [AIMessage(content="你击败了地精。", tool_calls=[])],
        "phase": "combat",
    })
    graph = _FakeGraph(
        initial_state=initial_state,
        final_state=final_state,
        chunks=[{
            "assistant": {
                "messages": [AIMessage(content="你击败了地精。", tool_calls=[])],
            }
        }],
    )
    service = _service(graph)
    service._apply_pre_turn_adventure_runtime = AsyncMock(return_value=AdventureRuntimeUpdate())
    service._apply_adventure_runtime = AsyncMock(
        return_value=AdventureRuntimeUpdate(
            player_notifications=[
                {
                    "kind": "xp_granted",
                    "node_id": "cragmaw_hideout_entrance",
                    "id": "goblin_ambush_hideout_75_xp",
                    "type": "xp",
                    "amount": 75,
                    "previous_xp": 0,
                    "current_xp": 75,
                    "description": "打败伏击地精并抵达克拉摩窝点后发放。",
                }
            ]
        )
    )

    async def _collect_events():
        return [event async for event in service.process_turn_stream(session_id="reward-stream", message="继续")]

    raw_events = asyncio.run(_collect_events())
    parsed_events = [_parse_sse_event(event) for event in raw_events]
    assistant_messages = [payload["content"] for name, payload in parsed_events if name == "assistant_message"]

    assert "你击败了地精。" in assistant_messages
    assert any("【经验奖励】你获得 75 XP，已计入角色卡" in message for message in assistant_messages)
