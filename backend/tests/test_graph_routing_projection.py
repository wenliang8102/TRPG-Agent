from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.graph.builder import build_graph
from app.graph.constants import ASSISTANT_NODE, COMBAT_AGENT_MODE, COMBAT_ASSISTANT_NODE, COMBAT_RESOLUTION_NODE, END_NODE, NARRATIVE_AGENT_MODE
from app.graph.edges import route_from_assistant, route_from_combat_resolution, route_from_reaction_resolution, route_from_router, route_from_tool
from app.graph.nodes import combat_assistant_node, combat_resolution_node
from app.graph.state import GraphState
from app.memory.context_assembler import ContextAssembler, trim_model_messages
from app.prompts import get_assistant_system_prompt
from app.services.tools import get_tool_profile
from app.services.tool_service import manage_space, modify_character_state


def _context_assembler() -> ContextAssembler:
    return ContextAssembler()


def _build_system_prompt(state: dict, mode: str) -> str:
    return _context_assembler().build_system_prompt(state, mode, get_assistant_system_prompt(mode))


def _build_model_input_messages(state: dict, mode: str):
    assembler = _context_assembler()
    hud_text = assembler.build_hud_text(state)
    return assembler.build_model_input_messages(state, mode, hud_text)


def _build_combat_brief(state: dict) -> str:
    return _context_assembler()._build_combat_brief(state)


def _build_combat_turn_directive(state: dict) -> str:
    return _context_assembler()._build_combat_turn_directive(state)


def _combat_state(current_actor_id: str) -> dict:
    return {
        "round": 2,
        "current_actor_id": current_actor_id,
        "initiative_order": ["player_hero", "goblin_1"],
        "participants": {
            "goblin_1": {
                "id": "goblin_1",
                "name": "Goblin",
                "side": "enemy",
                "hp": 7,
                "max_hp": 7,
                "ac": 15,
                "attacks": [{"name": "Scimitar"}],
            }
        },
    }


def _player_state() -> dict:
    return {
        "id": "player_hero",
        "name": "英雄",
        "side": "player",
        "hp": 18,
        "max_hp": 18,
        "ac": 16,
        "attacks": [{"name": "Longsword"}],
    }


def test_router_sends_player_turn_combat_to_combat_assistant():
    state = {
        "phase": "combat",
        "combat": _combat_state("player_hero"),
        "player": _player_state(),
        "messages": [HumanMessage(content="我攻击哥布林")],
    }

    assert route_from_router(state) == COMBAT_ASSISTANT_NODE


def test_router_sends_monster_turn_combat_to_combat_assistant():
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
        "messages": [HumanMessage(content="继续")],
    }

    assert route_from_router(state) == COMBAT_ASSISTANT_NODE


def test_tool_route_stops_on_pending_reaction():
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
        "pending_reaction": {"type": "reaction_prompt"},
    }

    assert route_from_tool(state) == END_NODE


def test_combat_resolution_route_returns_combat_assistant_for_player_turn():
    state = {
        "phase": "combat",
        "combat": _combat_state("player_hero"),
        "player": _player_state(),
    }

    assert route_from_combat_resolution(state) == COMBAT_ASSISTANT_NODE


def test_combat_resolution_route_returns_combat_assistant_for_monster_turn():
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
    }

    assert route_from_combat_resolution(state) == COMBAT_ASSISTANT_NODE


def test_tool_route_moves_combat_turn_into_resolution_node_before_assistant():
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
    }

    assert route_from_tool(state) == COMBAT_RESOLUTION_NODE


