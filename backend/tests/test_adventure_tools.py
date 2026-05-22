"""冒险节点工具测试 — 锁定 PDF 节点式推进的最小闭环。"""

from pathlib import Path
import json
import sys

from langgraph.types import Command

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.tools.adventure_tools import (  # noqa: E402
    claim_adventure_reward,
    _advance_adventure_impl,
    _inspect_adventure_state_impl,
    _load_adventure_node_impl,
    _mark_adventure_event_impl,
    _resolve_adventure_node_impl,
    _reveal_adventure_clue_impl,
    _search_adventure_nodes_impl,
    _switch_adventure_node_impl,
)


def _invoke_tool(tool_func, *, tool_input: dict) -> object:
    """用 ToolCall 格式调用 LangChain 工具，保持和现有测试一致。"""
    return tool_func.invoke({
        "name": tool_func.name,
        "args": tool_input,
        "id": "adventure-test-call",
        "type": "tool_call",
    })


def _payload(result: Command) -> dict:
    return json.loads(result.update["messages"][0].content)


def test_load_default_adventure_hook_node():
    result = _load_adventure_node_impl(None, {}, "adventure-test-call")

    assert isinstance(result, Command)
    payload = _payload(result)
    assert payload["node"]["id"] == "adventure_hook_meet_me_in_phandalin"
    assert payload["node"]["source_pages"] == [6, 6]
    assert payload["progression_rule"].startswith("剧情推进出口只看顶层 available_exits")
    assert payload["available_exits"][0]["id"] == "begin_escort_journey"
    assert "candidate_exits" not in payload["node"]


def test_inspect_adventure_state_impl_can_load_skill_instructions():
    result = _inspect_adventure_state_impl(True, {}, "adventure-test-call")

    content = result.update["messages"][0].content
    assert "冒险模组主持技能" in content
    assert "claim_adventure_reward" in content
    assert "manage_adventure" not in content


def test_claim_reward_tool_schema_does_not_leak_concrete_reward_ids():
    schema_text = json.dumps(claim_adventure_reward.args_schema.model_json_schema(), ensure_ascii=False)

    assert "goblin_ambush_hideout_75_xp" not in claim_adventure_reward.description
    assert "goblin_ambush_hideout_75_xp" not in schema_text


def test_advance_impl_settles_single_exit_local_requirements():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "adventure_hook_meet_me_in_phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
        }
    }

    advanced = _advance_adventure_impl("begin_escort_journey", state, "adventure-test-call")

    adventure = advanced.update["adventure"]
    assert adventure["active_node_id"] == "goblin_ambush"
    assert adventure["completed_node_ids"] == ["adventure_hook_meet_me_in_phandalin"]
    assert "depart_neverwinter_for_phandalin" in adventure["completed_event_ids"]
    assert {"delivery_job", "phandalin_destination"}.issubset(set(adventure["known_clue_ids"]))


def test_advance_requires_completed_event_when_exit_has_condition():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["goblin_ambush"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "pending_exit_option_ids": [],
        }
    }

    blocked = _advance_adventure_impl("investigate_goblin_trail", state, "adventure-test-call")
    assert "出口条件未满足" in _payload(blocked)["error"]

    marked = _mark_adventure_event_impl("goblin_ambush_resolved", state, "adventure-test-call")
    state["adventure"] = marked.update["adventure"]

    still_blocked = _advance_adventure_impl("investigate_goblin_trail", state, "adventure-test-call")
    assert "出口条件未满足" in _payload(still_blocked)["error"]

    revealed = _reveal_adventure_clue_impl("goblin_trail", state, "adventure-test-call")
    state["adventure"] = revealed.update["adventure"]

    advanced = _advance_adventure_impl("investigate_goblin_trail", state, "adventure-test-call")
    assert advanced.update["adventure"]["active_node_id"] == "goblin_trail_to_cragmaw_hideout"
    assert "goblin_ambush" in advanced.update["adventure"]["completed_node_ids"]
    assert advanced.update["adventure"]["breadcrumb_node_ids"][-1] == "goblin_trail_to_cragmaw_hideout"
    assert advanced.update["adventure"]["deferred_node_ids"] == []


