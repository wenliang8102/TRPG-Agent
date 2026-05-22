from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.graph.builder import build_graph
from app.graph.constants import (
    ASSISTANT_NODE,
    COMBAT_AGENT_MODE,
    COMBAT_ASSISTANT_NODE,
    COMBAT_END_NODE,
    COMBAT_EXECUTOR_NODE,
    COMBAT_START_NODE,
    COMBAT_RESOLUTION_NODE,
    DEATH_SAVE_PAUSE_NODE,
    END_NODE,
    NARRATIVE_AGENT_MODE,
    STATE_DEATH_SAVE_PAUSE_TURN_KEY,
)
from app.graph.edges import route_from_assistant, route_from_combat_end, route_from_combat_resolution, route_from_combat_start, route_from_reaction_resolution, route_from_router, route_from_tool
from app.graph.nodes import combat_assistant_node, combat_end_node, combat_executor_node, combat_resolution_node, combat_start_node, death_save_pause_node
from app.graph.state import GraphState
from app.memory.context_assembler import ContextAssembler, trim_model_messages
from app.prompts import get_assistant_system_prompt
from app.services.tools import get_tool_profile
from app.services.tools.combat_tools import delegate_combat_turn, prepare_combat_end
from app.services.tools.character_tools import modify_character_state
from app.services.tools.space_tools import manage_space


def _context_assembler() -> ContextAssembler:
    return ContextAssembler()


def _build_system_prompt(state: dict, mode: str) -> str:
    return _context_assembler().build_system_prompt(state, mode, get_assistant_system_prompt(mode))


def _build_model_input_messages(state: dict, mode: str):
    assembler = _context_assembler()
    hud_text = assembler.build_hud_text(state)
    runtime_state_text = assembler.build_runtime_state_text(state, mode, hud_text)
    return assembler.build_model_input_messages(state, mode, runtime_state_text)


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


def _invoke_tool(tool_func, *, tool_input: dict) -> object:
    tool_call = {
        "name": tool_func.name,
        "args": tool_input,
        "id": "test-call-id",
        "type": "tool_call",
    }
    return tool_func.invoke(tool_call)


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


def test_router_prioritizes_pending_combat_start_workflow():
    state = {
        "messages": [HumanMessage(content="进入战斗")],
        "pending_combat_start": {"combatant_ids": ["goblin_1"], "surprised_ids": ["player"]},
    }

    assert route_from_router(state) == COMBAT_START_NODE


def test_tool_route_executes_pending_combat_start_before_assistant():
    state = {
        "messages": [ToolMessage(content="计划已提交", tool_call_id="call_1")],
        "pending_combat_start": {"combatant_ids": ["goblin_1"], "surprised_ids": []},
    }

    assert route_from_tool(state) == COMBAT_START_NODE


def test_router_prioritizes_pending_combat_executor_workflow():
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
        "messages": [ToolMessage(content="已委托", tool_call_id="call_1")],
        "pending_combat_executor": {"actor_id": "goblin_1", "instruction": "攻击玩家"},
    }

    assert route_from_router(state) == COMBAT_EXECUTOR_NODE
    assert route_from_tool(state) == COMBAT_EXECUTOR_NODE


def test_router_prioritizes_pending_combat_end_workflow():
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
        "messages": [ToolMessage(content="已提交结束计划", tool_call_id="call_1")],
        "pending_combat_end": {"outcomes": [{"unit_id": "goblin_1", "result": "captured"}]},
    }

    assert route_from_router(state) == COMBAT_END_NODE
    assert route_from_tool(state) == COMBAT_END_NODE


def test_delegate_combat_turn_writes_pending_executor_request():
    result = _invoke_tool(
        delegate_combat_turn,
        tool_input={"actor_id": "goblin_1", "instruction": "靠近并攻击玩家。"},
    )

    assert result.update["pending_combat_executor"] == {
        "actor_id": "goblin_1",
        "instruction": "靠近并攻击玩家。",
    }
    assert result.update["messages"][0].additional_kwargs["hidden_from_ui"] is True


def test_delegate_combat_turn_accepts_player_instruction_handoff():
    result = _invoke_tool(
        delegate_combat_turn,
        tool_input={
            "actor_id": "player_hero",
            "instruction": "玩家说：我攻击最近的地精。裁定：player_hero 用长剑攻击 goblin_1。",
        },
    )

    assert result.update["pending_combat_executor"] == {
        "actor_id": "player_hero",
        "instruction": "玩家说：我攻击最近的地精。裁定：player_hero 用长剑攻击 goblin_1。",
    }


def test_prepare_combat_end_writes_pending_workflow_request():
    result = _invoke_tool(
        prepare_combat_end,
        tool_input={"outcomes": [{"unit_id": "goblin_1", "result": "captured"}], "reason": "地精投降。"},
    )

    assert result.update["pending_combat_end"] == {
        "outcomes": [{"unit_id": "goblin_1", "result": "captured"}],
        "reason": "地精投降。",
    }
    assert result.update["messages"][0].additional_kwargs["hidden_from_ui"] is True