def test_graph_continues_after_tool_message_until_llm_stops_calling_tools():
    class _LoopingLLMService:
        def __init__(self):
            self.calls = []

        def invoke_with_tools(self, messages, tools, system_prompt, mode):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return AIMessage(
                    content="",
                    tool_calls=[{"name": "request_dice_roll", "args": {"reason": "first", "formula": "1d1"}, "id": "call_1"}],
                )
            if len(self.calls) == 2:
                assert isinstance(messages[-1], ToolMessage)
                return AIMessage(
                    content="",
                    tool_calls=[{"name": "request_dice_roll", "args": {"reason": "second", "formula": "1d1"}, "id": "call_2"}],
                )
            assert isinstance(messages[-1], ToolMessage)
            return AIMessage(content="done", tool_calls=[])

    fake_service = _LoopingLLMService()
    graph = build_graph()

    with patch("app.graph.nodes._get_llm_service", return_value=fake_service):
        result = graph.invoke({"messages": [HumanMessage(content="roll twice")]})

    assert len(fake_service.calls) == 3
    assert result["messages"][-1].content == "done"
    assert sum(isinstance(message, ToolMessage) for message in result["messages"]) == 2


def test_graph_merges_concurrent_space_tool_updates():
    def place_player(state: GraphState) -> dict:
        space = dict(state["space"])
        space["placements"] = {
            "player_hero": {"unit_id": "player_hero", "map_id": "map_existing", "position": {"x": 10, "y": 10}},
        }
        return {"space": space}

    def place_goblin(state: GraphState) -> dict:
        space = dict(state["space"])
        space["placements"] = {
            "goblin_1": {"unit_id": "goblin_1", "map_id": "map_existing", "position": {"x": 25, "y": 10}},
        }
        return {"space": space}

    graph_builder = StateGraph(GraphState)
    graph_builder.add_node("place_player", place_player)
    graph_builder.add_node("place_goblin", place_goblin)
    graph_builder.add_edge(START, "place_player")
    graph_builder.add_edge(START, "place_goblin")
    graph_builder.add_edge("place_player", END)
    graph_builder.add_edge("place_goblin", END)
    graph = graph_builder.compile()

    result = graph.invoke({
        "space": {
            "active_map_id": "map_existing",
            "maps": {"map_existing": {"id": "map_existing", "name": "旧地图", "width": 80, "height": 60}},
            "placements": {},
        },
    })

    assert "map_existing" in result["space"]["maps"]
    assert result["space"]["placements"]["player_hero"]["position"] == {"x": 10.0, "y": 10.0}
    assert result["space"]["placements"]["goblin_1"]["position"] == {"x": 25.0, "y": 10.0}


def test_assistant_executes_only_first_tool_call_when_model_returns_parallel_calls():
    class _ParallelSpaceLLMService:
        def __init__(self):
            self.calls = 0

        def invoke_with_tools(self, messages, tools, system_prompt, mode):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "manage_space",
                            "args": {
                                "action": "create_map",
                                "payload": {"name": "伏击地点", "width": 80, "height": 60},
                            },
                            "id": "call_map",
                        },
                        {
                            "name": "manage_space",
                            "args": {
                                "action": "place_unit",
                                "payload": {"unit_id": "player_hero", "x": 10, "y": 10},
                            },
                            "id": "call_player",
                        },
                    ],
                )
            return AIMessage(content="地图已创建。", tool_calls=[])

    fake_service = _ParallelSpaceLLMService()
    graph = build_graph()

    with patch("app.graph.nodes._get_llm_service", return_value=fake_service):
        result = graph.invoke({"messages": [HumanMessage(content="创建地图并放置玩家")]})

    assert len(result["space"]["maps"]) == 1
    assert result["space"]["placements"] == {}
    assert sum(isinstance(message, ToolMessage) for message in result["messages"]) == 1


def test_assistant_route_no_longer_enters_summarize_on_long_history():
    state = {
        "messages": [HumanMessage(content=f"消息 {index}") for index in range(70)] + [AIMessage(content="收到。", tool_calls=[])],
    }

    assert route_from_assistant(state) == END_NODE


def test_reaction_resolution_moves_into_resolution_node_when_combat_continues():
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
    }

    assert route_from_reaction_resolution(state) == COMBAT_RESOLUTION_NODE