def test_adventure_impl_can_resolve_clue_and_advance():
    state = {
        "player": {"name": "英雄", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["goblin_ambush"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "pending_exit_option_ids": [],
        }
    }

    resolved = _resolve_adventure_node_impl(
        "",
        ["goblin_trail"],
        ["goblin_ambush_resolved"],
        state,
        "adventure-test-call",
    )
    state["adventure"] = resolved.update["adventure"]

    advanced = _advance_adventure_impl("investigate_goblin_trail", state, "adventure-test-call")

    assert advanced.update["adventure"]["active_node_id"] == "goblin_trail_to_cragmaw_hideout"

    state["adventure"] = advanced.update["adventure"]

    trail_resolved = _resolve_adventure_node_impl("", None, None, state, "adventure-test-call")
    state["adventure"] = trail_resolved.update["adventure"]

    hideout = _advance_adventure_impl("follow_trail_to_hideout", state, "adventure-test-call")

    assert hideout.update["adventure"]["active_node_id"] == "cragmaw_hideout_entrance"
    assert hideout.update["adventure"]["claimed_reward_ids"] == []
    assert hideout.update["adventure"]["pending_reward_grants"][0]["id"] == "goblin_ambush_hideout_75_xp"
    assert "reach_cragmaw_hideout" in hideout.update["adventure"]["completed_event_ids"]
    assert "player" not in hideout.update

    claimed = _invoke_tool(
        claim_adventure_reward,
        tool_input={
            "reward_id": "goblin_ambush_hideout_75_xp",
            "state": {"player": state["player"], "adventure": hideout.update["adventure"]},
        },
    )
    assert claimed.update["player"]["xp"] == 75
    assert claimed.update["adventure"]["claimed_reward_ids"] == ["goblin_ambush_hideout_75_xp"]
    assert claimed.update["adventure"]["pending_reward_grants"] == []
    claim_payload = _payload(claimed)
    assert "adventure_state" not in claim_payload
    assert "reward" not in claim_payload["result"]
    assert claim_payload["result"]["reward_id"] == "goblin_ambush_hideout_75_xp"
    assert claim_payload["result"]["amount"] == 75
    assert claim_payload["result"]["current_xp"] == 75
    assert "打败伏击地精" in claim_payload["result"]["description"]
    assert "打败伏击地精" in claim_payload["message"]

    repeated = _invoke_tool(
        claim_adventure_reward,
        tool_input={
            "reward_id": "goblin_ambush_hideout_75_xp",
            "state": {"player": claimed.update["player"], "adventure": claimed.update["adventure"]},
        },
    )
    assert "player" not in repeated.update
    assert "待领取奖励不存在" in _payload(repeated)["result"]["error"]
    assert "adventure_state" not in _payload(repeated)


def test_claim_adventure_reward_records_treasure_without_touching_player_xp():
    state = {
        "player": {"name": "温良", "xp": 75, "inventory": []},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "cragmaw_hideout_klarg_cave",
            "unlocked_node_ids": ["cragmaw_hideout_klarg_cave"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": ["cragmaw_hideout_milestone_complete"],
            "claimed_reward_ids": [],
            "pending_reward_grants": [
                {
                    "id": "klarg_treasure_cache",
                    "node_id": "cragmaw_hideout_klarg_cave",
                    "type": "treasure",
                    "amount": 1,
                    "scope": "party",
                    "description": "克拉格的小金库：600 cp、110 sp、两瓶治疗药水和翡翠青蛙。",
                    "requires": ["cragmaw_hideout_milestone_complete"],
                }
            ],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["cragmaw_hideout_klarg_cave"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    claimed = _invoke_tool(
        claim_adventure_reward,
        tool_input={"reward_id": "klarg_treasure_cache", "state": state},
    )

    assert claimed.update["player"]["xp"] == 75
    assert claimed.update["player"]["inventory"][0]["id"] == "klarg_treasure_cache"
    assert claimed.update["player"]["inventory"][0]["type"] == "treasure"
    assert claimed.update["player"]["inventory"][0]["quantity"] == 1
    assert "克拉格的小金库" in claimed.update["player"]["inventory"][0]["description"]
    assert claimed.update["adventure"]["claimed_reward_ids"] == ["klarg_treasure_cache"]
    assert claimed.update["adventure"]["pending_reward_grants"] == []
    payload = _payload(claimed)
    assert payload["result"]["type"] == "treasure"
    assert payload["result"]["amount"] == 1
    assert payload["result"]["inventory_item_id"] == "klarg_treasure_cache"
    assert "克拉格的小金库" in payload["message"]


def test_claim_adventure_reward_adds_gold_to_player_coins():
    state = {
        "player": {"name": "温良", "xp": 75, "coins": {"gp": 2}},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "barthen_provisions",
            "unlocked_node_ids": ["barthen_provisions"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": ["phandalin_supplies_delivered"],
            "claimed_reward_ids": [],
            "pending_reward_grants": [
                {
                    "id": "phandalin_delivery_10gp",
                    "node_id": "barthen_provisions",
                    "type": "gold",
                    "amount": 10,
                    "scope": "per_player",
                    "currency": "gp",
                    "description": "把开局护送的补给货车交到巴森补给后，每名玩家获得10 gp报酬。",
                    "requires": ["phandalin_supplies_delivered"],
                }
            ],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["barthen_provisions"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    claimed = _invoke_tool(
        claim_adventure_reward,
        tool_input={"reward_id": "phandalin_delivery_10gp", "state": state},
    )

    assert claimed.update["player"]["coins"]["gp"] == 12
    assert claimed.update["player"]["xp"] == 75
    assert claimed.update["adventure"]["claimed_reward_ids"] == ["phandalin_delivery_10gp"]
    payload = _payload(claimed)
    assert payload["result"]["current_amount"] == 12
    assert "+10 GP" in payload["message"]


def test_resolve_impl_returns_available_exits_after_scene_result():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["goblin_ambush"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "pending_exit_option_ids": [],
        }
    }

    resolved = _resolve_adventure_node_impl(
        "地精伏击已结束，玩家发现了通向窝点的踪迹。",
        ["goblin_trail"],
        ["goblin_ambush_resolved"],
        state,
        "adventure-test-call",
    )

    payload = _payload(resolved)
    assert "goblin_trail" in resolved.update["adventure"]["known_clue_ids"]
    assert "goblin_ambush_resolved" in resolved.update["adventure"]["completed_event_ids"]
    assert payload["recommended_action"]["tool"] == "ask_player_or_advance_declared_choice"
    assert payload["recommended_action"]["available_option_ids"] == [
        "investigate_goblin_trail",
        "go_to_phandalin_first",
        "return_to_ambush_site",
        "captive_leads_to_trail",
    ]


def test_resolve_impl_rejects_events_outside_current_node():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "adventure_hook_meet_me_in_phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "pending_exit_option_ids": [],
        }
    }

    resolved = _resolve_adventure_node_impl("", None, ["goblin_ambush_resolved"], state, "adventure-test-call")

    assert resolved.update["adventure"]["completed_event_ids"] == []


def test_mark_event_impl_rejects_events_outside_current_node():
    result = _mark_adventure_event_impl(
        "goblin_ambush_resolved",
        {
            "adventure": {
                "module_id": "lost_mine",
                "active_node_id": "adventure_hook_meet_me_in_phandalin",
                "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin"],
                "completed_node_ids": [],
                "known_clue_ids": [],
                "completed_event_ids": [],
                "pending_exit_option_ids": [],
            }
        },
        "adventure-test-call",
    )

    payload = _payload(result)
    assert "事件不属于当前节点" in payload["error"]
    assert "adventure" not in result.update


def test_advance_with_event_id_returns_exit_hint():
    result = _advance_adventure_impl("goblin_ambush_resolved", {}, "adventure-test-call")

    payload = _payload(result)
    assert "当前节点没有出口" in payload["error"]
    assert "available_exits.id" in payload["hint"]
    assert payload["available_exit_ids"] == ["begin_escort_journey"]


def test_search_adventure_nodes_finds_module_material():
    result = _search_adventure_nodes_impl("地精伏击", 5, {}, "adventure-test-call")

    payload = _payload(result)
    result_ids = [item["id"] for item in payload["results"]]
    assert "goblin_ambush" in result_ids


def test_load_ambush_node_includes_pdf_guidance():
    result = _load_adventure_node_impl("goblin_ambush", {}, "adventure-test-call")

    payload = _payload(result)
    node = payload["node"]
    assert node["scene_beats"]
    assert node["title"] == "地精伏击"
    assert any("新版突袭" in note for note in node["rules_notes"])
    assert any(clue["id"] == "goblin_trail" for clue in node["clues"])
    assert node["fallbacks"]


def test_load_ambush_node_overrides_legacy_surprise_rule():
    result = _load_adventure_node_impl("goblin_ambush", {}, "adventure-test-call")

    payload = _payload(result)
    node = payload["node"]
    visible_text = json.dumps(node, ensure_ascii=False)
    assert "第一轮无法执行任何动作" not in visible_text
    assert "新版突袭" in visible_text
    assert "先攻检定上获得劣势" in visible_text
    assert "不跳过首回合" in visible_text


def test_search_adventure_nodes_uses_dm_guidance_and_subsections():
    rest_result = _search_adventure_nodes_impl("休息 75 XP", 5, {}, "adventure-test-call")
    reward_result = _search_adventure_nodes_impl("75 XP 奖励经验值", 5, {}, "adventure-test-call")

    rest_ids = [item["id"] for item in _payload(rest_result)["results"]]
    reward_ids = [item["id"] for item in _payload(reward_result)["results"]]
    assert "cragmaw_hideout_entrance" in rest_ids
    assert "cragmaw_hideout_entrance" in reward_ids
    assert any(item in rest_ids for item in ["cragmaw_hideout_klarg_cave", "cragmaw_hideout_treasure_and_milestone"])


def test_switch_adventure_node_updates_bookmark_without_exit_requirement():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "adventure_hook_meet_me_in_phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "pending_exit_option_ids": [],
        }
    }

    result = _switch_adventure_node_impl(
        "phandalin",
        "玩家决定暂时不追踪地精，继续护送补给。",
        state,
        "adventure-test-call",
    )

    assert result.update["adventure"]["active_node_id"] == "phandalin"
    assert result.update["adventure"]["completed_node_ids"] == []
    assert result.update["adventure"]["deferred_node_ids"] == ["adventure_hook_meet_me_in_phandalin"]
    assert result.update["adventure"]["breadcrumb_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "phandalin"]
    payload = _payload(result)
    assert payload["result"]["to"] == "phandalin"
