"""冒险运行时裁定测试 — LLM 只做语义判断，状态写入必须受节点约束。"""

import json

from app.adventures.runtime import (
    AdventurePreTurnDecision,
    AdventureProgressDecision,
    LLMAdventureDirector,
    adjudicate_and_apply_adventure_progress,
    adjudicate_and_apply_pre_turn_adventure_progress,
    build_adventure_node_frame_message,
)
from app.adventures.navigation import returnable_node_ids
from app.adventures.store import get_adventure_store
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


class FakeDirector:
    def __init__(self, decision: AdventureProgressDecision) -> None:
        self.decision = decision

    def adjudicate(self, *, state, recent_messages, session_id=None):
        return self.decision

    def adjudicate_pre_turn(self, *, state, player_message, session_id=None):
        return AdventurePreTurnDecision(
            completed_event_ids=self.decision.completed_event_ids,
            discovered_clue_ids=self.decision.discovered_clue_ids,
            exit_option_id=self.decision.exit_option_id,
            target_node_id=self.decision.target_node_id,
            transition_kind=self.decision.transition_kind,
            needs_player_choice=self.decision.needs_player_choice,
            confidence=self.decision.confidence,
            reason=self.decision.reason,
        )


class FakeSummaryLLMService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def invoke_summary(self, summary_input: str, *, system_prompt: str) -> str:
        self.calls.append(
            {
                "summary_input": summary_input,
                "system_prompt": system_prompt,
            }
        )
        return "{}"


def _ambush_state() -> dict:
    return {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "deferred_node_ids": [],
            "transition_log": [],
        }
    }


def test_runtime_accepts_director_decision_with_current_node_ids_only():
    state = _ambush_state()
    state["player"] = {"name": "温良", "xp": 0}

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                completed_event_ids=["goblin_ambush_resolved", "fake_event"],
                discovered_clue_ids=["goblin_trail", "fake_clue"],
                exit_option_id="investigate_goblin_trail",
                confidence=0.9,
            )
        ),
    )

    assert update.adventure is not None
    assert update.adventure["completed_event_ids"] == ["goblin_ambush_resolved"]
    assert update.adventure["known_clue_ids"] == ["goblin_trail"]
    assert update.applied == "advanced"
    assert update.adventure["pending_exit_option_ids"] == []
    assert update.adventure["active_node_id"] == "goblin_trail_to_cragmaw_hideout"
    assert update.adventure["breadcrumb_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "goblin_trail_to_cragmaw_hideout"]
    assert update.adventure["deferred_node_ids"] == []
    assert update.adventure["claimed_reward_ids"] == []
    assert "player" not in update.state_update


def test_runtime_does_not_double_award_claimed_node_reward_but_reconciles_arrival_event():
    state = {
        "player": {"name": "温良", "xp": 75},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "cragmaw_hideout_entrance",
            "unlocked_node_ids": [
                "adventure_hook_meet_me_in_phandalin",
                "goblin_ambush",
                "cragmaw_hideout_entrance",
            ],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "known_clue_ids": ["goblin_trail"],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "claimed_reward_ids": ["goblin_ambush_hideout_75_xp"],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "cragmaw_hideout_entrance"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                confidence=0.0,
            )
        ),
    )

    assert update.adventure is not None
    assert update.adventure["claimed_reward_ids"] == ["goblin_ambush_hideout_75_xp"]
    assert update.adventure["completed_event_ids"] == ["goblin_ambush_resolved", "reach_cragmaw_hideout"]
    assert "player" not in update.state_update
    assert update.player_notifications == []


def test_runtime_marks_node_reward_pending_instead_of_granting_xp():
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "completed_node_ids": [],
            "known_clue_ids": ["goblin_trail"],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                target_node_id="cragmaw_hideout_entrance",
                transition_kind="switch",
                confidence=0.9,
            )
        ),
    )

    assert "player" not in update.state_update
    assert "adventure_reward_notice" not in update.state_update
    assert update.player_notifications == []
    assert update.adventure["claimed_reward_ids"] == []
    assert update.adventure["pending_reward_grants"][0]["id"] == "goblin_ambush_hideout_75_xp"
    assert update.adventure["pending_reward_grants"][0]["amount"] == 75