def test_tool_route_stops_on_pending_reaction():
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
        "pending_reaction": {"type": "reaction_prompt"},
    }

    assert route_from_tool(state) == END_NODE


def test_combat_start_route_returns_combat_assistant_after_success():
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
    }

    assert route_from_combat_start(state) == COMBAT_ASSISTANT_NODE


def test_combat_end_route_returns_narrative_assistant_after_success():
    state = {
        "phase": "exploration",
        "combat": None,
    }

    assert route_from_combat_end(state) == ASSISTANT_NODE


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


def test_router_pauses_before_player_death_save_turn():
    player = {**_player_state(), "hp": 0, "death_save_successes": 1, "death_save_failures": 1}
    state = {
        "phase": "combat",
        "combat": _combat_state("player_hero"),
        "player": player,
        "messages": [HumanMessage(content="继续")],
    }

    assert route_from_router(state) == DEATH_SAVE_PAUSE_NODE


def test_router_continues_after_death_save_pause_for_same_turn():
    player = {**_player_state(), "hp": 0, "death_save_successes": 1, "death_save_failures": 1}
    state = {
        "phase": "combat",
        "combat": _combat_state("player_hero"),
        "player": player,
        "messages": [HumanMessage(content="继续")],
        STATE_DEATH_SAVE_PAUSE_TURN_KEY: "detached:2:player_hero",
    }

    assert route_from_router(state) == COMBAT_ASSISTANT_NODE


def test_combat_resolution_pauses_when_next_turn_reaches_downed_player():
    player = {**_player_state(), "hp": 0, "death_save_successes": 0, "death_save_failures": 0}
    state = {
        "phase": "combat",
        "combat": _combat_state("player_hero"),
        "player": player,
    }

    assert route_from_combat_resolution(state) == DEATH_SAVE_PAUSE_NODE


def test_death_save_pause_node_marks_current_turn():
    player = {**_player_state(), "hp": 0, "death_save_successes": 2, "death_save_failures": 0}
    state = {
        "phase": "combat",
        "combat": _combat_state("player_hero"),
        "player": player,
    }

    result = death_save_pause_node(state)

    assert result[STATE_DEATH_SAVE_PAUSE_TURN_KEY] == "detached:2:player_hero"
    assert "在掷死亡豁免之前" in result["messages"][0].content
    assert "2 成功 / 0 失败" in result["messages"][0].content


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
                assert isinstance(messages[-2], ToolMessage)
                assert isinstance(messages[-1], HumanMessage)
                assert str(messages[-1].content).startswith("[系统:运行状态帧]")
                return AIMessage(
                    content="",
                    tool_calls=[{"name": "request_dice_roll", "args": {"reason": "second", "formula": "1d1"}, "id": "call_2"}],
                )
            assert isinstance(messages[-2], ToolMessage)
            assert isinstance(messages[-1], HumanMessage)
            assert str(messages[-1].content).startswith("[系统:运行状态帧]")
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


def test_combat_start_node_executes_pending_plan_with_surprise_reason():
    player = _player_state()
    goblin = _combat_state("goblin_1")["participants"]["goblin_1"]
    state = {
        "player": player,
        "scene_units": {"goblin_1": goblin},
        "space": {
            "active_map_id": "map_1",
            "maps": {"map_1": {"id": "map_1", "name": "战斗地图", "width": 100, "height": 100, "grid_size": 5}},
            "placements": {
                "player_hero": {"unit_id": "player_hero", "map_id": "map_1", "position": {"x": 0, "y": 0}},
                "goblin_1": {"unit_id": "goblin_1", "map_id": "map_1", "position": {"x": 20, "y": 0}},
            },
        },
        "pending_combat_start": {
            "combatant_ids": ["goblin_1"],
            "surprised_ids": ["player"],
            "reason": "地精伏击成功，玩家先攻劣势。",
        },
    }

    result = combat_start_node(state)

    assert result["pending_combat_start"] is None
    assert result["phase"] == "combat"
    assert "goblin_1" in result["combat"]["participants"]
    assert result["player"]["surprised"] is True
    assert "开战裁定：地精伏击成功" in result["messages"][0].content
    assert "战斗开始！第 1 回合" in result["messages"][0].content


def test_combat_start_node_applies_llm_map_and_placements_before_initiative():
    player = _player_state()
    goblin = _combat_state("goblin_1")["participants"]["goblin_1"]
    state = {
        "player": player,
        "scene_units": {"goblin_1": goblin},
        "pending_combat_start": {
            "combatant_ids": ["goblin_1"],
            "surprised_ids": [],
            "map_plan": {"action": "create", "map_id": "ambush_road", "name": "三猪小径伏击", "width": 150, "height": 120},
            "placements": [
                {"unit_id": "player", "x": 25, "y": 60},
                {"unit_id": "goblin_1", "x": 80, "y": 45},
            ],
            "reason": "道路两侧灌木适合伏击。",
        },
    }

    result = combat_start_node(state)

    assert result["phase"] == "combat"
    assert result["space"]["active_map_id"] == "ambush_road"
    assert result["space"]["placements"]["player_hero"]["position"] == {"x": 25.0, "y": 60.0}
    assert result["space"]["placements"]["goblin_1"]["position"] == {"x": 80.0, "y": 45.0}
    assert result["combat"]["current_actor_id"] in {"player_hero", "goblin_1"}


