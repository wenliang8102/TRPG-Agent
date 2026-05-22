import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.adventures.runtime import AdventurePreTurnDecision, AdventureProgressDecision, AdventureRuntimeUpdate
from app.memory.context_assembler import ADVENTURE_NODE_FRAME_MESSAGE_PREFIX
from app.services.chat_session_service import ChatSessionService


async def _noop_touch_chat_session(**kwargs):
    return None


def _noop_trace(*args, **kwargs):
    return None


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
        if isinstance(graph_input, dict) and graph_input.get("messages"):
            self.values["messages"] = [*list(self.values.get("messages", [])), *list(graph_input["messages"])]
        self.values = {**self.values, **self.result}
        return self.result

    async def astream(self, graph_input, config, stream_mode):
        self.last_input = graph_input
        self.last_config = config
        if isinstance(graph_input, dict) and graph_input.get("messages"):
            self.values["messages"] = [*list(self.values.get("messages", [])), *list(graph_input["messages"])]
        self.values = {**self.values, **self.result}
        yield {"assistant": self.result}

    async def aget_state(self, config):
        self.last_config = config
        return SimpleNamespace(values=self.values, tasks=self.tasks)

    async def aupdate_state(self, config, values, as_node=None):
        self.last_config = config
        self.state_updates.append(values)
        self.values = {**self.values, **values}


class FakeAdventureDirector:
    def __init__(
        self,
        decision: AdventureProgressDecision | None = None,
        pre_turn_decision: AdventurePreTurnDecision | None = None,
    ):
        self.decision = decision or AdventureProgressDecision()
        self.pre_turn_decision = pre_turn_decision or AdventurePreTurnDecision()
        self.calls = []
        self.pre_turn_calls = []

    def adjudicate_pre_turn(self, *, state, player_message, session_id=None):
        self.pre_turn_calls.append({"state": state, "player_message": player_message, "session_id": session_id})
        return self.pre_turn_decision

    def adjudicate(self, *, state, recent_messages, session_id=None):
        self.calls.append({"state": state, "recent_messages": recent_messages, "session_id": session_id})
        return self.decision


def _noop_director():
    return FakeAdventureDirector()


class ChatSessionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._touch_session_patcher = patch(
            "app.services.chat_session_service.touch_chat_session",
            side_effect=_noop_touch_chat_session,
        )
        self._trace_request_patcher = patch(
            "app.services.chat_session_service.trace_chat_request",
            side_effect=_noop_trace,
        )
        self._trace_result_patcher = patch(
            "app.services.chat_session_service.trace_chat_result",
            side_effect=_noop_trace,
        )
        self._trace_error_patcher = patch(
            "app.services.chat_session_service.trace_chat_error",
            side_effect=_noop_trace,
        )
        self._trace_adventure_update_patcher = patch(
            "app.services.chat_session_service.trace_adventure_runtime_update",
            side_effect=_noop_trace,
        )
        self._trace_adventure_failed_patcher = patch(
            "app.services.chat_session_service.trace_adventure_runtime_failed",
            side_effect=_noop_trace,
        )
        self._touch_session_patcher.start()
        self._trace_request_patcher.start()
        self._trace_result_patcher.start()
        self._trace_error_patcher.start()
        self._trace_adventure_update_patcher.start()
        self._trace_adventure_failed_patcher.start()

    async def asyncTearDown(self):
        self._trace_adventure_failed_patcher.stop()
        self._trace_adventure_update_patcher.stop()
        self._trace_error_patcher.stop()
        self._trace_result_patcher.stop()
        self._trace_request_patcher.stop()
        self._touch_session_patcher.stop()

    async def test_process_turn_returns_last_non_tool_ai_message(self):
        graph = FakeGraph(
            {
                "messages": [
                    AIMessage(content="", tool_calls=[{"name": "mock_lookup", "args": {"query": "beijing"}, "id": "call_1"}]),
                    AIMessage(content="查询完成。", tool_calls=[]),
                ]
            }
        )
        service = ChatSessionService(graph=graph, adventure_director=_noop_director())

        result = await service.process_turn(message="查一下北京", session_id="s1")

        self.assertEqual("查询完成。", result["reply"])
        self.assertEqual("s1", result["session_id"])
        self.assertIsNone(result["plan"])
        self.assertEqual("s1", graph.last_config["configurable"]["thread_id"])
        self.assertEqual(80, graph.last_config["recursion_limit"])
        self.assertEqual("查一下北京", graph.last_input["messages"][0].content)

    async def test_process_turn_clears_hot_episodic_context_before_graph_run(self):
        graph = FakeGraph({"messages": [AIMessage(content="继续推进。", tool_calls=[])]})
        graph.values = {
            "messages": [],
            "episodic_context": ["旧热摘要不应继续注入。"],
        }
        service = ChatSessionService(graph=graph, adventure_director=_noop_director())

        result = await service.process_turn(message="继续", session_id="episodic-demo")

        self.assertEqual("继续推进。", result["reply"])
        self.assertEqual("episodic-demo", graph.state_updates[0]["session_id"])
        self.assertEqual([], graph.state_updates[0]["episodic_context"])

    async def test_runtime_context_appends_initial_adventure_node_frame(self):
        graph = FakeGraph({"messages": [AIMessage(content="继续推进。", tool_calls=[])]})
        service = ChatSessionService(graph=graph, adventure_director=_noop_director())

        await service.process_turn(message="继续", session_id="initial-node-frame-demo")

        first_update = graph.state_updates[0]
        self.assertEqual("adventure_hook_meet_me_in_phandalin", first_update["adventure"]["active_node_id"])
        self.assertTrue(first_update["messages"][0].content.startswith(ADVENTURE_NODE_FRAME_MESSAGE_PREFIX))
        self.assertIn('"node_id":"adventure_hook_meet_me_in_phandalin"', first_update["messages"][0].content)

    async def test_process_turn_generates_session_id_when_missing(self):
        graph = FakeGraph({"messages": [AIMessage(content="ok", tool_calls=[])]})
        service = ChatSessionService(graph=graph, adventure_director=_noop_director())

        result = await service.process_turn(message="hello")

        UUID(result["session_id"])
        self.assertEqual(result["session_id"], graph.last_config["configurable"]["thread_id"])

    @patch("app.services.chat_session_service.trace_chat_result")
    @patch("app.services.chat_session_service.trace_chat_request")
    async def test_process_turn_records_trace_events(self, mock_trace_request, mock_trace_result):
        graph = FakeGraph({"messages": [AIMessage(content="继续推进。", tool_calls=[])]})
        service = ChatSessionService(graph=graph, adventure_director=_noop_director())

        result = await service.process_turn(message="继续", session_id="trace-demo")

        self.assertEqual("继续推进。", result["reply"])
        mock_trace_request.assert_called_once()
        mock_trace_result.assert_called_once()
        self.assertEqual("trace-demo", mock_trace_request.call_args.args[0])
        self.assertEqual("trace-demo", mock_trace_result.call_args.args[0])

    async def test_process_turn_stream_skips_blank_assistant_messages(self):
        graph = FakeGraph({
            "messages": [
                AIMessage(content="\n\n", tool_calls=[]),
                AIMessage(content="格林拔剑。", tool_calls=[]),
            ],
            "phase": "combat",
        })
        service = ChatSessionService(graph=graph, adventure_director=_noop_director())

        events = [
            event
            async for event in service.process_turn_stream(message="继续", session_id="stream-blank-demo")
        ]

        assistant_events = [event for event in events if event.startswith("event: assistant_message")]
        self.assertEqual(1, len(assistant_events))
        self.assertIn("格林拔剑。", assistant_events[0])
        self.assertNotIn("\\n\\n", assistant_events[0])

    async def test_process_turn_appends_reward_announcement_after_model_reply(self):
        graph = FakeGraph({
            "messages": [AIMessage(content="你击败了地精。", tool_calls=[])],
            "phase": "combat",
        })
        service = ChatSessionService(graph=graph, adventure_director=_noop_director())
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

        result = await service.process_turn(message="继续", session_id="reward-demo")

        self.assertIn("你击败了地精。", result["reply"])
        self.assertIn("【经验奖励】你获得 75 XP，已计入角色卡", result["reply"])
        self.assertIn("打败伏击地精并抵达克拉摩窝点后发放。", result["reply"])

    async def test_process_turn_applies_adventure_director_after_graph_run(self):
        graph = FakeGraph({
            "messages": [AIMessage(content="地精倒下，你发现林中有拖拽痕迹。", tool_calls=[])],
            "phase": "exploration",
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "goblin_ambush",
                "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
                "completed_node_ids": [],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "pending_exit_option_ids": [],
            },
        })
        director = FakeAdventureDirector(
            AdventureProgressDecision(
                completed_event_ids=["goblin_ambush_resolved"],
                discovered_clue_ids=["goblin_trail"],
                exit_option_id="investigate_goblin_trail",
                confidence=0.9,
            )
        )
        service = ChatSessionService(graph=graph, adventure_director=director)

        result = await service.process_turn(message="我搜索伏击现场", session_id="adventure-runtime-demo")

        self.assertEqual("地精倒下，你发现林中有拖拽痕迹。", result["reply"])
        self.assertEqual(["goblin_trail"], result["adventure"]["known_clue_ids"])
        self.assertEqual(
            ["goblin_ambush_resolved"],
            result["adventure"]["completed_event_ids"],
        )
        self.assertEqual([], result["adventure"]["pending_exit_option_ids"])
        self.assertEqual("goblin_trail_to_cragmaw_hideout", result["adventure"]["active_node_id"])
        self.assertEqual(1, len(director.calls))
        self.assertEqual("adventure-runtime-demo", director.calls[0]["session_id"])

    async def test_adventure_runtime_appends_node_frame_only_when_active_node_changes(self):
        graph = FakeGraph({
            "messages": [AIMessage(content="地精倒下。", tool_calls=[])],
            "phase": "exploration",
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "goblin_ambush",
                "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
                "completed_node_ids": [],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "pending_exit_option_ids": [],
            },
        })
        director = FakeAdventureDirector(
            AdventureProgressDecision(
                exit_option_id="investigate_goblin_trail",
                transition_kind="advance",
                confidence=0.9,
            )
        )
        service = ChatSessionService(graph=graph, adventure_director=director)

        await service.process_turn(message="追踪地精", session_id="node-frame-demo")

        frame_updates = [
            update
            for update in graph.state_updates
            if update.get("messages")
            and str(update["messages"][0].content).startswith(ADVENTURE_NODE_FRAME_MESSAGE_PREFIX)
        ]
        self.assertEqual(2, len(frame_updates))
        self.assertIn('"node_id":"adventure_hook_meet_me_in_phandalin"', frame_updates[0]["messages"][0].content)
        self.assertIn('"node_id":"goblin_trail_to_cragmaw_hideout"', frame_updates[1]["messages"][0].content)

        duplicate = service._adventure_node_frame_update(SimpleNamespace(values=graph.values), graph.values["adventure"])

        self.assertEqual({}, duplicate)

    async def test_process_turn_passes_full_message_history_to_adventure_director(self):
        graph = FakeGraph({
            "messages": [
                HumanMessage(content="第一轮旧玩家输入"),
                AIMessage(content="第一轮旧主持回复", tool_calls=[]),
                HumanMessage(content="第二轮旧玩家输入"),
                AIMessage(content="第二轮旧主持回复", tool_calls=[]),
                HumanMessage(content="本轮新玩家输入"),
                AIMessage(content="本轮主持回复", tool_calls=[]),
            ],
            "phase": "exploration",
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "goblin_ambush",
                "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
                "completed_node_ids": [],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "pending_exit_option_ids": [],
            },
        })
        director = FakeAdventureDirector()
        service = ChatSessionService(graph=graph, adventure_director=director)

        await service.process_turn(message="本轮新玩家输入", session_id="history-demo")

        self.assertEqual(1, len(director.calls))
        self.assertEqual(
            [
                "第一轮旧玩家输入",
                "第一轮旧主持回复",
                "第二轮旧玩家输入",
                "第二轮旧主持回复",
                "本轮新玩家输入",
                "本轮主持回复",
            ],
            [getattr(message, "content", "") for message in director.calls[0]["recent_messages"]],
        )

    async def test_process_turn_applies_pre_turn_adventure_director_before_graph_run(self):
        graph = FakeGraph({
            "messages": [AIMessage(content="你们沿着三猪小径前进，前方出现两匹倒毙的马。", tool_calls=[])],
            "phase": "exploration",
        })
        graph.values = {
            "messages": [],
            "phase": "exploration",
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "adventure_hook_meet_me_in_phandalin",
                "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin"],
                "completed_node_ids": [],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "pending_exit_option_ids": [],
            },
        }
        director = FakeAdventureDirector(
            pre_turn_decision=AdventurePreTurnDecision(
                exit_option_id="continue_to_ambush",
                confidence=0.92,
                needs_player_choice=False,
            )
        )
        service = ChatSessionService(graph=graph, adventure_director=director)

        result = await service.process_turn(message="一起出发", session_id="pre-turn-demo")

        self.assertEqual("goblin_ambush", result["adventure"]["active_node_id"])
        self.assertEqual("goblin_ambush", graph.state_updates[1]["adventure"]["active_node_id"])
        self.assertEqual(1, len(director.pre_turn_calls))
        self.assertEqual("一起出发", graph.last_input["messages"][0].content)

    async def test_process_turn_keeps_desynced_reply_player_visible_and_records_guardrail(self):
        graph = FakeGraph({
            "messages": [AIMessage(content="你们已经在酒馆救出了冈德伦。", tool_calls=[])],
            "phase": "exploration",
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "goblin_ambush",
                "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
                "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "pending_exit_option_ids": [],
            },
        })
        director = FakeAdventureDirector(
            decision=AdventureProgressDecision(
                desync_detected=True,
                unsupported_claims=["冈德伦已被救出", "酒馆剧情"],
                warning="回到地精伏击节点。",
                reason="主持回复越过当前节点。",
            )
        )
        service = ChatSessionService(graph=graph, adventure_director=director)

        result = await service.process_turn(message="继续", session_id="guardrail-demo")

        self.assertEqual("你们已经在酒馆救出了冈德伦。", result["reply"])
        self.assertIn("adventure_guardrail_warning", graph.state_updates[-1])
        self.assertEqual(["冈德伦已被救出", "酒馆剧情"], graph.state_updates[-1]["adventure_guardrail_warning"]["unsupported_claims"])

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
        service = ChatSessionService(graph=graph, adventure_director=_noop_director())

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
        service = ChatSessionService(graph=graph, adventure_director=_noop_director())

        history = await service.get_history(session_id="demo", limit=10)

        self.assertEqual(
            [
                {"role": "user", "content": "继续战斗"},
                {"role": "assistant", "content": "战斗开始！让我先为哥布林1执行攻击。"},
                {"role": "assistant", "content": "哥布林1失手了，现在轮到哥布林2行动。"},
            ],
            history["messages"],
        )

    async def test_get_history_normalizes_adventure_state_for_frontend(self):
        graph = FakeGraph({"messages": []})
        graph.values = {
            "messages": [],
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "goblin_ambush",
                "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
                "completed_node_ids": [],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "pending_exit_option_ids": [],
            },
        }
        service = ChatSessionService(graph=graph, adventure_director=_noop_director())

        history = await service.get_history(session_id="demo", limit=10)

        self.assertEqual(["adventure_hook_meet_me_in_phandalin", "goblin_ambush"], history["adventure"]["unlocked_node_ids"])
        self.assertEqual(["goblin_ambush"], history["adventure"]["breadcrumb_node_ids"])
        self.assertEqual([], history["adventure"]["deferred_node_ids"])

    async def test_end_player_controlled_turn_uses_tool_without_agent_message(self):
        graph = FakeGraph({})
        graph.values = {
            "messages": [],
            "phase": "combat",
            "player": {
                "id": "player_hero",
                "name": "英雄",
                "side": "player",
                "hp": 12,
                "max_hp": 12,
                "action_available": False,
            },
            "combat": {
                "round": 1,
                "current_actor_id": "player_hero",
                "initiative_order": ["player_hero", "ally_1"],
                "participants": {
                    "ally_1": {
                        "id": "ally_1",
                        "name": "伊莲",
                        "side": "ally",
                        "hp": 9,
                        "max_hp": 9,
                    }
                },
            },
        }
        service = ChatSessionService(graph, adventure_director=_noop_director())

        result = await service.end_player_controlled_turn(session_id="demo", actor_id="player_hero")

        self.assertEqual(result["combat"]["current_actor_id"], "ally_1")
        self.assertIn("当前行动者：伊莲", result["message"])
        self.assertIsNone(graph.last_input)
        self.assertTrue(graph.state_updates)
        flow_message = graph.state_updates[-1]["messages"][0]
        self.assertIsInstance(flow_message, HumanMessage)
        self.assertIn("[系统:前端结束回合]", flow_message.content)
        self.assertIn("next_turn: 第 1 回合", flow_message.content)

    async def test_end_player_controlled_turn_rejects_enemy_actor(self):
        graph = FakeGraph({})
        graph.values = {
            "messages": [],
            "phase": "combat",
            "player": {"id": "player_hero", "name": "英雄", "side": "player", "hp": 12, "max_hp": 12},
            "combat": {
                "round": 1,
                "current_actor_id": "goblin_1",
                "initiative_order": ["goblin_1", "player_hero"],
                "participants": {
                    "goblin_1": {
                        "id": "goblin_1",
                        "name": "Goblin",
                        "side": "enemy",
                        "hp": 7,
                        "max_hp": 7,
                    }
                },
            },
        }
        service = ChatSessionService(graph, adventure_director=_noop_director())

        with self.assertRaises(ValueError):
            await service.end_player_controlled_turn(session_id="demo", actor_id="goblin_1")

    async def test_end_player_controlled_turn_wakes_agent_for_enemy_turn(self):
        graph = FakeGraph({
            "messages": [AIMessage(content="Goblin 眯起眼睛，开始寻找射击角度。")],
        })
        graph.values = {
            "messages": [],
            "phase": "combat",
            "player": {
                "id": "player_hero",
                "name": "英雄",
                "side": "player",
                "hp": 12,
                "max_hp": 12,
            },
            "combat": {
                "round": 1,
                "current_actor_id": "player_hero",
                "initiative_order": ["player_hero", "goblin_1"],
                "participants": {
                    "goblin_1": {
                        "id": "goblin_1",
                        "name": "Goblin",
                        "side": "enemy",
                        "hp": 7,
                        "max_hp": 7,
                        "action_available": True,
                    }
                },
            },
        }
        service = ChatSessionService(graph, adventure_director=_noop_director())

        result = await service.end_player_controlled_turn(session_id="demo", actor_id="player_hero")

        self.assertEqual(result["combat"]["current_actor_id"], "goblin_1")
        self.assertIsNotNone(graph.last_input)
        handoff = graph.last_input["messages"][0]
        self.assertIsInstance(handoff, HumanMessage)
        self.assertIn("[系统:战斗流程接管]", handoff.content)
        self.assertIn("当前行动者是 Goblin [ID:goblin_1]", handoff.content)
        self.assertTrue(any(
            "[系统:前端结束回合]" in getattr(update.get("messages", [None])[0], "content", "")
            for update in graph.state_updates
        ))
        self.assertIn(
            {"type": "assistant_message", "content": "Goblin 眯起眼睛，开始寻找射击角度。"},
            result["events"],
        )


if __name__ == "__main__":
    unittest.main()