def test_runtime_can_mark_treasure_reward_pending_from_current_node():
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "cragmaw_hideout_klarg_cave",
            "unlocked_node_ids": ["cragmaw_hideout_klarg_cave"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": ["cragmaw_hideout_milestone_complete"],
            "claimed_reward_ids": ["cragmaw_hideout_milestone_275_xp"],
            "pending_reward_grants": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["cragmaw_hideout_klarg_cave"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }
    store = get_adventure_store()
    node = store.get_node("cragmaw_hideout_klarg_cave")
    original_rewards = node.rewards
    node.rewards = [
        {
            "id": "klarg_treasure_cache",
            "type": "treasure",
            "amount": 1,
            "scope": "party",
            "requires": ["cragmaw_hideout_milestone_complete"],
            "description": "克拉格的小金库。",
        }
    ]
    try:
        update = adjudicate_and_apply_adventure_progress(
            state,
            recent_messages=[],
            director=FakeDirector(AdventureProgressDecision(confidence=0.0)),
        )
    finally:
        node.rewards = original_rewards

    assert update.adventure["pending_reward_grants"][0]["id"] == "klarg_treasure_cache"
    assert update.adventure["pending_reward_grants"][0]["type"] == "treasure"
    assert "player" not in update.state_update


def test_pre_turn_settles_current_node_facts_before_switching_and_syncing_rewards():
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": [],
            "completed_event_ids": ["enter_goblin_ambush"],
            "claimed_reward_ids": [],
            "pending_reward_grants": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    update = adjudicate_and_apply_pre_turn_adventure_progress(
        state,
        player_message="我们已经解决伏击地精，并进入窝点。",
        director=FakeDirector(
            AdventureProgressDecision(
                completed_event_ids=["goblin_ambush_resolved", "fake_event"],
                discovered_clue_ids=["goblin_trail", "fake_clue"],
                target_node_id="cragmaw_hideout_entrance",
                transition_kind="switch",
                confidence=0.95,
                reason="玩家确认伏击已解决并抵达窝点入口。",
            )
        ),
    )

    assert update.applied == "pre_turn_switched"
    assert update.adventure["active_node_id"] == "cragmaw_hideout_entrance"
    assert "goblin_ambush_resolved" in update.adventure["completed_event_ids"]
    assert "fake_event" not in update.adventure["completed_event_ids"]
    assert update.adventure["known_clue_ids"] == ["goblin_trail"]
    assert "reach_cragmaw_hideout" in update.adventure["completed_event_ids"]
    assert update.adventure["pending_reward_grants"][0]["id"] == "goblin_ambush_hideout_75_xp"
    assert update.adventure["claimed_reward_ids"] == []
    assert "player" not in update.state_update


def test_runtime_settles_selected_exit_local_requirements_when_advancing():
    update = adjudicate_and_apply_adventure_progress(
        {**_ambush_state(), "player": {"name": "温良", "xp": 0}},
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                exit_option_id="investigate_goblin_trail",
                confidence=0.9,
            )
        ),
    )

    assert update.adventure is not None
    assert update.applied == "advanced"
    assert update.adventure["active_node_id"] == "goblin_trail_to_cragmaw_hideout"
    assert update.adventure["completed_event_ids"] == ["goblin_ambush_resolved"]
    assert update.adventure["known_clue_ids"] == ["goblin_trail"]
    assert update.adventure["pending_exit_option_ids"] == []


def test_runtime_settles_selected_exit_local_clue_when_advancing():
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": ["delivery_job", "phandalin_destination"],
            "completed_event_ids": ["depart_neverwinter_for_phandalin", "enter_goblin_ambush", "goblin_ambush_resolved"],
            "claimed_reward_ids": [],
            "pending_reward_grants": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    update = adjudicate_and_apply_pre_turn_adventure_progress(
        state,
        player_message="追寻踪迹",
        director=FakeDirector(
            AdventureProgressDecision(
                exit_option_id="investigate_goblin_trail",
                target_node_id="goblin_trail_to_cragmaw_hideout",
                transition_kind="advance",
                confidence=0.95,
                reason="玩家明确追踪地精踪迹。",
            )
        ),
    )

    assert update.applied == "pre_turn_advanced"
    assert update.adventure["active_node_id"] == "goblin_trail_to_cragmaw_hideout"
    assert "goblin_trail" in update.adventure["known_clue_ids"]
    assert update.adventure["pending_reward_grants"] == []


def test_runtime_does_not_block_director_exit_on_cross_node_requirements():
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_trail_to_cragmaw_hideout",
            "unlocked_node_ids": ["goblin_ambush", "goblin_trail_to_cragmaw_hideout"],
            "completed_node_ids": [],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "claimed_reward_ids": [],
            "pending_reward_grants": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["goblin_ambush", "goblin_trail_to_cragmaw_hideout"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[HumanMessage(content="俘虏地精带我们去窝点。")],
        director=FakeDirector(
            AdventureProgressDecision(
                exit_option_id="guided_to_hideout",
                transition_kind="advance",
                confidence=0.95,
                reason="玩家明确让俘虏带路前往窝点。",
            )
        ),
    )

    assert update.applied == "advanced"
    assert update.adventure["active_node_id"] == "cragmaw_hideout_entrance"
    assert "goblin_ambush_resolved" not in update.adventure["completed_event_ids"]


def test_runtime_commits_director_target_even_when_player_choice_remains_inside_target():
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "known_clue_ids": [],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin", "goblin_ambush"],
            "deferred_node_ids": ["phandalin"],
            "transition_log": [],
        },
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[HumanMessage(content="我到了克拉摩窝点洞口，但还没决定怎么进去。")],
        director=FakeDirector(
            AdventureProgressDecision(
                target_node_id="cragmaw_hideout_entrance",
                transition_kind="switch",
                needs_player_choice=True,
                confidence=0.85,
                reason="玩家已经到达窝点入口，但仍需选择洞口战术。",
            )
        ),
    )

    assert update.applied == "switched"
    assert update.adventure["active_node_id"] == "cragmaw_hideout_entrance"
    assert "reach_cragmaw_hideout" in update.adventure["completed_event_ids"]
    assert update.adventure["claimed_reward_ids"] == []
    assert update.adventure["pending_reward_grants"][0]["id"] == "goblin_ambush_hideout_75_xp"
    assert "player" not in update.state_update