def test_combat_start_node_rejects_out_of_bounds_llm_placement():
    player = _player_state()
    goblin = _combat_state("goblin_1")["participants"]["goblin_1"]
    state = {
        "player": player,
        "scene_units": {"goblin_1": goblin},
        "pending_combat_start": {
            "combatant_ids": ["goblin_1"],
            "map_plan": {"action": "create", "map_id": "tight_room", "name": "狭窄石室", "width": 30, "height": 30},
            "placements": [
                {"unit_id": "player", "x": 10, "y": 10},
                {"unit_id": "goblin_1", "x": 80, "y": 10},
            ],
        },
    }

    result = combat_start_node(state)

    assert result["pending_combat_start"] is None
    assert "超出地图" in result["messages"][0].content
    assert "combat" not in result


def test_combat_end_node_awards_xp_for_captured_enemy():
    player = _player_state()
    player["xp"] = 0
    combat = _combat_state("goblin_1")
    combat["participants"]["goblin_1"]["hp"] = 3
    combat["participants"]["goblin_1"]["challenge_rating"] = "1/4"
    state = {
        "phase": "combat",
        "player": player,
        "combat": combat,
        "scene_units": {"goblin_1": combat["participants"]["goblin_1"]},
        "space": {
            "active_map_id": "road",
            "maps": {"road": {"id": "road", "name": "小路", "width": 60, "height": 40, "grid_size": 5}},
            "placements": {
                "goblin_1": {"unit_id": "goblin_1", "map_id": "road", "position": {"x": 10, "y": 10}},
            },
        },
        "pending_combat_end": {
            "outcomes": [{"unit_id": "goblin_1", "result": "captured"}],
            "reason": "地精放下武器投降。",
        },
    }

    result = combat_end_node(state)

    assert result["pending_combat_end"] is None
    assert result["phase"] == "exploration"
    assert result["combat"] is None
    assert result["player"]["xp"] == 50
    assert result["departed_units"]["goblin_1"]["departure_reason"] == "defeated"
    assert "goblin_1" not in result["space"]["placements"]
    assert "战斗收束裁定：地精放下武器投降。" in result["messages"][0].content
    assert "战斗 XP: +50" in result["messages"][0].content


def test_combat_end_node_blocks_unclassified_living_enemy():
    combat = _combat_state("goblin_1")
    combat["participants"]["goblin_1"]["hp"] = 3
    state = {
        "phase": "combat",
        "player": _player_state(),
        "combat": combat,
        "pending_combat_end": {
            "outcomes": [],
            "reason": "战斗结束。",
        },
    }

    result = combat_end_node(state)

    assert result["pending_combat_end"] is None
    assert "仍存活的敌方单位缺少结局分类" in result["messages"][0].content
    assert "captured/surrendered/subdued/defeated" in result["messages"][0].content
    assert "combat" not in result


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
    assert isinstance(projected_messages[-2], AIMessage)
    assert projected_messages[-1].content.startswith("[工具:attack_action]")
    assert state["messages"][-1].content == tool_message.content


def test_model_projection_keeps_direct_end_turn_flow_frame():
    flow_message = HumanMessage(content=(
        "[系统:前端结束回合]\n"
        "player_hero 已通过前端按钮结束回合；后端已执行 next_turn。\n"
        "next_turn: 第 1 回合 — 当前行动者：Goblin [ID: goblin_1] (HP: 7/7)"
    ))
    state = {
        "phase": "combat",
        "combat": _combat_state("goblin_1"),
        "player": _player_state(),
        "messages": [flow_message],
    }

    projected_messages = _build_model_input_messages(state, COMBAT_AGENT_MODE)

    assert projected_messages[-1].content.startswith("[系统:前端结束回合]")
    assert "next_turn: 第 1 回合" in projected_messages[-1].content


def test_model_projection_keeps_workflow_combat_end_result_without_tool_prefix_breakage():
    """workflow 直接写入的 ToolMessage 必须转成事实消息，不能留下悬空工具结果。"""
    end_message = ToolMessage(
        content="战斗收束裁定：地精投降。\n共进行了 2 回合。 存活: 英雄 离场: Goblin 战斗 XP: +50。",
        tool_call_id="workflow-combat-end",
        name="end_combat",
    )
    state = {
        "phase": "exploration",
        "combat": None,
        "player": _player_state(),
        "messages": [HumanMessage(content="我接受投降。"), end_message],
    }

    projected_messages = _build_model_input_messages(state, NARRATIVE_AGENT_MODE)

    assert not any(isinstance(message, ToolMessage) for message in projected_messages)
    assert isinstance(projected_messages[-1], HumanMessage)
    assert "战斗收束裁定：地精投降" in projected_messages[-1].content
    assert "战斗 XP: +50" in projected_messages[-1].content


