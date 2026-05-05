"""结构化怪物动作第一阶段验收测试。"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import patch

import d20

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.graph.state import AttackInfo, CombatantState, CombatState
from app.monsters.lost_mine import get_lost_mine_actions, get_lost_mine_traits
from app.services.tools._helpers import advance_turn


def _invoke_tool(tool_func, *, tool_input: dict) -> object:
    """用 LangChain ToolCall 格式调用含注入参数的工具。"""
    tool_call = {
        "name": tool_func.name,
        "args": tool_input,
        "id": "test-call-id",
        "type": "tool_call",
    }
    return tool_func.invoke(tool_call)


def _unit(
    uid: str,
    name: str,
    *,
    hp: int = 30,
    ac: int = 10,
    actions_slug: str | None = None,
    attacks: list[AttackInfo] | None = None,
) -> dict:
    """生成带结构化动作的最小战斗单位。"""
    return CombatantState(
        id=uid,
        name=name,
        side="enemy",
        hp=hp,
        max_hp=hp,
        ac=ac,
        base_ac=ac,
        speed=30,
        abilities={"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        modifiers={"str": 0, "dex": 0, "con": 0, "int": 0, "wis": 0, "cha": 0},
        attacks=attacks or [],
        actions=get_lost_mine_actions(actions_slug) if actions_slug else [],
        traits=get_lost_mine_traits(actions_slug) if actions_slug else [],
        action_recharges={action.id: True for action in get_lost_mine_actions(actions_slug) if action.recharge} if actions_slug else {},
    ).model_dump()


def _state(attacker: dict, targets: list[dict], *, space: dict | None = None) -> dict:
    """构建怪物工具需要的最小 combat state。"""
    participants = {attacker["id"]: attacker, **{target["id"]: target for target in targets}}
    return {
        "combat": CombatState(
            round=1,
            participants={uid: CombatantState(**unit) for uid, unit in participants.items()},
            initiative_order=list(participants),
            current_actor_id=attacker["id"],
        ),
        **({"space": space} if space else {}),
    }


def _flat_rolls(values: list[str]):
    """把 d20.roll 固定成顺序结果，便于测试命中、伤害和豁免分支。"""
    rolls = iter(values)
    real_roll = d20.roll

    def _roll(_: str):
        return real_roll(next(rolls))

    return _roll


def test_goblin_actions_keep_existing_attack_behavior():
    from app.services.tool_service import use_monster_action

    goblin = _unit("goblin_1", "Goblin", actions_slug="goblin")
    target = _unit("target_1", "Target", hp=20, ac=10)
    state = _state(goblin, [target])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15", "5"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "goblin_1", "target_ids": ["target_1"], "action_id": "scimitar", "state": state},
        ).update

    assert "Scimitar" in result["messages"][0].content
    assert result["combat"]["participants"]["target_1"]["hp"] == 15
    assert result["combat"]["participants"]["goblin_1"]["action_available"] is False


def test_structured_simple_attack_keeps_player_reaction_window():
    from app.services.tool_service import use_monster_action

    goblin = _unit("goblin_1", "Goblin", actions_slug="goblin")
    player = _unit("player_hero", "Hero", hp=20, ac=10)
    player["side"] = "player"
    player["known_spells"] = ["shield"]
    player["resources"] = {"spell_slot_lv1": 1}
    player["reaction_available"] = True
    combat = CombatState(
        round=1,
        participants={"goblin_1": CombatantState(**goblin)},
        initiative_order=["goblin_1", "player_hero"],
        current_actor_id="goblin_1",
    )
    state = {"combat": combat, "player": player}

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "goblin_1", "target_ids": ["player_hero"], "action_id": "scimitar", "state": state},
        ).update

    assert result["pending_reaction"]["attacker_id"] == "goblin_1"
    assert result["messages"][0].additional_kwargs["hidden_from_ui"] is True
    assert "hp_changes" not in result
    assert result["player"]["hp"] == 20


def test_wolf_bite_pauses_for_player_shield_before_prone_save():
    """狼咬击带命中后效果，也必须先给玩家护盾术反应窗口。"""
    from app.services.tool_service import use_monster_action

    wolf = _unit("wolf_1", "Wolf", actions_slug="wolf")
    player = _unit("player_hero", "Hero", hp=20, ac=10)
    player["side"] = "player"
    player["known_spells"] = ["shield"]
    player["resources"] = {"spell_slot_lv1": 1}
    player["reaction_available"] = True
    combat = CombatState(
        round=1,
        participants={"wolf_1": CombatantState(**wolf)},
        initiative_order=["wolf_1", "player_hero"],
        current_actor_id="wolf_1",
    )
    state = {"combat": combat, "player": player}

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "wolf_1", "target_ids": ["player_hero"], "action_id": "bite", "state": state},
        ).update

    assert result["pending_reaction"]["attacker_id"] == "wolf_1"
    assert result["pending_reaction"]["monster_attack_action"]["id"] == "bite"
    assert "hp_changes" not in result
    assert result["player"]["hp"] == 20


def test_wolf_bite_resume_after_declining_reaction_applies_damage_and_prone():
    """放弃护盾术后，应继续结算同一个狼咬击的伤害和倒地豁免。"""
    from app.graph import nodes
    from app.services.tool_service import use_monster_action

    wolf = _unit("wolf_1", "Wolf", actions_slug="wolf")
    player = _unit("player_hero", "Hero", hp=20, ac=10)
    player["side"] = "player"
    player["known_spells"] = ["shield"]
    player["resources"] = {"spell_slot_lv1": 1}
    player["reaction_available"] = True
    combat = CombatState(
        round=1,
        participants={"wolf_1": CombatantState(**wolf)},
        initiative_order=["wolf_1", "player_hero"],
        current_actor_id="wolf_1",
    )
    state = {"combat": combat, "player": player}

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15"])):
        pending_state = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "wolf_1", "target_ids": ["player_hero"], "action_id": "bite", "state": state},
        ).update

    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["6", "5"])):
        resolved = nodes.resolve_reaction_node({
            "combat": pending_state["combat"],
            "player": pending_state["player"],
            "pending_reaction": pending_state["pending_reaction"],
            "reaction_choice": {"spell_id": None},
        })

    assert resolved["player"]["hp"] == 14
    assert any(condition["id"] == "prone" for condition in resolved["player"]["conditions"])
    assert resolved["combat"]["participants"]["wolf_1"]["action_available"] is False


def test_monster_spell_creates_counterspell_reaction_window():
    from app.services.tool_service import use_monster_action

    skull = _unit("skull_1", "Flameskull", actions_slug="flameskull")
    player = _unit("player_hero", "Hero", hp=30, ac=10)
    player["side"] = "player"
    player["known_spells"] = ["counterspell"]
    player["resources"] = {"spell_slot_lv3": 1}
    player["reaction_available"] = True
    combat = CombatState(
        round=1,
        participants={"skull_1": CombatantState(**skull)},
        initiative_order=["skull_1", "player_hero"],
        current_actor_id="skull_1",
    )
    state = {"combat": combat, "player": player}

    result = _invoke_tool(
        use_monster_action,
        tool_input={"actor_id": "skull_1", "target_ids": ["player_hero"], "action_id": "fireball", "state": state},
    ).update

    assert result["pending_reaction"]["trigger"] == "on_enemy_cast"
    assert result["pending_reaction"]["spell_action"]["spell_id"] == "fireball"
    assert result["pending_reaction"]["available_reactions"][0]["spell_id"] == "counterspell"
    assert "hp_changes" not in result


def test_counterspell_prompt_blocks_monster_spell_after_player_choice():
    from app.graph import nodes
    from app.services.tool_service import use_monster_action

    skull = _unit("skull_1", "Flameskull", actions_slug="flameskull")
    player = _unit("player_hero", "Hero", hp=30, ac=10)
    player["side"] = "player"
    player["known_spells"] = ["counterspell"]
    player["resources"] = {"spell_slot_lv3": 1}
    player["reaction_available"] = True
    combat = CombatState(
        round=1,
        participants={"skull_1": CombatantState(**skull)},
        initiative_order=["skull_1", "player_hero"],
        current_actor_id="skull_1",
    )
    state = {"combat": combat, "player": player}

    pending_state = _invoke_tool(
        use_monster_action,
        tool_input={"actor_id": "skull_1", "target_ids": ["player_hero"], "action_id": "fireball", "state": state},
    ).update
    resolved = nodes.resolve_reaction_node({
        "combat": pending_state["combat"],
        "player": pending_state["player"],
        "pending_reaction": pending_state["pending_reaction"],
        "reaction_choice": {"spell_id": "counterspell", "slot_level": 3},
    })

    assert resolved["pending_reaction"] is None
    assert resolved["player"]["hp"] == 30
    assert resolved["player"]["resources"]["spell_slot_lv3"] == 0
    assert resolved["combat"]["participants"]["skull_1"]["action_available"] is False
    assert "法术反制" in resolved["messages"][0].content
    assert "被打断" in resolved["messages"][0].content


def test_unknown_action_id_fails_fast():
    from app.services.tool_service import attack_action, use_monster_action

    goblin = _unit("goblin_1", "Goblin", actions_slug="goblin")
    target = _unit("target_1", "Target")
    state = _state(goblin, [target])

    result = _invoke_tool(
        use_monster_action,
        tool_input={"actor_id": "goblin_1", "target_ids": ["target_1"], "action_id": "bad_action", "state": state},
    ).update
    assert "未知动作" in result["messages"][0].content
    assert "scimitar" in result["messages"][0].content

    legacy_result = _invoke_tool(
        attack_action,
        tool_input={"attacker_id": "goblin_1", "target_id": "target_1", "attack_name": "Bad Attack", "state": state},
    ).update
    assert "未知攻击" in legacy_result["messages"][0].content
    assert "徒手" not in legacy_result["messages"][0].content


def test_wolf_bite_can_apply_prone_on_failed_save():
    from app.services.tool_service import use_monster_action

    wolf = _unit("wolf_1", "Wolf", actions_slug="wolf")
    target = _unit("target_1", "Target", hp=20, ac=10)
    state = _state(wolf, [target])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15", "6", "5"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "wolf_1", "target_ids": ["target_1"], "action_id": "bite", "state": state},
        ).update

    target_after = result["combat"]["participants"]["target_1"]
    assert any(condition["id"] == "prone" for condition in target_after["conditions"])


def test_ghoul_claws_apply_paralyzed_and_end_turn_save_can_remove():
    from app.services.tool_service import use_monster_action

    ghoul = _unit("ghoul_1", "Ghoul", actions_slug="ghoul")
    target = _unit("target_1", "Target", hp=20, ac=10)
    state = _state(ghoul, [target])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15", "5", "5"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "ghoul_1", "target_ids": ["target_1"], "action_id": "claws", "state": state},
        ).update

    target_after = result["combat"]["participants"]["target_1"]
    assert any(condition["id"] == "paralyzed" for condition in target_after["conditions"])

    combat = result["combat"]
    combat["current_actor_id"] = "target_1"
    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15"])):
        advance_turn(combat)

    assert not any(condition["id"] == "paralyzed" for condition in combat["participants"]["target_1"]["conditions"])


def test_giant_spider_bite_applies_poison_damage_save_half():
    from app.services.tool_service import use_monster_action

    spider = _unit("spider_1", "Giant Spider", actions_slug="giant-spider")
    target = _unit("target_1", "Target", hp=40, ac=10)
    state = _state(spider, [target])

    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["15", "6", "16", "8"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "spider_1", "target_ids": ["target_1"], "action_id": "bite", "state": state},
        ).update

    assert result["combat"]["participants"]["target_1"]["hp"] == 30
    assert "追加伤害骰" in result["messages"][0].content


def test_giant_spider_web_recharge_and_restrained():
    from app.services.tool_service import use_monster_action

    spider = _unit("spider_1", "Giant Spider", actions_slug="giant-spider")
    target = _unit("target_1", "Target", hp=20, ac=10)
    state = _state(spider, [target])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "spider_1", "target_ids": ["target_1"], "action_id": "web", "state": state},
        ).update

    spider_after = result["combat"]["participants"]["spider_1"]
    target_after = result["combat"]["participants"]["target_1"]
    assert spider_after["action_recharges"]["web"] is False
    assert any(condition["id"] == "restrained" for condition in target_after["conditions"])

    combat = result["combat"]
    combat["current_actor_id"] = "target_1"
    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["5"])):
        advance_turn(combat)
    assert combat["participants"]["spider_1"]["action_recharges"]["web"] is True


def test_redbrand_multiattack_consumes_one_action_and_hits_twice():
    from app.services.tool_service import use_monster_action

    redbrand = _unit("redbrand_1", "Redbrand Ruffian", actions_slug="redbrand-ruffian")
    target = _unit(
        "target_1",
        "Target",
        hp=30,
        ac=10,
        attacks=[AttackInfo(name="Strike", attack_bonus=2, damage_dice="1d6")],
    )
    state = _state(redbrand, [target])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15", "5", "15", "5"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "redbrand_1", "target_ids": ["target_1"], "action_id": "multiattack", "state": state},
        ).update

    assert result["combat"]["participants"]["target_1"]["hp"] == 20
    assert result["combat"]["participants"]["redbrand_1"]["action_available"] is False
    assert result["messages"][0].content.count("Shortsword") == 2


def test_owlbear_multiattack_uses_beak_and_claws():
    from app.services.tool_service import use_monster_action

    owlbear = _unit("owlbear_1", "Owlbear", actions_slug="owlbear")
    target = _unit("target_1", "Target", hp=40, ac=10)
    state = _state(owlbear, [target])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15", "8", "15", "9"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "owlbear_1", "target_ids": ["target_1"], "action_id": "multiattack", "state": state},
        ).update

    assert "Beak" in result["messages"][0].content
    assert "Claws" in result["messages"][0].content
    assert result["combat"]["participants"]["target_1"]["hp"] == 23


def test_zombie_undead_fortitude_sets_hp_to_one_on_success():
    from app.services.tool_service import use_monster_action

    attacker = _unit(
        "attacker_1",
        "Attacker",
        attacks=[AttackInfo(name="Club", attack_bonus=5, damage_dice="10", damage_type="bludgeoning")],
    )
    zombie = _unit("zombie_1", "Zombie", hp=5, ac=10, actions_slug="zombie")
    state = _state(attacker, [zombie])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15", "10", "20"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "attacker_1", "target_ids": ["zombie_1"], "action_id": "Club", "state": state},
        ).update

    assert result["combat"]["participants"]["zombie_1"]["hp"] == 1
    assert result["hp_changes"][0]["new_hp"] == 1


def test_green_dragon_poison_breath_hits_cone_targets_and_recharges():
    from app.services.tool_service import use_monster_action

    dragon = _unit("dragon_1", "Young Green Dragon", actions_slug="young-green-dragon")
    center = _unit("center_1", "Center", hp=80)
    outside = _unit("outside_1", "Outside", hp=80)
    space = {
        "active_map_id": "map_1",
        "maps": {"map_1": {"id": "map_1", "name": "Cave", "width": 100, "height": 100}},
        "placements": {
            "dragon_1": {"unit_id": "dragon_1", "map_id": "map_1", "position": {"x": 10, "y": 10}, "facing_deg": 0},
            "center_1": {"unit_id": "center_1", "map_id": "map_1", "position": {"x": 30, "y": 10}},
            "outside_1": {"unit_id": "outside_1", "map_id": "map_1", "position": {"x": 30, "y": 30}},
        },
    }
    state = _state(dragon, [center, outside], space=space)

    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["42", "5"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "dragon_1", "target_ids": [], "action_id": "poison_breath", "state": state},
        ).update

    assert result["combat"]["participants"]["center_1"]["hp"] == 38
    assert result["combat"]["participants"]["outside_1"]["hp"] == 80
    assert result["combat"]["participants"]["dragon_1"]["action_recharges"]["poison_breath"] is False

    combat = result["combat"]
    combat["current_actor_id"] = "outside_1"
    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["5"])):
        advance_turn(combat)
    assert combat["participants"]["dragon_1"]["action_recharges"]["poison_breath"] is True


def test_bugbear_brute_and_surprise_attack_add_bonus_damage_once():
    from app.services.tool_service import use_monster_action

    bugbear = _unit("bugbear_1", "Bugbear", actions_slug="bugbear")
    target = _unit("target_1", "Target", hp=50, ac=10)
    state = _state(bugbear, [target])

    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["15", "10", "7"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "bugbear_1", "target_ids": ["target_1"], "action_id": "morningstar", "state": state},
        ).update

    assert result["combat"]["participants"]["target_1"]["hp"] == 33
    assert "surprise_attack" in result["messages"][0].content


def test_hobgoblin_martial_advantage_requires_ally_near_target():
    from app.services.tool_service import use_monster_action

    hobgoblin = _unit("hobgoblin_1", "Hobgoblin", actions_slug="hobgoblin")
    ally = _unit("ally_1", "Ally")
    target = _unit("target_1", "Target", hp=40, ac=10)
    space = {
        "active_map_id": "map_1",
        "maps": {"map_1": {"id": "map_1", "name": "Room", "width": 80, "height": 80}},
        "placements": {
            "hobgoblin_1": {"unit_id": "hobgoblin_1", "map_id": "map_1", "position": {"x": 0, "y": 0}},
            "ally_1": {"unit_id": "ally_1", "map_id": "map_1", "position": {"x": 5, "y": 0}},
            "target_1": {"unit_id": "target_1", "map_id": "map_1", "position": {"x": 5, "y": 0}},
        },
    }
    state = _state(hobgoblin, [ally, target], space=space)

    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["15", "5", "7"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "hobgoblin_1", "target_ids": ["target_1"], "action_id": "longsword", "state": state},
        ).update

    assert result["combat"]["participants"]["target_1"]["hp"] == 28
    assert "martial_advantage" in result["messages"][0].content


def test_grick_multiattack_only_uses_beak_after_tentacles_hit():
    from app.services.tool_service import use_monster_action

    grick = _unit("grick_1", "Grick", actions_slug="grick")
    target = _unit("target_1", "Target", hp=40, ac=10)
    state = _state(grick, [target])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15", "8", "15", "5"])):
        hit_result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "grick_1", "target_ids": ["target_1"], "action_id": "multiattack", "state": state},
        ).update

    assert hit_result["combat"]["participants"]["target_1"]["hp"] == 27

    grick = _unit("grick_1", "Grick", actions_slug="grick")
    target = _unit("target_1", "Target", hp=40, ac=20)
    state = _state(grick, [target])
    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["5"])):
        miss_result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "grick_1", "target_ids": ["target_1"], "action_id": "multiattack", "state": state},
        ).update

    assert miss_result["combat"]["participants"]["target_1"]["hp"] == 40
    assert "后续连击未触发" in miss_result["messages"][0].content


def test_nothic_rotting_gaze_deals_necrotic_damage_on_failed_save():
    from app.services.tool_service import use_monster_action

    nothic = _unit("nothic_1", "Nothic", actions_slug="nothic")
    target = _unit(
        "target_1",
        "Target",
        hp=30,
        ac=10,
        attacks=[AttackInfo(name="Strike", attack_bonus=2, damage_dice="1d6")],
    )
    state = _state(nothic, [target])

    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["5", "9"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "nothic_1", "target_ids": ["target_1"], "action_id": "rotting_gaze", "state": state},
        ).update

    assert result["combat"]["participants"]["target_1"]["hp"] == 21
    assert "Rotting Gaze" in result["messages"][0].content


def test_stirge_attaches_and_drains_at_turn_start_then_can_detach():
    from app.services.tool_service import use_monster_action

    stirge = _unit("stirge_1", "Stirge", actions_slug="stirge")
    target = _unit("target_1", "Target", hp=30, ac=10)
    state = _state(stirge, [target])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15", "5"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "stirge_1", "target_ids": ["target_1"], "action_id": "blood_drain", "state": state},
        ).update

    stirge_after = result["combat"]["participants"]["stirge_1"]
    assert any(condition["id"] == "attached" for condition in stirge_after["conditions"])

    combat = result["combat"]
    combat["current_actor_id"] = "target_1"
    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["4"])):
        advance_turn(combat)

    assert combat["participants"]["target_1"]["hp"] == 21

    detach_state = {"combat": CombatState(**combat)}
    detached = _invoke_tool(
        use_monster_action,
        tool_input={"actor_id": "stirge_1", "target_ids": [], "action_id": "detach", "state": detach_state},
    ).update
    assert not any(condition["id"] == "attached" for condition in detached["combat"]["participants"]["stirge_1"]["conditions"])


def test_wraith_life_drain_can_reduce_max_hp_on_failed_save():
    from app.services.tool_service import use_monster_action

    wraith = _unit("wraith_1", "Wraith", actions_slug="wraith")
    target = _unit("target_1", "Target", hp=50, ac=10)
    state = _state(wraith, [target])

    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["15", "20", "5"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "wraith_1", "target_ids": ["target_1"], "action_id": "life_drain", "state": state},
        ).update

    target_after = result["combat"]["participants"]["target_1"]
    assert target_after["hp"] == 30
    assert target_after["max_hp"] == 30


def test_evil_mage_spell_action_reuses_spell_registry():
    from app.services.tool_service import use_monster_action

    mage = _unit("mage_1", "Evil Mage", actions_slug="evil-mage")
    target = _unit("target_1", "Target", hp=30, ac=10)
    state = _state(mage, [target])

    with patch("app.spells.magic_missile.d20.roll", side_effect=_flat_rolls(["4", "4", "4"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "mage_1", "target_ids": ["target_1"], "action_id": "magic_missile", "state": state},
        ).update

    assert "魔法飞弹" in result["messages"][0].content
    assert result["combat"]["participants"]["target_1"]["hp"] < 30
    assert result["combat"]["participants"]["mage_1"]["action_available"] is False


def test_monster_self_spell_does_not_require_explicit_target():
    from app.services.tool_service import use_monster_action

    skull = _unit("skull_1", "Flameskull", actions_slug="flameskull")
    state = _state(skull, [])

    result = _invoke_tool(
        use_monster_action,
        tool_input={"actor_id": "skull_1", "target_ids": [], "action_id": "blur", "state": state},
    ).update

    skull_after = result["combat"]["participants"]["skull_1"]
    assert any(condition["id"] == "blurred" for condition in skull_after["conditions"])
    assert skull_after["concentrating_on"] == "blur"


def test_monster_point_area_spell_expands_targets_from_space():
    from app.services.tool_service import use_monster_action

    skull = _unit("skull_1", "Flameskull", actions_slug="flameskull")
    near = _unit("near_1", "Near", hp=40)
    far = _unit("far_1", "Far", hp=40)
    space = {
        "active_map_id": "map_1",
        "maps": {"map_1": {"id": "map_1", "name": "Hall", "width": 200, "height": 200}},
        "placements": {
            "skull_1": {"unit_id": "skull_1", "map_id": "map_1", "position": {"x": 0, "y": 0}},
            "near_1": {"unit_id": "near_1", "map_id": "map_1", "position": {"x": 35, "y": 0}},
            "far_1": {"unit_id": "far_1", "map_id": "map_1", "position": {"x": 80, "y": 0}},
        },
    }
    state = _state(skull, [near, far], space=space)

    with patch("app.spells._resolvers.d20.roll", side_effect=_flat_rolls(["24", "5"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={
                "actor_id": "skull_1",
                "target_ids": [],
                "action_id": "fireball",
                "target_point": {"x": 35, "y": 0},
                "state": state,
            },
        ).update

    assert result["combat"]["participants"]["near_1"]["hp"] == 16
    assert result["combat"]["participants"]["far_1"]["hp"] == 40


def test_flaming_sphere_damages_creature_ending_turn_nearby():
    from app.conditions._base import build_condition_extra, create_condition

    skull = _unit("skull_1", "Flameskull", actions_slug="flameskull")
    target = _unit("target_1", "Target", hp=30)
    skull["conditions"] = [
        create_condition(
            "flaming_sphere",
            source_id="concentration:flaming_sphere",
            extra=build_condition_extra(position={"x": 10, "y": 0}, damage_dice="2d6"),
        )
    ]
    space = {
        "active_map_id": "map_1",
        "maps": {"map_1": {"id": "map_1", "name": "Hall", "width": 100, "height": 100}},
        "placements": {
            "skull_1": {"unit_id": "skull_1", "map_id": "map_1", "position": {"x": 0, "y": 0}},
            "target_1": {"unit_id": "target_1", "map_id": "map_1", "position": {"x": 12, "y": 0}},
        },
    }
    combat = CombatState(
        round=1,
        participants={"skull_1": CombatantState(**skull), "target_1": CombatantState(**target)},
        initiative_order=["target_1", "skull_1"],
        current_actor_id="target_1",
    ).model_dump()

    with patch("app.spells.flaming_sphere.d20.roll", side_effect=_flat_rolls(["6", "5"])):
        turn_text = advance_turn(combat, state={"combat": combat, "space": space})

    assert "焰球回合末" in turn_text
    assert combat["participants"]["target_1"]["hp"] == 24


def test_monster_misty_step_updates_space():
    from app.services.tool_service import use_monster_action

    mage = _unit("mage_1", "Evil Mage", actions_slug="evil-mage")
    space = {
        "active_map_id": "map_1",
        "maps": {"map_1": {"id": "map_1", "name": "Room", "width": 80, "height": 80}},
        "placements": {
            "mage_1": {"unit_id": "mage_1", "map_id": "map_1", "position": {"x": 0, "y": 0}},
        },
    }
    state = _state(mage, [], space=space)

    result = _invoke_tool(
        use_monster_action,
        tool_input={
            "actor_id": "mage_1",
            "target_ids": [],
            "action_id": "misty_step",
            "target_point": {"x": 20, "y": 0},
            "state": state,
        },
    ).update

    assert result["space"]["placements"]["mage_1"]["position"] == {"x": 20.0, "y": 0.0}
    assert result["combat"]["participants"]["mage_1"]["bonus_action_available"] is False


def test_counterspell_prompt_resume_preserves_monster_spell_space_context():
    from app.graph import nodes
    from app.services.tool_service import use_monster_action

    skull = _unit("skull_1", "Flameskull", actions_slug="flameskull")
    player = _unit("player_hero", "Hero", hp=40, ac=10)
    player["side"] = "player"
    player["known_spells"] = ["counterspell"]
    player["resources"] = {"spell_slot_lv3": 1}
    player["reaction_available"] = True
    near = _unit("near_1", "Near", hp=40)
    space = {
        "active_map_id": "map_1",
        "maps": {"map_1": {"id": "map_1", "name": "Hall", "width": 200, "height": 200}},
        "placements": {
            "skull_1": {"unit_id": "skull_1", "map_id": "map_1", "position": {"x": 0, "y": 0}},
            "player_hero": {"unit_id": "player_hero", "map_id": "map_1", "position": {"x": 35, "y": 0}},
            "near_1": {"unit_id": "near_1", "map_id": "map_1", "position": {"x": 40, "y": 0}},
        },
    }
    combat = CombatState(
        round=1,
        participants={"skull_1": CombatantState(**skull), "near_1": CombatantState(**near)},
        initiative_order=["skull_1", "player_hero", "near_1"],
        current_actor_id="skull_1",
    )
    state = {"combat": combat, "player": player, "space": space}

    pending_state = _invoke_tool(
        use_monster_action,
        tool_input={
            "actor_id": "skull_1",
            "target_ids": [],
            "action_id": "fireball",
            "target_point": {"x": 35, "y": 0},
            "state": state,
        },
    ).update

    with patch("app.spells._resolvers.d20.roll", side_effect=_flat_rolls(["24", "5", "5"])):
        resolved = nodes.resolve_reaction_node({
            "combat": pending_state["combat"],
            "player": pending_state["player"],
            "pending_reaction": pending_state["pending_reaction"],
            "reaction_choice": {"spell_id": None},
        })

    assert resolved["player"]["hp"] == 16
    assert resolved["combat"]["participants"]["near_1"]["hp"] == 16
    assert resolved["combat"]["participants"]["skull_1"]["action_available"] is False


def test_flameskull_fire_ray_multiattack_hits_twice():
    from app.services.tool_service import use_monster_action

    skull = _unit("skull_1", "Flameskull", actions_slug="flameskull")
    target = _unit("target_1", "Target", hp=40, ac=10)
    state = _state(skull, [target])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["15", "9", "15", "8"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "skull_1", "target_ids": ["target_1"], "action_id": "multiattack", "state": state},
        ).update

    assert result["combat"]["participants"]["target_1"]["hp"] == 23
    assert result["messages"][0].content.count("Fire Ray") == 2


def test_spectator_paralyzing_ray_applies_save_ends_condition():
    from app.services.tool_service import use_monster_action

    spectator = _unit("spectator_1", "Spectator", actions_slug="spectator")
    target = _unit("target_1", "Target", hp=30, ac=10)
    state = _state(spectator, [target])

    with patch("app.services.tools._helpers.d20.roll", side_effect=_flat_rolls(["5"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "spectator_1", "target_ids": ["target_1"], "action_id": "paralyzing_ray", "state": state},
        ).update

    condition = result["combat"]["participants"]["target_1"]["conditions"][0]
    assert condition["id"] == "paralyzed"
    assert condition["extra"]["save_ends"] == {"ability": "con", "dc": 13}


def test_spectator_eye_rays_can_apply_status_and_damage():
    from app.services.tool_service import use_monster_action

    spectator = _unit("spectator_1", "Spectator", actions_slug="spectator")
    first = _unit("first_1", "First", hp=30, ac=10)
    second = _unit("second_1", "Second", hp=30, ac=10)
    state = _state(spectator, [first, second])

    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["5", "5", "12"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={
                "actor_id": "spectator_1",
                "target_ids": ["first_1", "second_1"],
                "action_id": "eye_rays",
                "state": state,
            },
        ).update

    assert any(c["id"] == "confused" for c in result["combat"]["participants"]["first_1"]["conditions"])
    assert result["combat"]["participants"]["second_1"]["hp"] == 18


def test_confused_roll_can_block_action_on_turn_start():
    from app.services.tool_service import use_monster_action

    spectator = _unit("spectator_1", "Spectator", actions_slug="spectator")
    target = _unit(
        "target_1",
        "Target",
        hp=30,
        ac=10,
        attacks=[AttackInfo(name="Strike", attack_bonus=2, damage_dice="1d6")],
    )
    state = _state(spectator, [target])

    with patch("app.services.tools.monster_action_resolvers.d20.roll", side_effect=_flat_rolls(["5"])):
        result = _invoke_tool(
            use_monster_action,
            tool_input={"actor_id": "spectator_1", "target_ids": ["target_1"], "action_id": "confusion_ray", "state": state},
        ).update

    combat = result["combat"]
    combat["current_actor_id"] = "spectator_1"
    with patch("app.conditions.confused.d20.roll", side_effect=_flat_rolls(["3"])):
        turn_text = advance_turn(combat)

    blocked = _invoke_tool(
        use_monster_action,
        tool_input={"actor_id": "target_1", "target_ids": ["spectator_1"], "action_id": "Strike", "state": {"combat": CombatState(**combat)}},
    ).update

    assert "困惑射线" in turn_text
    assert "不能执行动作" in blocked["messages"][0].content