def test_runtime_advances_when_director_confidently_selects_ready_exit():
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

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                exit_option_id="continue_to_ambush",
                confidence=0.9,
                needs_player_choice=False,
            )
        ),
    )

    assert update.applied == "advanced"
    assert update.adventure["active_node_id"] == "goblin_ambush"
    assert update.adventure["completed_node_ids"] == ["adventure_hook_meet_me_in_phandalin"]
    assert update.adventure["unlocked_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"]
    assert update.adventure["breadcrumb_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"]
    assert update.state_update["adventure_runtime_directive"]["node_id"] == "goblin_ambush"


def test_pre_turn_can_depart_from_opening_node_by_settling_single_exit_requirements():
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
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "deferred_node_ids": [],
            "transition_log": [],
        }
    }

    update = adjudicate_and_apply_pre_turn_adventure_progress(
        state,
        player_message="继续吧",
        director=FakeDirector(
            AdventureProgressDecision(
                exit_option_id="begin_escort_journey",
                confidence=0.95,
                reason="玩家明确同意出发前往凡达林。",
            )
        ),
    )

    assert update.applied == "pre_turn_advanced"
    assert update.adventure["active_node_id"] == "goblin_ambush"
    assert update.adventure["completed_node_ids"] == ["adventure_hook_meet_me_in_phandalin"]
    assert "depart_neverwinter_for_phandalin" in update.adventure["completed_event_ids"]
    assert {"delivery_job", "phandalin_destination"}.issubset(set(update.adventure["known_clue_ids"]))
    assert update.adventure["claimed_reward_ids"] == []


def test_pre_turn_runtime_advances_before_main_reply_when_player_selects_exit():
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

    update = adjudicate_and_apply_pre_turn_adventure_progress(
        state,
        player_message="一起出发，沿三猪小径继续前进",
        director=FakeDirector(
            AdventureProgressDecision(
                exit_option_id="continue_to_ambush",
                confidence=0.92,
                needs_player_choice=False,
            )
        ),
    )

    assert update.applied == "pre_turn_advanced"
    assert update.adventure["active_node_id"] == "goblin_ambush"
    assert update.adventure["completed_node_ids"] == ["adventure_hook_meet_me_in_phandalin"]
    assert update.adventure["breadcrumb_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"]
    assert update.state_update["adventure_runtime_directive"]["node_id"] == "goblin_ambush"


def test_pre_turn_runtime_does_not_advance_uncertain_choice():
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

    update = adjudicate_and_apply_pre_turn_adventure_progress(
        state,
        player_message="我再观察一下",
        director=FakeDirector(
            AdventureProgressDecision(
                exit_option_id="continue_to_ambush",
                confidence=0.55,
                needs_player_choice=True,
            )
        ),
    )

    assert update.applied == "pre_turn_advanced"
    assert update.adventure["active_node_id"] == "goblin_ambush"
    assert update.adventure["pending_exit_option_ids"] == []
    assert update.adventure["breadcrumb_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"]


def test_pre_turn_runtime_does_not_commit_generic_continue_on_multi_exit_node():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": ["delivery_job", "gundren_went_ahead", "phandalin_destination"],
            "completed_event_ids": ["enter_goblin_ambush"],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "deferred_node_ids": [],
            "transition_log": [],
        }
    }

    update = adjudicate_and_apply_pre_turn_adventure_progress(
        state,
        player_message="继续前进",
        director=FakeDirector(
            AdventureProgressDecision(
                exit_option_id="go_to_phandalin_first",
                target_node_id="phandalin",
                transition_kind="advance",
                confidence=0.95,
                reason="误把泛化继续匹配为去凡达林。",
            )
        ),
    )

    assert update.applied == ""
    assert update.adventure is None
    assert update.state_update == {}


def test_pre_turn_runtime_advances_clear_low_confidence_single_exit():
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

    update = adjudicate_and_apply_pre_turn_adventure_progress(
        state,
        player_message="追",
        director=FakeDirector(
            AdventureProgressDecision(
                exit_option_id="continue_to_ambush",
                confidence=0.7,
                needs_player_choice=False,
            )
        ),
    )

    assert update.applied == "pre_turn_advanced"
    assert update.adventure["active_node_id"] == "goblin_ambush"
    assert update.adventure["breadcrumb_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"]


def test_pre_turn_runtime_switches_to_semantic_candidate_node():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "known_clue_ids": ["redbrand_threat", "sildar_cragmaw_castle", "side_quests_unlocked"],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "deferred_node_ids": ["goblin_ambush"],
            "transition_log": [],
        }
    }

    update = adjudicate_and_apply_pre_turn_adventure_progress(
        state,
        player_message="我们直接去克拉摩窝点。",
        director=FakeDirector(
            AdventureProgressDecision(
                target_node_id="goblin_ambush",
                transition_kind="switch",
                confidence=0.9,
                needs_player_choice=False,
            )
        ),
    )

    assert update.applied == "pre_turn_switched"
    assert update.adventure["active_node_id"] == "goblin_ambush"
    assert update.adventure["deferred_node_ids"] == ["phandalin"]
    assert update.adventure["breadcrumb_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin", "goblin_ambush"]
    assert update.state_update["adventure_runtime_directive"]["node_id"] == "goblin_ambush"