def test_combat_projection_keeps_full_history_under_large_context_budget():
    messages = [HumanMessage(content=f"消息 {index}") for index in range(60)]

    trimmed_messages = trim_model_messages(messages, COMBAT_AGENT_MODE)

    assert len(trimmed_messages) == 60
    assert trimmed_messages[0].content == "消息 0"


def test_combat_projection_keeps_full_prelude_and_active_battle_span_under_budget():
    messages = [
        HumanMessage(content=f"旧探索 {index}")
        for index in range(12)
    ]
    messages.extend([
        AIMessage(content="", tool_calls=[{"name": "start_combat", "args": {"combatant_ids": ["goblin_1"]}, "id": "call_start"}]),
        ToolMessage(content="战斗开始！第 1 回合。", tool_call_id="call_start", name="start_combat"),
        AIMessage(content="", tool_calls=[{"name": "attack_action", "args": {"attacker_id": "goblin_1"}, "id": "call_attack"}]),
        ToolMessage(content="Goblin 攻击英雄。", tool_call_id="call_attack", name="attack_action"),
        HumanMessage(content="我反击。"),
    ])

    trimmed_messages = trim_model_messages(
        messages,
        COMBAT_AGENT_MODE,
        state={"active_combat_message_start": 12},
    )

    assert [message.content for message in trimmed_messages[:12]] == [f"旧探索 {index}" for index in range(12)]
    assert isinstance(trimmed_messages[12], AIMessage)
    assert trimmed_messages[12].tool_calls[0]["name"] == "start_combat"
    assert trimmed_messages[-1].content == "我反击。"


def test_combat_budget_trim_protects_active_battle_span():
    messages = [HumanMessage(content="旧探索 " + ("x" * 50_000)) for _ in range(40)]
    combat_start = len(messages)
    messages.extend([
        AIMessage(content="", tool_calls=[{"name": "start_combat", "args": {"combatant_ids": ["wolf_1"]}, "id": "call_start"}]),
        ToolMessage(content="战斗开始！第 1 回合。", tool_call_id="call_start", name="start_combat"),
        HumanMessage(content="继续战斗。"),
    ])

    trimmed_messages = trim_model_messages(
        messages,
        COMBAT_AGENT_MODE,
        state={"active_combat_message_start": combat_start},
    )

    assert any(isinstance(message, AIMessage) and message.tool_calls[0]["name"] == "start_combat" for message in trimmed_messages)
    assert trimmed_messages[-1].content == "继续战斗。"


def test_combat_projection_falls_back_to_detecting_start_combat_tool_message_under_budget():
    messages = [
        HumanMessage(content=f"旧探索 {index}")
        for index in range(8)
    ]
    messages.extend([
        AIMessage(content="", tool_calls=[{"name": "start_combat", "args": {"combatant_ids": ["wolf_1"]}, "id": "call_start"}]),
        ToolMessage(content="战斗开始！第 1 回合。", tool_call_id="call_start", name="start_combat"),
        HumanMessage(content="继续战斗。"),
    ])

    trimmed_messages = trim_model_messages(messages, COMBAT_AGENT_MODE, state={})

    assert [message.content for message in trimmed_messages[:8]] == [f"旧探索 {index}" for index in range(8)]
    assert isinstance(trimmed_messages[8], AIMessage)
    assert trimmed_messages[8].tool_calls[0]["name"] == "start_combat"


def test_post_combat_projection_keeps_full_battle_history_after_archive_metadata():
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
    projected_text = "\n".join(str(message.content) for message in projected_messages)

    assert "[系统:战斗归档]" not in projected_text
    assert "战斗开始！第 1 回合。" in projected_text
    assert "Goblin 使用 [Scimitar]" in projected_text
    assert "共进行了 2 回合。 存活: 英雄 倒下: Goblin" in projected_text
    assert projected_messages[-1].content.startswith("我检查哥布林尸体。")


def test_post_combat_projection_keeps_start_combat_record_even_if_archive_metadata_exists():
    state = {
        "phase": "exploration",
        "combat": None,
        "player": _player_state(),
        "messages": [
            HumanMessage(content="继续前进。"),
            AIMessage(content="我应该正式启动战斗流程。", tool_calls=[{"name": "request_dice_roll", "args": {}, "id": "call_roll"}]),
            ToolMessage(content="突袭检定 raw=12", tool_call_id="call_roll", name="request_dice_roll"),
            AIMessage(content="现在进入正式战斗！", tool_calls=[{"name": "start_combat", "args": {"combatant_ids": ["goblin_1"]}, "id": "call_start"}]),
            ToolMessage(content="战斗开始！第 1 回合。", tool_call_id="call_start", name="start_combat"),
            AIMessage(content="", tool_calls=[{"name": "end_combat", "args": {}, "id": "call_end"}]),
            ToolMessage(content="共进行了 1 回合。 存活: 英雄 倒下: Goblin", tool_call_id="call_end", name="end_combat"),
            HumanMessage(content="我检查路障。"),
        ],
        "combat_archives": [
            {
                "summary": "地精伏击已经结束，英雄击败了拦路地精。",
                "start_index": 3,
                "end_index": 6,
            }
        ],
    }

    projected_messages = _build_model_input_messages(state, NARRATIVE_AGENT_MODE)
    projected_text = "\n".join(str(message.content) for message in projected_messages)

    assert "[系统:战斗归档]" not in projected_text
    assert "正式启动战斗流程" in projected_text
    assert "突袭检定 raw=12" in projected_text
    assert "现在进入正式战斗" in projected_text
    assert "战斗开始！第 1 回合。" in projected_text
    assert "共进行了 1 回合。 存活: 英雄 倒下: Goblin" in projected_text
    assert projected_messages[-1].content == "我检查路障。"