def test_model_projection_summarizes_tool_messages_without_mutating_transcript():
    tool_message = ToolMessage(
        content="Goblin 使用 [Scimitar] 攻击 英雄!\n伤害骰: 1d6+2 → 5 点伤害\n英雄 HP: 18 → 13",
        tool_call_id="call_1",
        name="attack_action",
    )
    state = {
        "phase": "combat",
        "combat": _combat_state("player_hero"),
        "player": _player_state(),
        "messages": [
            HumanMessage(content="我攻击哥布林"),
            AIMessage(content="", tool_calls=[{"name": "attack_action", "args": {"attacker_id": "player_hero"}, "id": "call_1"}]),
            tool_message,
        ],
    }

    projected_messages = _build_model_input_messages(state, COMBAT_AGENT_MODE)

    assert tool_message.content.startswith("Goblin 使用 [Scimitar]")
    assert isinstance(projected_messages[-3], SystemMessage)
    assert "<runtime_state" in projected_messages[-3].content
    assert 'source="hud"' in projected_messages[-3].content
    assert "状态快照" in projected_messages[-3].content
    assert isinstance(projected_messages[-2], AIMessage)
    assert projected_messages[-1].content.startswith("[工具:attack_action]")
    assert state["messages"][-1].content == tool_message.content


def test_combat_projection_trims_more_aggressively_than_full_history():
    messages = [HumanMessage(content=f"消息 {index}") for index in range(60)]

    trimmed_messages = trim_model_messages(messages, COMBAT_AGENT_MODE)

    assert len(trimmed_messages) == 32
    assert messages[0].content == "消息 0"


def test_post_combat_projection_collapses_archived_battle_to_single_summary():
    state = {
        "phase": "exploration",
        "combat": None,
        "player": _player_state(),
        "messages": [
            HumanMessage(content="我们进入洞穴。"),
            ToolMessage(content="战斗开始！第 1 回合。", tool_call_id="call_1", name="start_combat"),
            AIMessage(content="", tool_calls=[{"name": "attack_action", "args": {"attacker_id": "player_hero"}, "id": "call_2"}]),
            ToolMessage(content="Goblin 使用 [Scimitar] 攻击 英雄!\n英雄 HP: 12 → 9", tool_call_id="call_2", name="attack_action"),
            ToolMessage(content="共进行了 2 回合。 存活: 英雄 倒下: Goblin", tool_call_id="call_3", name="end_combat"),
            HumanMessage(content="我检查哥布林尸体。"),
        ],
        "combat_archives": [
            {
                "summary": "英雄在 2 回合内击败哥布林，消耗 1 次护盾。",
                "start_index": 1,
                "end_index": 4,
            }
        ],
    }

    projected_messages = _build_model_input_messages(state, NARRATIVE_AGENT_MODE)

    assert len(projected_messages) == 4
    assert projected_messages[1].content.startswith("[系统:战斗归档]")
    assert "英雄在 2 回合内击败哥布林" in projected_messages[1].content
    assert "Goblin 使用 [Scimitar]" not in projected_messages[1].content
    assert projected_messages[-1].content.startswith("我检查哥布林尸体。")
    assert isinstance(projected_messages[-2], SystemMessage)
    assert "<runtime_state" in projected_messages[-2].content
    assert 'source="hud"' in projected_messages[-2].content
    assert "状态快照" in projected_messages[-2].content