def test_runtime_switches_to_semantic_candidate_node_after_reply():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "known_clue_ids": ["redbrand_threat", "sildar_cragmaw_castle", "side_quests_unlocked"],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "deferred_node_ids": ["goblin_ambush"],
            "transition_log": [],
        }
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[HumanMessage(content="我们去克拉摩窝点。")],
        director=FakeDirector(
            AdventureProgressDecision(
                target_node_id="goblin_ambush",
                transition_kind="switch",
                confidence=0.9,
                needs_player_choice=False,
            )
        ),
    )

    assert update.applied == "switched"
    assert update.adventure["active_node_id"] == "goblin_ambush"
    assert update.adventure["deferred_node_ids"] == ["phandalin"]
    assert update.adventure["breadcrumb_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin", "goblin_ambush"]
    assert update.state_update["adventure_runtime_directive"]["node_id"] == "goblin_ambush"


def test_runtime_switches_to_director_room_without_module_specific_entrance_rewrite():
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": ["goblin_trail"],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "deferred_node_ids": ["goblin_ambush"],
            "transition_log": [],
        },
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[HumanMessage(content="修达带我进入洞口，前方有地精交谈。")],
        director=FakeDirector(
            AdventureProgressDecision(
                target_node_id="cragmaw_hideout__6_goblin_den",
                transition_kind="switch",
                confidence=0.9,
                needs_player_choice=False,
            )
        ),
    )

    assert update.applied == "switched"
    assert update.adventure["active_node_id"] == "cragmaw_hideout_goblin_den"
    assert "reach_cragmaw_hideout" not in update.adventure["completed_event_ids"]
    assert update.adventure["claimed_reward_ids"] == []
    assert update.adventure["pending_reward_grants"] == []
    assert "player" not in update.state_update
    assert update.state_update["adventure_runtime_directive"]["node_id"] == "cragmaw_hideout_goblin_den"


def test_pre_turn_advancing_from_trail_settles_selected_exit_local_event_and_syncs_reward():
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_trail_to_cragmaw_hideout",
            "unlocked_node_ids": [
                "adventure_hook_meet_me_in_phandalin",
                "goblin_ambush",
                "goblin_trail_to_cragmaw_hideout",
            ],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "known_clue_ids": ["delivery_job", "phandalin_destination", "goblin_trail"],
            "completed_event_ids": [
                "depart_neverwinter_for_phandalin",
                "enter_goblin_ambush",
                "goblin_ambush_resolved",
                "enter_goblin_trail_to_cragmaw_hideout",
                "snare_trap_resolved",
            ],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": [
                "adventure_hook_meet_me_in_phandalin",
                "goblin_ambush",
                "goblin_trail_to_cragmaw_hideout",
            ],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    update = adjudicate_and_apply_pre_turn_adventure_progress(
        state,
        player_message="我们进去",
        director=FakeDirector(
            AdventureProgressDecision(
                exit_option_id="follow_trail_to_hideout",
                target_node_id="cragmaw_hideout_entrance",
                transition_kind="advance",
                confidence=0.95,
                reason="玩家明确进入克拉摩窝点。",
            )
        ),
    )

    assert update.applied == "pre_turn_advanced"
    assert update.adventure["active_node_id"] == "cragmaw_hideout_entrance"
    assert "reach_cragmaw_hideout" in update.adventure["completed_event_ids"]
    assert update.adventure["pending_reward_grants"][0]["id"] == "goblin_ambush_hideout_75_xp"


def test_runtime_uses_node_routing_instead_of_module_specific_switch_rules():
    state = {
        "player": {"name": "温良", "xp": 0},
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": ["goblin_trail"],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "deferred_node_ids": ["goblin_ambush"],
            "transition_log": [],
        },
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[HumanMessage(content="我们进入克拉摩窝点的地精休息室。")],
        director=FakeDirector(
            AdventureProgressDecision(
                target_node_id="cragmaw_hideout__6_goblin_den",
                transition_kind="switch",
                confidence=0.9,
                needs_player_choice=False,
            )
        ),
    )

    assert update.adventure["active_node_id"] == "cragmaw_hideout_goblin_den"
    assert "reach_cragmaw_hideout" not in update.adventure["completed_event_ids"]
    assert update.adventure["pending_reward_grants"] == []
    assert "player" not in update.state_update


def test_runtime_revisits_deferred_node_when_director_requests_backtrack():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": ["goblin_trail"],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "deferred_node_ids": ["goblin_ambush"],
            "transition_log": [],
        }
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[HumanMessage(content="我们回去看看遇袭地点。")],
        director=FakeDirector(
            AdventureProgressDecision(
                target_node_id="goblin_ambush",
                transition_kind="revisit",
                confidence=0.9,
                needs_player_choice=False,
            )
        ),
    )

    assert update.applied == "revisited"
    assert update.adventure["active_node_id"] == "goblin_ambush"
    assert update.adventure["deferred_node_ids"] == ["phandalin"]
    assert update.adventure["breadcrumb_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin", "goblin_ambush"]
    assert update.state_update["adventure_runtime_directive"]["kind"] == "node_revisited"