def test_tool_profiles_split_exploration_and_combat_visibility():
    narrative_tools = {tool.name for tool in get_tool_profile("narrative")}
    combat_tools = {tool.name for tool in get_tool_profile("combat")}

    assert "load_skill" not in narrative_tools
    assert "load_skill" not in combat_tools
    assert "weather" not in narrative_tools
    assert "weather" not in combat_tools
    assert "manage_adventure" not in narrative_tools
    assert "manage_adventure" not in combat_tools
    assert "load_adventure_node" not in narrative_tools
    assert "search_adventure_nodes" not in narrative_tools
    assert "switch_adventure_node" not in narrative_tools
    assert "reveal_adventure_clue" not in narrative_tools
    assert "mark_adventure_event" not in narrative_tools
    assert "advance_adventure" not in narrative_tools
    assert "claim_adventure_reward" in narrative_tools
    assert "claim_adventure_reward" not in combat_tools
    assert "manage_scene_units" in narrative_tools
    assert "manage_scene_units" in combat_tools
    assert "end_combat" not in narrative_tools
    assert "end_combat" not in combat_tools
    assert "prepare_combat_end" not in narrative_tools
    assert "prepare_combat_end" in combat_tools
    assert "prepare_combat_start" in narrative_tools
    assert "prepare_combat_start" not in combat_tools
    assert "spawn_ally" not in narrative_tools
    assert "spawn_monsters" not in narrative_tools
    assert "clear_dead_units" not in narrative_tools
    assert "start_combat" not in narrative_tools
    assert "remove_unit" not in combat_tools
    assert "start_combat" not in combat_tools
    assert "take_rest" in narrative_tools
    assert "take_rest" not in combat_tools
    assert "use_class_feature" not in narrative_tools
    assert "use_class_feature" not in combat_tools
    assert "attack_action" not in combat_tools
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


def test_tool_profiles_expose_only_current_recommended_entries_in_stable_order():
    """模型可见工具必须保持当前推荐入口和稳定顺序，历史工具只给 ToolNode。"""
    assert [tool.name for tool in get_tool_profile("narrative")] == [
        "request_dice_roll",
        "load_character_profile",
        "modify_character_state",
        "manage_scene_units",
        "prepare_combat_start",
        "cast_spell",
        "manage_inventory",
        "use_class_action",
        "inspect_unit",
        "consult_rules_handbook",
        "take_rest",
        "manage_space",
        "claim_adventure_reward",
    ]
    assert [tool.name for tool in get_tool_profile("combat")] == [
        "request_dice_roll",
        "modify_character_state",
        "delegate_combat_turn",
        "prepare_combat_end",
        "manage_scene_units",
        "next_turn",
        "inspect_unit",
        "consult_rules_handbook",
        "manage_space",
    ]


def test_toolnode_only_tools_are_runtime_backing_entries_without_profile_duplicates():
    """ToolNode 只额外保留仍被工作流或执行器使用的底层战斗工具。"""
    from app.services.tools import _COMBAT_TOOLS, _NARRATIVE_TOOLS, _RUNTIME_ONLY_TOOLS, get_tools

    profile_tool_names = {tool.name for tool in _NARRATIVE_TOOLS} | {tool.name for tool in _COMBAT_TOOLS}
    runtime_only_tool_names = {tool.name for tool in _RUNTIME_ONLY_TOOLS}
    assert not (runtime_only_tool_names & profile_tool_names)

    all_tool_names = {tool.name for tool in get_tools()}
    assert "use_class_feature" not in all_tool_names
    assert "spawn_ally" not in all_tool_names
    assert "spawn_monsters" not in all_tool_names
    assert "clear_dead_units" not in all_tool_names
    assert "load_skill" not in all_tool_names
    assert "apply_condition" not in all_tool_names
    assert "remove_condition" not in all_tool_names
    assert "grant_xp" not in all_tool_names
    assert "level_up" not in all_tool_names
    assert "choose_arcane_tradition" not in all_tool_names
    assert "choose_fighter_archetype" not in all_tool_names
    assert "switch_plane_map" not in all_tool_names
    assert "manage_adventure" not in all_tool_names
    assert "inspect_adventure_state" not in all_tool_names
    assert "load_adventure_node" not in all_tool_names
    assert "search_adventure_nodes" not in all_tool_names
    assert "switch_adventure_node" not in all_tool_names
    assert "reveal_adventure_clue" not in all_tool_names
    assert "mark_adventure_event" not in all_tool_names
    assert "advance_adventure" not in all_tool_names
    assert "create_plane_map" not in all_tool_names
    assert "place_unit" not in all_tool_names
    assert "move_unit" not in all_tool_names
    assert "remove_unit" not in all_tool_names
    assert "measure_distance" not in all_tool_names
    assert "query_units_in_radius" not in all_tool_names
    assert "attack_action" in all_tool_names
    assert "use_monster_action" in all_tool_names
    assert "start_combat" in all_tool_names
    assert "end_combat" in all_tool_names
    assert "prepare_combat_start" in all_tool_names
    assert "prepare_combat_end" in all_tool_names
    assert "claim_adventure_reward" in all_tool_names
    assert "buy_item" not in all_tool_names
    assert "use_item" not in all_tool_names
    assert "manage_inventory" in all_tool_names
    assert "use_class_action" in all_tool_names


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
    assert "target_id=该友方ID" in content
    assert "choose_feat" in content


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
    assert "场景切换到新的房间、道路、洞穴、营地、建筑或遭遇区域" in content