def test_tool_profiles_split_exploration_and_combat_visibility():
    narrative_tools = {tool.name for tool in get_tool_profile("narrative")}
    combat_tools = {tool.name for tool in get_tool_profile("combat")}

    assert "load_skill" not in narrative_tools
    assert "load_skill" not in combat_tools
    assert "weather" not in narrative_tools
    assert "weather" not in combat_tools
    assert "start_combat" in narrative_tools
    assert "start_combat" not in combat_tools
    assert "attack_action" in combat_tools
    assert "attack_action" not in narrative_tools
    assert "manage_space" in narrative_tools
    assert "manage_space" in combat_tools
    assert "create_plane_map" not in narrative_tools
    assert "switch_plane_map" not in narrative_tools
    assert "place_unit" not in narrative_tools
    assert "move_unit" not in combat_tools
    assert "measure_distance" not in combat_tools
    assert "query_units_in_radius" not in combat_tools
    assert "grant_xp" not in narrative_tools
    assert "level_up" not in narrative_tools
    assert "choose_arcane_tradition" not in narrative_tools
    assert "apply_condition" not in combat_tools
    assert "remove_condition" not in combat_tools
    assert ASSISTANT_NODE == "assistant"


def test_modify_character_state_help_returns_skill_instructions():
    result = modify_character_state.invoke({
        "name": "modify_character_state",
        "args": {"action": "help"},
        "id": "state-help-call",
        "type": "tool_call",
    })

    content = result.update["messages"][0].content
    assert "角色状态调整技能" in content
    assert "modify_character_state" in content
    assert "character_progression" in content
    assert 'action="level_up"' not in content


def test_modify_character_state_progression_help_returns_growth_skill():
    result = modify_character_state.invoke({
        "name": "modify_character_state",
        "args": {"action": "help", "payload": {"topic": "progression"}},
        "id": "progression-help-call",
        "type": "tool_call",
    })

    content = result.update["messages"][0].content
    assert "角色成长与子职技能" in content
    assert 'action="level_up"' in content
    assert 'action="choose_fighter_archetype"' in content


def test_manage_space_help_returns_skill_instructions():
    result = manage_space.invoke({
        "name": "manage_space",
        "args": {"action": "help"},
        "id": "space-help-call",
        "type": "tool_call",
    })

    content = result.update["messages"][0].content
    assert "平面空间管理技能" in content
    assert "manage_space" in content
    assert 'action="query_radius"' in content


def test_combat_brief_includes_conditions_attacks_and_scene_stakes():
    poisoned_goblin = _combat_state("goblin_1")
    poisoned_goblin["participants"]["goblin_1"]["conditions"] = [{"id": "poisoned", "name_cn": "中毒"}]
    state = {
        "phase": "combat",
        "combat": poisoned_goblin,
        "player": _player_state(),
        "scene_summary": "盗匪正在拖走人质，必须尽快压制火力。",
    }

    brief = _build_combat_brief(state)

    assert "当前局势/战斗 stakes" in brief
    assert "中毒" in brief
    assert "Scimitar" in brief


def test_combat_turn_directive_switches_between_monster_and_player_turns():
    monster_state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
    }
    player_state = {
        "phase": "combat",
        "combat": _combat_state("player_hero"),
        "player": _player_state(),
    }

    monster_directive = _build_combat_turn_directive(monster_state)
    player_directive = _build_combat_turn_directive(player_state)

    assert "怪物/NPC" in monster_directive
    assert "可执行动作" in monster_directive
    assert "approach_unit" in monster_directive
    assert "玩家单位" in player_directive
    assert "玩家最新意图" in player_directive


@patch("app.graph.nodes.finish_llm_trace")
@patch("app.graph.nodes.start_llm_trace", return_value=("invoke-1", "2026-04-26T12:00:00+08:00"))
@patch("app.graph.nodes._get_llm_service")
def test_combat_assistant_records_full_prompt_trace(mock_get_llm_service, mock_start_trace, mock_finish_trace):
    mock_get_llm_service.return_value.invoke_with_tools.return_value = AIMessage(content="哥布林向你逼近。", tool_calls=[])
    state = {
        "session_id": "trace-session",
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
        "messages": [HumanMessage(content="继续战斗")],
    }

    result = combat_assistant_node(state)

    assert result["output"] == "哥布林向你逼近。"
    assert mock_start_trace.called
    assert mock_finish_trace.called
    start_kwargs = mock_start_trace.call_args.kwargs
    assert start_kwargs["system_prompt"]
    assert any("继续战斗" in str(message.content) for message in start_kwargs["messages"])
    assert "<runtime_state" in str(start_kwargs["messages"][0].content)