def test_runtime_revisits_completed_breadcrumb_node_after_phandalin_detour():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "known_clue_ids": [],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "deferred_node_ids": [],
            "transition_log": [],
        }
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[HumanMessage(content="巴森说冈德伦没到，我们回遇袭地点找线索。")],
        director=FakeDirector(
            AdventureProgressDecision(
                target_node_id="goblin_ambush",
                transition_kind="revisit",
                confidence=0.9,
                needs_player_choice=False,
            )
        ),
    )

    assert update.applied == "revisited"
    assert update.adventure["active_node_id"] == "goblin_ambush"
    assert update.adventure["deferred_node_ids"] == ["phandalin"]
    assert update.adventure["breadcrumb_node_ids"] == ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin", "goblin_ambush"]
    assert update.state_update["adventure_runtime_directive"]["kind"] == "node_revisited"


def test_pre_turn_runtime_revisits_deferred_node_before_main_reply():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": ["goblin_trail"],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "deferred_node_ids": ["goblin_ambush"],
            "transition_log": [],
        }
    }

    update = adjudicate_and_apply_pre_turn_adventure_progress(
        state,
        player_message="我们回去看看遇袭地点。",
        director=FakeDirector(
            AdventureProgressDecision(
                target_node_id="goblin_ambush",
                transition_kind="revisit",
                confidence=0.9,
                needs_player_choice=False,
            )
        ),
    )

    assert update.applied == "pre_turn_revisited"
    assert update.adventure["active_node_id"] == "goblin_ambush"
    assert update.adventure["deferred_node_ids"] == ["phandalin"]
    assert update.state_update["adventure_runtime_directive"]["kind"] == "node_revisited"


def test_runtime_records_guardrail_warning_from_director():
    update = adjudicate_and_apply_adventure_progress(
        _ambush_state(),
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                desync_detected=True,
                unsupported_claims=["冈德伦已被救出", "红标帮在酒馆埋伏"],
                warning="回到地精伏击节点，不要继续酒馆剧情。",
                reason="主持回复越过当前节点。",
            )
        ),
    )

    assert update.applied == "guardrail_warning"
    warning = update.state_update["adventure_guardrail_warning"]
    assert warning["node_id"] == "goblin_ambush"
    assert warning["unsupported_claims"] == ["冈德伦已被救出", "红标帮在酒馆埋伏"]


def test_runtime_does_not_warn_for_system_authorized_opening_companion():
    state = {
        "player": {"name": "温良"},
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

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                desync_detected=True,
                unsupported_claims=[
                    "玩家有一位名叫格林的同行战士伙伴",
                    "玩家在出发前结识了格林，甘德伦的信件也寄给了他",
                ],
                warning="不要引入未支持 NPC。",
                reason="开局同行战士由系统授权。",
            )
        ),
    )

    assert update.applied == ""
    assert update.state_update == {}


def test_runtime_keeps_warning_when_opening_companion_is_mixed_with_sildar():
    state = {
        "player": {"name": "温良"},
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

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                desync_detected=True,
                unsupported_claims=["修达作为开局友方战士同伴和玩家一起出发"],
                warning="不要混淆格林和修达。",
                reason="开局同伴被混同为剧情 NPC。",
            )
        ),
    )

    assert update.state_update["adventure_guardrail_warning"]["unsupported_claims"] == [
        "修达作为开局友方战士同伴和玩家一起出发"
    ]


def test_runtime_filters_known_module_alias_claims_but_keeps_state_leaps():
    update = adjudicate_and_apply_adventure_progress(
        _ambush_state(),
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                desync_detected=True,
                unsupported_claims=[
                    "目的地为与冈德伦·洛克希尔会合——当前节点未提及该 NPC 或任务目标",
                    "玩家已抵达凡戴尔镇——当前节点未包含凡戴尔镇作为可达地点",
                ],
                warning="不要提前引入未解锁地点。",
                reason="过滤音译差异，但保留实际状态跳跃。",
            )
        ),
    )

    assert update.applied == "guardrail_warning"
    warning = update.state_update["adventure_guardrail_warning"]
    assert warning["unsupported_claims"] == ["玩家已抵达凡戴尔镇——当前节点未包含凡戴尔镇作为可达地点"]