def test_adventure_module_skill_is_registered_for_on_demand_help():
    from app.services.skills import load_skill_content

    content = load_skill_content("adventure_module")

    assert "冒险模组主持技能" in content
    assert "claim_adventure_reward" in content
    assert "manage_adventure" not in content
    assert 'action="resolve"' not in content


def test_class_actions_skill_is_registered_for_real_dialogue_triggers():
    """职业动作技能说明覆盖玩家自然语言中的回气、动作如潮和战技选择。"""
    from app.services.skills import load_skill_content

    content = load_skill_content("class_actions")

    assert "职业动作技能" in content
    assert "我使用回气" in content
    assert "我使用动作如潮" in content
    assert "我选择摔绊攻击、精准攻击、恐吓攻击作为战技" in content
    assert 'action_id="choose_maneuvers"' in content


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


def test_combat_brief_separates_ally_from_enemy_side():
    combat = _combat_state("ally_wizard")
    combat["initiative_order"] = ["ally_wizard", "goblin_1", "player_hero"]
    combat["participants"]["ally_wizard"] = {
        "id": "ally_wizard",
        "name": "伊莲",
        "side": "ally",
        "hp": 12,
        "max_hp": 12,
        "ac": 12,
        "resources": {"spell_slot_lv1": 1},
        "resource_caps": {"spell_slot_lv1": 3},
        "known_spells": ["magic_missile", "shield"],
        "reaction_available": True,
        "attacks": [{"name": "Dagger"}],
    }
    state = {
        "phase": "combat",
        "combat": combat,
        "player": _player_state(),
    }

    brief = _build_combat_brief(state)
    directive = _build_combat_turn_directive(state)

    assert "友方侧: 伊莲" in brief
    assert "对立侧: Goblin" in brief
    assert "spell_slot_lv1=1/3" in brief
    assert "shield" in brief
    assert "当前是友方单位 伊莲" in directive
    assert "玩家指导或接管" in directive


def test_fallen_ally_turn_directive_requires_death_save():
    combat = _combat_state("ally_wizard")
    combat["initiative_order"] = ["ally_wizard", "goblin_1", "player_hero"]
    combat["participants"]["ally_wizard"] = {
        "id": "ally_wizard",
        "name": "伊莲",
        "side": "ally",
        "hp": 0,
        "max_hp": 12,
        "ac": 12,
        "death_save_successes": 1,
        "death_save_failures": 0,
        "is_stable": False,
        "is_dead": False,
    }
    state = {
        "phase": "combat",
        "combat": combat,
        "player": _player_state(),
    }

    directive = _build_combat_turn_directive(state)

    assert "必须进行死亡豁免" in directive
    assert "target_id=该友方ID" in directive


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
    assert "委托战斗执行器" in monster_directive
    assert "不要直接手算移动" in monster_directive
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
    assert start_kwargs["runtime_state_text"]
    assert any("继续战斗" in str(message.content) for message in start_kwargs["messages"])
    assert "<runtime_state" in str(start_kwargs["messages"][-1].content)
    assert result["messages"][0].content.startswith("[系统:运行状态帧]")
    assert result["messages"][1].content == "哥布林向你逼近。"


def test_narrative_system_prompt_excludes_combat_only_guidelines():
    prompt = _build_system_prompt({"messages": []}, NARRATIVE_AGENT_MODE)

    assert "探索代理补充准则" in prompt
    assert "战斗代理补充准则" not in prompt
    assert "战斗阶段保持简洁播报" not in prompt
    assert "工具返回是客观事实来源" in prompt
    assert "不要使用图标、emoji" in prompt
    assert "不要主动输出整块角色卡" in prompt
    assert "场景切换到新的房间、道路、洞穴、营地、建筑或遭遇区域" in prompt
    assert "先建立或切换地图" in prompt
    assert 'action="level_up"' not in prompt
    assert "character_state_management" not in prompt
    assert "后台导航器" in prompt
    assert "路径回溯" in prompt
    assert "不得自行发明当前节点未给出的地点、NPC、派系" in prompt
    assert "内部冒险校准或流程指令" in prompt
    assert "不要向玩家复述校准" in prompt
    assert "reveal_adventure_clue" not in prompt
    assert "search_adventure_nodes 能查到后期" not in prompt
    assert "manage_adventure" not in prompt


