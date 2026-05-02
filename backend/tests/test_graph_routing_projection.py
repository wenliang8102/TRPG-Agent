from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.constants import ASSISTANT_NODE, COMBAT_AGENT_MODE, COMBAT_ASSISTANT_NODE, COMBAT_RESOLUTION_NODE, END_NODE, NARRATIVE_AGENT_MODE
from app.graph.edges import route_from_assistant, route_from_combat_resolution, route_from_reaction_resolution, route_from_router, route_from_tool
from app.graph.nodes import combat_assistant_node, combat_resolution_node
from app.memory.context_assembler import ContextAssembler, trim_model_messages
from app.prompts import get_assistant_system_prompt
from app.services.tools import get_tool_profile
from app.services.tool_service import load_skill


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
    assert projected_messages[-1].content.startswith("[工具:attack_action]")
    assert "实时系统监控窗" in projected_messages[-1].content
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

    assert len(projected_messages) == 3
    assert projected_messages[1].content.startswith("[系统:战斗归档]")
    assert "英雄在 2 回合内击败哥布林" in projected_messages[1].content
    assert "Goblin 使用 [Scimitar]" not in projected_messages[1].content
    assert projected_messages[-1].content.startswith("我检查哥布林尸体。")
    assert "实时系统监控窗" in projected_messages[-1].content


def test_tool_profiles_split_exploration_and_combat_visibility():
    narrative_tools = {tool.name for tool in get_tool_profile("narrative")}
    combat_tools = {tool.name for tool in get_tool_profile("combat")}

    assert "load_skill" in narrative_tools
    assert "load_skill" in combat_tools
    assert "start_combat" in narrative_tools
    assert "start_combat" not in combat_tools
    assert "attack_action" in combat_tools
    assert "attack_action" not in narrative_tools
    assert "grant_xp" not in narrative_tools
    assert "level_up" not in narrative_tools
    assert "choose_arcane_tradition" not in narrative_tools
    assert "apply_condition" not in combat_tools
    assert "remove_condition" not in combat_tools
    assert ASSISTANT_NODE == "assistant"


def test_load_skill_returns_character_state_management_instructions():
    result = load_skill.invoke({
        "name": "load_skill",
        "args": {"skill_id": "character_state_management"},
        "id": "skill-call",
        "type": "tool_call",
    })

    content = result.update["messages"][0].content
    assert "角色状态调整技能" in content
    assert "modify_character_state" in content
    assert 'action="level_up"' in content


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
    assert "合法目标" in monster_directive
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
    assert "继续战斗" in str(start_kwargs["messages"][0].content)


def test_narrative_system_prompt_excludes_combat_only_guidelines():
    prompt = _build_system_prompt({"messages": []}, NARRATIVE_AGENT_MODE)

    assert "探索代理补充准则" in prompt
    assert "战斗代理补充准则" not in prompt
    assert "禁止虚构战斗结果" not in prompt
    assert "回合意识" not in prompt
    assert "场景单位管理" in prompt
    assert "避免使用图标、emoji" in prompt
    assert "不要主动输出玩家角色卡面板" in prompt
    assert 'action="level_up"' not in prompt
    assert "character_state_management" in prompt


def test_combat_system_prompt_includes_combat_only_guidelines():
    prompt = _build_system_prompt({"messages": []}, COMBAT_AGENT_MODE)

    assert "战斗代理补充准则" in prompt
    assert "禁止虚构战斗结果" in prompt
    assert "回合意识" in prompt
    assert "怪物回合结算" in prompt
    assert "不要主动输出玩家角色卡面板" in prompt


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