def test_director_payload_keeps_full_history_and_structured_runtime_context():
    fake_llm = FakeSummaryLLMService()
    director = LLMAdventureDirector(llm_service=fake_llm)
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "completed_node_ids": [],
            "known_clue_ids": ["goblin_trail"],
            "completed_event_ids": ["goblin_ambush_resolved"],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
        "adventure_runtime_directive": {
            "kind": "node_advanced",
            "node_id": "cragmaw_hideout_entrance",
            "instruction": "先按当前节点事实重整场景。",
        },
        "adventure_guardrail_warning": {
            "node_id": "goblin_ambush",
            "warning": "回到当前节点。",
            "unsupported_claims": ["酒馆剧情"],
            "reason": "越界。",
        },
    }
    messages = [
        HumanMessage(content="我去检查伏击地点。"),
        HumanMessage(content="[系统:运行状态帧]\n这条不应进入 director 历史。"),
        AIMessage(content="我先按工具确认地点。", tool_calls=[{"name": "manage_adventure", "args": {"action": "load_node"}, "id": "call_1"}]),
        ToolMessage(content='{"ok": true}', tool_call_id="call_1", name="manage_adventure"),
        HumanMessage(content="继续往前走。"),
    ]

    director.adjudicate(state=state, recent_messages=messages, session_id="director-history-demo")

    payload = json.loads(fake_llm.calls[0]["summary_input"])
    history = payload["message_history"]

    assert fake_llm.calls[0]["summary_input"].index('"director_contract"') < fake_llm.calls[0]["summary_input"].index('"message_history"')
    assert fake_llm.calls[0]["summary_input"].index('"message_history"') < fake_llm.calls[0]["summary_input"].index('"active_node_pointer"')
    assert fake_llm.calls[0]["summary_input"].index('"active_node_pointer"') < fake_llm.calls[0]["summary_input"].index('"adventure_state"')
    assert fake_llm.calls[0]["summary_input"].index('"adventure_state"') < fake_llm.calls[0]["summary_input"].index('"runtime_context"')
    assert fake_llm.calls[0]["summary_input"].index('"runtime_context"') < fake_llm.calls[0]["summary_input"].index('"turn_context"')
    assert len(history) == 5
    assert "current_node" not in payload
    assert history[0]["content"] == "我去检查伏击地点。"
    assert "system_authorized_facts" not in payload["director_contract"]
    assert history[1]["tool_calls"][0]["name"] == "manage_adventure"
    assert history[1]["tool_calls"][0]["args"] == {"action": "load_node"}
    assert "id" not in history[1]["tool_calls"][0]
    assert "tool_call_id" not in history[2]
    assert history[2]["role"] == "tool:manage_adventure"
    assert history[-2]["content"] == "继续往前走。"
    assert history[-1]["role"] == "system:adventure_node_frame"
    assert history[-1]["node_id"] == "goblin_ambush"
    assert payload["known_clue_window"] == ["goblin_trail"]
    assert payload["runtime_context"]["adventure_runtime_directive"]["node_id"] == "cragmaw_hideout_entrance"
    assert "adventure_reward_notice" not in payload["runtime_context"]
    assert payload["runtime_context"]["adventure_guardrail_warning"]["unsupported_claims"] == ["酒馆剧情"]
    assert payload["runtime_context"]["system_authorized_facts"]
    assert payload["turn_context"]["stage"] == "post_turn"
    assert payload["turn_context"]["player_message"] == "继续往前走。"


def test_director_keeps_adventure_node_frame_as_ledger_entry():
    fake_llm = FakeSummaryLLMService()
    director = LLMAdventureDirector(llm_service=fake_llm)
    state = _ambush_state()
    messages = [
        HumanMessage(content="我去检查伏击地点。"),
        build_adventure_node_frame_message("goblin_ambush"),
        HumanMessage(content="继续往前走。"),
    ]

    director.adjudicate(state=state, recent_messages=messages, session_id="director-node-frame-demo")

    payload = json.loads(fake_llm.calls[0]["summary_input"])
    history = payload["message_history"]
    node_frame = history[1]
    assert node_frame["role"] == "system:adventure_node_frame"
    assert node_frame["frame_type"] == "adventure_node"
    assert node_frame["node_id"] == "goblin_ambush"
    assert node_frame["current_node"]["id"] == "goblin_ambush"
    assert payload["active_node_pointer"]["active_node_id"] == "goblin_ambush"
    assert "只有最后一个" in payload["active_node_pointer"]["rule"]


def test_pre_turn_director_keeps_dict_adventure_node_frame_as_ledger_entry():
    fake_llm = FakeSummaryLLMService()
    director = LLMAdventureDirector(llm_service=fake_llm)
    node_frame = build_adventure_node_frame_message("goblin_ambush")
    state = {
        **_ambush_state(),
        "messages": [
            {"role": "human", "content": "我去检查伏击地点。"},
            {"role": "human", "content": node_frame.content},
        ],
    }

    director.adjudicate_pre_turn(state=state, player_message="继续观察", session_id="director-node-frame-pre")

    payload = json.loads(fake_llm.calls[0]["summary_input"])
    node_frames = [
        item
        for item in payload["message_history"]
        if item["role"] == "system:adventure_node_frame"
    ]
    assert len(node_frames) == 1
    assert node_frames[0]["node_id"] == "goblin_ambush"
    assert node_frames[0]["current_node"]["id"] == "goblin_ambush"