def test_combat_system_prompt_includes_combat_only_guidelines():
    prompt = _build_system_prompt({"messages": []}, COMBAT_AGENT_MODE)

    assert "战斗代理补充准则" in prompt
    assert "战斗阶段保持简洁播报" in prompt
    assert "不要在没有工具结果的情况下描述命中" in prompt
    assert "不要等待用户继续发话" in prompt
    assert "工具返回是客观事实来源" in prompt
    assert "不要主动输出整块角色卡" in prompt
    assert "开战前必须确认当前地图对应本次遭遇地点" in prompt


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
                tool_calls=[{"name": "delegate_combat_turn", "args": {"actor_id": "goblin_1", "instruction": "靠近并攻击玩家。"}, "id": "call_1"}],
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

    assert result["messages"][0].content.startswith("[系统:运行状态帧]")
    assert result["messages"][1].tool_calls[0]["name"] == "delegate_combat_turn"
    llm_call = fake_service.calls[0]
    assert llm_call["mode"] == COMBAT_AGENT_MODE
    assert [tool.name for tool in llm_call["tools"]] == [tool.name for tool in get_tool_profile("combat")]
    runtime_state = llm_call["messages"][-1].content
    assert "当前是怪物/NPC Goblin [ID:goblin_1] 的回合" in runtime_state
    assert "不要等待用户继续发话" in runtime_state
    assert "委托战斗执行器" in runtime_state
    assert "哥布林正试图拖走祭司" in runtime_state
    assert "start_combat" not in {tool.name for tool in llm_call["tools"]}
    assert "attack_action" not in {tool.name for tool in llm_call["tools"]}
    assert "manage_space" in {tool.name for tool in llm_call["tools"]}


def test_combat_assistant_uses_same_tools_for_player_turn():
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
            return AIMessage(content="你准备行动。")

    fake_service = _FakeLLMService()
    state = {
        "phase": "combat",
        "combat": _combat_state("player_hero"),
        "player": _player_state(),
        "messages": [HumanMessage(content="我攻击地精")],
    }

    with patch("app.graph.nodes._get_llm_service", return_value=fake_service):
        combat_assistant_node(state)

    assert [tool.name for tool in fake_service.calls[0]["tools"]] == [tool.name for tool in get_tool_profile("combat")]
    tool_names = {tool.name for tool in fake_service.calls[0]["tools"]}
    assert "delegate_combat_turn" in tool_names
    assert "attack_action" not in tool_names
    assert "use_monster_action" not in tool_names


def test_combat_assistant_uses_same_tools_for_ai_controlled_ally_turn():
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
                tool_calls=[{"name": "delegate_combat_turn", "args": {"actor_id": "ally_1", "instruction": "保护玩家。"}, "id": "call_ally"}],
            )

    fake_service = _FakeLLMService()
    combat = _combat_state("ally_1")
    combat["initiative_order"] = ["player_hero", "ally_1", "goblin_1"]
    combat["participants"]["ally_1"] = {
        "id": "ally_1",
        "name": "伊莲",
        "side": "ally",
        "hp": 8,
        "max_hp": 8,
        "ac": 12,
        "control_mode": "ai",
    }
    state = {
        "phase": "combat",
        "combat": combat,
        "player": _player_state(),
        "messages": [HumanMessage(content="继续")],
    }

    with patch("app.graph.nodes._get_llm_service", return_value=fake_service):
        combat_assistant_node(state)

    tool_names = {tool.name for tool in fake_service.calls[0]["tools"]}
    runtime_state = fake_service.calls[0]["messages"][-1].content
    assert [tool.name for tool in fake_service.calls[0]["tools"]] == [tool.name for tool in get_tool_profile("combat")]
    assert "attack_action" not in tool_names
    assert "use_monster_action" not in tool_names
    assert "AI 托管友方单位 伊莲" in runtime_state
    assert "委托战斗执行器" in runtime_state