def test_narrative_system_prompt_excludes_combat_only_guidelines():
    prompt = _build_system_prompt({"messages": []}, NARRATIVE_AGENT_MODE)

    assert "探索代理补充准则" in prompt
    assert "战斗代理补充准则" not in prompt
    assert "战斗阶段保持简洁播报" not in prompt
    assert "工具返回是客观事实来源" in prompt
    assert "不要使用图标、emoji" in prompt
    assert "不要主动输出整块角色卡" in prompt
    assert 'action="level_up"' not in prompt
    assert "character_state_management" not in prompt


def test_combat_system_prompt_includes_combat_only_guidelines():
    prompt = _build_system_prompt({"messages": []}, COMBAT_AGENT_MODE)

    assert "战斗代理补充准则" in prompt
    assert "战斗阶段保持简洁播报" in prompt
    assert "不要在没有工具结果的情况下描述命中" in prompt
    assert "不要等待用户继续发话" in prompt
    assert "工具返回是客观事实来源" in prompt
    assert "不要主动输出整块角色卡" in prompt


def test_combat_assistant_node_invokes_llm_with_monster_turn_directive_and_combat_tools():
    class _FakeLLMService:
        def __init__(self):
            self.calls = []

        def invoke_with_tools(self, messages, tools, system_prompt, mode):
            self.calls.append({
                "messages": messages,
                "tools": tools,
                "system_prompt": system_prompt,
                "mode": mode,
            })
            return AIMessage(
                content="",
                tool_calls=[{"name": "attack_action", "args": {"attacker_id": "goblin_1", "target_id": "player_hero"}, "id": "call_1"}],
            )

    fake_service = _FakeLLMService()
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
        "scene_summary": "哥布林正试图拖走祭司，必须立刻拦截。",
        "messages": [HumanMessage(content="继续")],
    }

    with patch("app.graph.nodes._get_llm_service", return_value=fake_service):
        result = combat_assistant_node(state)

    assert result["messages"][0].tool_calls[0]["name"] == "attack_action"
    llm_call = fake_service.calls[0]
    assert llm_call["mode"] == COMBAT_AGENT_MODE
    assert {tool.name for tool in llm_call["tools"]} == {tool.name for tool in get_tool_profile("combat")}
    assert "当前是怪物/NPC Goblin [ID:goblin_1] 的回合" in llm_call["system_prompt"]
    assert "不要等待用户继续发话" in llm_call["system_prompt"]
    assert "哥布林正试图拖走祭司" in llm_call["system_prompt"]
    assert "start_combat" not in {tool.name for tool in llm_call["tools"]}


def test_combat_resolution_node_interrupts_and_ends_battle_on_player_death():
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": {
            **_player_state(),
            "hp": 0,
            "max_hp": 18,
        },
        "messages": [ToolMessage(content="Goblin 使用 [Scimitar] 攻击 英雄!\n英雄 HP: 4 → 0", tool_call_id="call_1")],
        "active_combat_message_start": 0,
        "hp_changes": [{"id": "player_hero", "old_hp": 4, "new_hp": 0, "max_hp": 18}],
    }

    with patch("langgraph.types.interrupt", return_value="revive"):
        result = combat_resolution_node(state)

    assert result["combat"] is None
    assert result["phase"] == "exploration"
    assert result["player"]["hp"] == 9
    assert result["messages"][0].content == "[系统] 玩家角色倒下，战斗结束。"
    assert result["hp_changes"] == []
    assert result["active_combat_message_start"] is None
    assert result["combat_archives"][0]["start_index"] == 0
    assert result["combat_archives"][0]["end_index"] == 1
    assert "战斗以玩家角色倒下告终" in result["combat_archives"][0]["summary"]