def test_director_candidate_and_returnable_nodes_use_index_views():
    fake_llm = FakeSummaryLLMService()
    director = LLMAdventureDirector(llm_service=fake_llm)
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "cragmaw_hideout_entrance",
            "unlocked_node_ids": [
                "adventure_hook_meet_me_in_phandalin",
                "goblin_ambush",
                "cragmaw_hideout_entrance",
            ],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": [
                "adventure_hook_meet_me_in_phandalin",
                "goblin_ambush",
                "cragmaw_hideout_entrance",
            ],
            "deferred_node_ids": ["goblin_ambush"],
            "transition_log": [],
        },
    }

    director.adjudicate(
        state=state,
        recent_messages=[HumanMessage(content="我想回到伏击地点重新检查马尸。")],
        session_id="node-index-view-demo",
    )

    payload = json.loads(fake_llm.calls[0]["summary_input"])
    indexed_nodes = [*payload["candidate_nodes"], *payload["returnable_nodes"]]

    assert indexed_nodes
    assert any(item["id"] == "goblin_ambush" for item in indexed_nodes)
    for item in indexed_nodes:
        assert "dm_summary" in item
        assert "clue_ids" in item
        assert "event_ids" in item
        assert "exit_ids" in item
        assert "scene_beats" not in item
        assert "rules_notes" not in item
        assert "fallbacks" not in item
        assert "clues" not in item
        assert "exits" not in item


def test_director_history_uses_same_roles_for_state_dicts_and_messages():
    fake_llm = FakeSummaryLLMService()
    director = LLMAdventureDirector(llm_service=fake_llm)
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
        "messages": [
            HumanMessage(content="检查马尸。"),
            AIMessage(content="我检查痕迹。", tool_calls=[{"name": "inspect_scene", "args": {"target": "horse"}, "id": "call_old"}]),
            ToolMessage(content="发现黑羽箭。", tool_call_id="call_old", name="inspect_scene"),
        ],
    }

    director.adjudicate_pre_turn(state=state, player_message="继续观察", session_id="role-cache-pre")
    director.adjudicate(
        state=state,
        recent_messages=[
            HumanMessage(content="检查马尸。"),
            AIMessage(content="我检查痕迹。", tool_calls=[{"name": "inspect_scene", "args": {"target": "horse"}, "id": "call_old"}]),
            ToolMessage(content="发现黑羽箭。", tool_call_id="call_old", name="inspect_scene"),
            HumanMessage(content="继续观察"),
        ],
        session_id="role-cache-post",
    )

    pre_payload = json.loads(fake_llm.calls[0]["summary_input"])
    post_payload = json.loads(fake_llm.calls[1]["summary_input"])

    assert pre_payload["message_history"][:3] == post_payload["message_history"][:3]
    assert [item["role"] for item in pre_payload["message_history"][:3]] == [
        "human",
        "assistant",
        "tool:inspect_scene",
    ]


def test_director_node_frame_current_node_view_keeps_ids_but_compacts_repeated_facts():
    fake_llm = FakeSummaryLLMService()
    director = LLMAdventureDirector(llm_service=fake_llm)
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": [
                "adventure_hook_meet_me_in_phandalin",
                "goblin_ambush",
            ],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": [],
            "completed_event_ids": [],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": [
                "adventure_hook_meet_me_in_phandalin",
                "goblin_ambush",
            ],
            "deferred_node_ids": [],
            "transition_log": [],
        },
    }

    director.adjudicate(
        state=state,
        recent_messages=[
            build_adventure_node_frame_message("goblin_ambush"),
            HumanMessage(content="我搜索伏击现场。"),
        ],
        session_id="compact-node-view",
    )

    payload = json.loads(fake_llm.calls[0]["summary_input"])
    node_frame = payload["message_history"][0]
    current_node = node_frame["current_node"]
    source_node = get_adventure_store().get_node("goblin_ambush")

    assert "current_node" not in payload
    assert node_frame["role"] == "system:adventure_node_frame"
    clue_ids = {item["id"] for item in current_node["clues"]}
    exit_ids = {item["id"] for item in current_node["exits"]}
    reward_ids = {item["id"] for item in current_node["rewards"]}

    assert clue_ids == {item["id"] for item in source_node.clues}
    assert exit_ids == {item.id for item in source_node.exits}
    assert reward_ids == {item["id"] for item in source_node.rewards}
    assert set(current_node["events"]) == set(source_node.events)
    assert any(item.get("alias_of") for item in current_node["exits"])
    assert len(json.dumps(current_node, ensure_ascii=False, separators=(",", ":"))) < 16_000
    assert len(current_node["scene_beats"]) <= len(source_node.scene_beats)
    assert len(current_node["rules_notes"]) <= len(source_node.rules_notes)