def test_combat_executor_node_runs_delegated_tool_and_returns_trace():
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
            if len(self.calls) == 1:
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "attack_action",
                        "args": {"attacker_id": "goblin_1", "target_id": "player_hero"},
                        "id": "executor-call-1",
                    }],
                )
            assert isinstance(messages[-1], ToolMessage)
            return AIMessage(content="已攻击玩家。", tool_calls=[])

    fake_service = _FakeLLMService()
    combat = _combat_state("goblin_1")
    combat["participants"]["goblin_1"]["action_available"] = True
    combat["participants"]["goblin_1"]["attacks"] = [{
        "name": "Scimitar",
        "attack_bonus": 4,
        "damage_dice": "1d6+2",
        "damage_type": "slashing",
        "reach_feet": 5,
    }]
    state = {
        "phase": "combat",
        "combat": combat,
        "player": _player_state(),
        "space": {
            "active_map_id": "road",
            "maps": {"road": {"id": "road", "name": "小路", "width": 60, "height": 40, "grid_size": 5}},
            "placements": {
                "goblin_1": {"unit_id": "goblin_1", "map_id": "road", "position": {"x": 10, "y": 10}},
                "player_hero": {"unit_id": "player_hero", "map_id": "road", "position": {"x": 15, "y": 10}},
            },
        },
        "messages": [HumanMessage(content="继续")],
        "pending_combat_executor": {"actor_id": "goblin_1", "instruction": "用弯刀攻击玩家。"},
    }

    with patch("app.graph.nodes._get_llm_service", return_value=fake_service):
        result = combat_executor_node(state)

    executor_result = result["combat_executor_result"]
    assert executor_result["status"] == "completed"
    assert executor_result["turn_should_end"] is True
    assert executor_result["resource_state"]["action_available"] is False
    assert executor_result["used_resources"] == ["action"]
    assert executor_result["recommended_next"]["kind"] == "end_turn"
    assert executor_result["tool_trace"][0]["tool"] == "attack_action"
    assert result["pending_combat_executor"] is None
    assert result["combat"]["participants"]["goblin_1"]["action_available"] is False
    assert result["combat_events"][0]["attack_roll"]["raw_roll"] >= 1
    assert "hp_changes" in result["combat_events"][0]
    assert result["messages"][0].content.startswith("[系统:战斗执行器]")
    assert fake_service.calls[0]["mode"] == COMBAT_AGENT_MODE
    assert {tool.name for tool in fake_service.calls[0]["tools"]} == {
        "manage_space",
        "use_monster_action",
        "attack_action",
        "cast_spell",
        "use_class_action",
        "manage_inventory",
        "inspect_unit",
    }


def test_combat_executor_recommends_continue_when_primary_action_remains():
    class _FakeLLMService:
        def __init__(self):
            self.calls = []

        def invoke_with_tools(self, messages, tools, system_prompt, mode):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "manage_space",
                        "args": {
                            "action": "move_unit",
                            "payload": {"unit_id": "goblin_1", "x": 20, "y": 10},
                        },
                        "id": "executor-move",
                    }],
                )
            return AIMessage(content="已移动到攻击位置，仍可攻击。", tool_calls=[])

    combat = _combat_state("goblin_1")
    combat["participants"]["goblin_1"]["action_available"] = True
    combat["participants"]["goblin_1"]["movement_left"] = 30
    state = {
        "phase": "combat",
        "combat": combat,
        "player": _player_state(),
        "space": {
            "active_map_id": "road",
            "maps": {"road": {"id": "road", "name": "小路", "width": 60, "height": 40, "grid_size": 5}},
            "placements": {
                "goblin_1": {"unit_id": "goblin_1", "map_id": "road", "position": {"x": 10, "y": 10}},
                "player_hero": {"unit_id": "player_hero", "map_id": "road", "position": {"x": 25, "y": 10}},
            },
        },
        "messages": [HumanMessage(content="继续")],
        "pending_combat_executor": {"actor_id": "goblin_1", "instruction": "先靠近玩家。"},
    }

    with patch("app.graph.nodes._get_llm_service", return_value=_FakeLLMService()):
        result = combat_executor_node(state)

    executor_result = result["combat_executor_result"]
    assert executor_result["turn_should_end"] is False
    assert executor_result["needs_main_agent_decision"] is False
    assert executor_result["resource_state"]["action_available"] is True
    assert executor_result["used_resources"] == ["movement"]
    assert executor_result["recommended_next"]["kind"] == "continue_executor"
    assert "主要动作仍可用" in executor_result["remaining_meaningful_options"][0]


def test_assistant_invocation_filters_stale_runtime_frames_but_appends_current_one():
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
            return AIMessage(content="继续。", tool_calls=[])

    fake_service = _FakeLLMService()
    state = {
        "phase": "combat",
        "combat": _combat_state("player_hero"),
        "player": _player_state(),
        "messages": [
            HumanMessage(content="上一轮行动"),
            HumanMessage(content="[系统:运行状态帧]\n旧帧，不应再进模型输入。"),
            AIMessage(content="上一轮回应", tool_calls=[]),
            HumanMessage(content="继续"),
        ],
    }

    with patch("app.graph.nodes._get_llm_service", return_value=fake_service):
        result = combat_assistant_node(state)

    llm_messages = fake_service.calls[0]["messages"]
    stale_frames = [
        message
        for message in llm_messages[:-1]
        if isinstance(message, HumanMessage) and str(message.content).startswith("[系统:运行状态帧]")
    ]
    assert stale_frames == []
    assert str(llm_messages[-1].content).startswith("[系统:运行状态帧]")
    assert result["messages"][0].content.startswith("[系统:运行状态帧]")
    assert result["messages"][1].content == "继续。"


def test_combat_resolution_node_does_not_interrupt_on_player_down():
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

    result = combat_resolution_node(state)

    assert result == {}