def test_spider_web_hides_stale_opening_return_nodes_from_director():
    adventure = {
        "module_id": "lost_mine",
        "active_node_id": "spider_web_overview",
        "unlocked_node_ids": ["goblin_ambush", "cragmaw_hideout_klarg_cave", "phandalin", "spider_web_overview"],
        "completed_node_ids": ["goblin_ambush", "cragmaw_hideout_klarg_cave"],
        "known_clue_ids": [],
        "completed_event_ids": [],
        "claimed_reward_ids": [],
        "pending_exit_option_ids": [],
        "breadcrumb_node_ids": [
            "adventure_hook_meet_me_in_phandalin",
            "goblin_ambush",
            "cragmaw_hideout_entrance",
            "cragmaw_hideout_klarg_cave",
            "phandalin",
            "spider_web_overview",
        ],
        "deferred_node_ids": ["goblin_ambush", "cragmaw_hideout_klarg_cave", "phandalin"],
        "transition_log": [],
    }

    returnable = returnable_node_ids(adventure, "spider_web_overview")

    assert "phandalin" in returnable
    assert "goblin_ambush" not in returnable
    assert "cragmaw_hideout_entrance" not in returnable
    assert "cragmaw_hideout_klarg_cave" not in returnable


def test_wave_echo_hides_older_chapter_return_nodes_from_director():
    adventure = {
        "module_id": "lost_mine",
        "active_node_id": "wave_echo_forge_of_spells",
        "unlocked_node_ids": ["phandalin", "old_owl_well", "cragmaw_castle_search", "wave_echo_forge_of_spells"],
        "completed_node_ids": [],
        "known_clue_ids": [],
        "completed_event_ids": [],
        "claimed_reward_ids": [],
        "pending_exit_option_ids": [],
        "breadcrumb_node_ids": [
            "goblin_ambush",
            "phandalin",
            "old_owl_well",
            "cragmaw_castle_search",
            "wave_echo_overview",
            "wave_echo_forge_of_spells",
        ],
        "deferred_node_ids": ["goblin_ambush", "old_owl_well", "cragmaw_castle_search", "phandalin", "wave_echo_starry_cavern"],
        "transition_log": [],
    }

    returnable = returnable_node_ids(adventure, "wave_echo_forge_of_spells")

    assert "phandalin" in returnable
    assert "wave_echo_overview" in returnable
    assert "wave_echo_starry_cavern" in returnable
    assert "goblin_ambush" not in returnable
    assert "old_owl_well" not in returnable
    assert "cragmaw_castle_search" not in returnable


def test_pre_turn_director_prompt_rejects_generic_movement_on_multi_exit_nodes():
    fake_llm = FakeSummaryLLMService()
    director = LLMAdventureDirector(llm_service=fake_llm)
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": ["delivery_job", "gundren_went_ahead", "phandalin_destination"],
            "completed_event_ids": ["enter_goblin_ambush"],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
        "messages": [],
    }

    director.adjudicate_pre_turn(state=state, player_message="继续前进", session_id="pre-turn-prompt-demo")

    prompt = fake_llm.calls[0]["system_prompt"]
    assert "泛化行动必须输出 null" in prompt
    assert "除非玩家同时明确说出去凡达林、追踪地精踪迹、进入洞口、返回遇袭地点等具体目标" in prompt


def test_director_uses_shared_prompt_and_stage_labels_for_both_turns():
    fake_llm = FakeSummaryLLMService()
    director = LLMAdventureDirector(llm_service=fake_llm)
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "goblin_ambush",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "completed_node_ids": ["adventure_hook_meet_me_in_phandalin"],
            "known_clue_ids": ["delivery_job", "gundren_went_ahead", "phandalin_destination"],
            "completed_event_ids": ["enter_goblin_ambush"],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush"],
            "deferred_node_ids": [],
            "transition_log": [],
        },
        "messages": [HumanMessage(content="继续前进")],
    }

    director.adjudicate_pre_turn(state=state, player_message="继续前进", session_id="shared-prompt-pre")
    director.adjudicate(state=state, recent_messages=[HumanMessage(content="继续前进")], session_id="shared-prompt-post")

    assert fake_llm.calls[0]["system_prompt"] == fake_llm.calls[1]["system_prompt"]
    pre_payload = json.loads(fake_llm.calls[0]["summary_input"])
    post_payload = json.loads(fake_llm.calls[1]["summary_input"])
    assert pre_payload["turn_context"]["stage"] == "pre_turn"
    assert post_payload["turn_context"]["stage"] == "post_turn"
    assert pre_payload["turn_context"]["player_message"] == "继续前进"
    assert post_payload["turn_context"]["player_message"] == "继续前进"


def test_runtime_writes_director_visible_clue_window_without_dropping_full_clues():
    state = {
        "adventure": {
            "module_id": "lost_mine",
            "active_node_id": "phandalin",
            "unlocked_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "completed_node_ids": [],
            "known_clue_ids": ["delivery_job", "redbrands_control_phandalin", "glasstaff_name", "sildar_plan"],
            "completed_event_ids": [],
            "claimed_reward_ids": [],
            "pending_exit_option_ids": [],
            "breadcrumb_node_ids": ["adventure_hook_meet_me_in_phandalin", "goblin_ambush", "phandalin"],
            "deferred_node_ids": [],
            "transition_log": [],
        }
    }

    update = adjudicate_and_apply_adventure_progress(
        state,
        recent_messages=[],
        director=FakeDirector(
            AdventureProgressDecision(
                visible_clue_ids=["glasstaff_name", "fake_clue", "sildar_plan"],
            )
        ),
    )

    assert update.state_update["adventure_visible_clue_ids"] == ["glasstaff_name", "sildar_plan"]
    assert update.adventure is None
