"""职业动作工具测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.graph.state import CombatState
from app.allies.profiles import get_ally_profile
from app.services.tools.class_action_tools import use_class_action
from app.services.tools import get_tool_profile, get_tools


def _combat_state_for(actor: dict) -> CombatState:
    """构造只包含玩家回合的最小战斗状态。"""
    return CombatState(
        round=1,
        participants={},
        initiative_order=[actor["id"]],
        current_actor_id=actor["id"],
    )


def _fighter() -> dict:
    """构造具备 2 级战士基础职业动作的最小角色。"""
    return {
        "id": "fighter_1",
        "name": "Fighter",
        "side": "player",
        "level": 2,
        "hp": 10,
        "max_hp": 20,
        "class_features": ["second_wind", "action_surge"],
        "resources": {"second_wind_uses": 1, "action_surge_uses": 1},
        "resource_caps": {"second_wind_uses": 1, "action_surge_uses": 1},
        "action_available": False,
        "bonus_action_available": True,
    }


def _battle_master() -> dict:
    """构造 3 级战斗大师，用于验证战技选择只记录不执行。"""
    fighter = _fighter()
    fighter.update({
        "level": 3,
        "fighter_archetype": "battle_master",
        "class_features": ["second_wind", "action_surge", "combat_superiority", "student_of_war"],
        "maneuvers_known_count": 3,
        "maneuvers": [],
        "superiority_die": "1d8",
        "maneuver_save_dc": 13,
        "resources": {
            "second_wind_uses": 1,
            "action_surge_uses": 1,
            "superiority_dice": 4,
        },
        "resource_caps": {
            "second_wind_uses": 1,
            "action_surge_uses": 1,
            "superiority_dice": 4,
        },
    })
    return fighter


def test_use_class_action_lists_available_actions():
    """职业动作工具应列出角色当前能主动使用的动作。"""
    state = {"player": _fighter()}

    result = use_class_action.func(action_id="", state=state, tool_call_id="call-test")

    content = result.update["messages"][0].content
    assert "second_wind" in content
    assert "action_surge" in content


def test_use_class_feature_is_removed_from_all_tool_entries():
    """旧职业特性入口已删除，主动职业动作统一走 use_class_action。"""
    narrative_tools = {tool.name for tool in get_tool_profile("narrative")}
    combat_tools = {tool.name for tool in get_tool_profile("combat")}
    all_tools = {tool.name for tool in get_tools()}

    assert "use_class_feature" not in narrative_tools
    assert "use_class_feature" not in combat_tools
    assert "use_class_feature" not in all_tools


def test_level_1_fighter_lists_second_wind_only():
    """1 级战士只应看到回气，不应提前获得动作如潮。"""
    fighter = _fighter()
    fighter["level"] = 1
    fighter["class_features"] = ["second_wind"]
    fighter["resources"] = {"second_wind_uses": 1}
    state = {"player": fighter}

    result = use_class_action.func(action_id="", state=state, tool_call_id="call-test")

    content = result.update["messages"][0].content
    assert "second_wind" in content
    assert "action_surge" not in content


def test_opening_fighter_companion_is_named_grin_and_has_second_wind():
    """开局战士友方应明确是格林，并复用职业动作框架获得回气。"""
    ally = get_ally_profile("fighter_companion")
    state = {"player": _fighter(), "scene_units": {"fighter_companion": ally}}

    listed = use_class_action.func(
        action_id="",
        target_id="fighter_companion",
        state=state,
        tool_call_id="call-list",
    )

    assert ally["name"] == "格林"
    assert "second_wind" in listed.update["messages"][0].content
    assert "action_surge" not in listed.update["messages"][0].content


def test_opening_fighter_companion_can_use_second_wind_from_scene_units():
    """友方作为职业动作使用者时，应按场景单位写回自身资源和 HP。"""
    ally = get_ally_profile("fighter_companion")
    ally["hp"] = 5
    state = {"player": _fighter(), "scene_units": {"fighter_companion": ally}}

    with patch("app.services.class_actions.fighter.d20.roll") as roll:
        roll.return_value.total = 4
        roll.return_value.__str__ = lambda self: "1d10 (4)"
        result = use_class_action.func(
            action_id="second_wind",
            target_id="fighter_companion",
            state=state,
            tool_call_id="call-use",
        )

    updated = result.update["scene_units"]["fighter_companion"]
    assert updated["hp"] == 10
    assert updated["resources"]["second_wind_uses"] == 0
    assert result.update["hp_changes"][0]["old_hp"] == 5
    assert result.update["hp_changes"][0]["new_hp"] == 10


def test_level_2_fighter_lists_action_surge():
    """2 级战士应能看到动作如潮。"""
    state = {"player": _fighter()}

    result = use_class_action.func(action_id="", state=state, tool_call_id="call-test")

    assert "action_surge" in result.update["messages"][0].content


def test_second_wind_consumes_resource_and_heals():
    """回气应消耗 1 次资源，并恢复 1d10 + 战士等级的 HP。"""
    fighter = _fighter()
    state = {"player": fighter}

    with patch("app.services.class_actions.fighter.d20.roll") as roll:
        roll.return_value.total = 6
        roll.return_value.__str__ = lambda self: "1d10 (6)"
        result = use_class_action.func(action_id="second_wind", state=state, tool_call_id="call-test")

    updated = result.update["player"]
    assert updated["hp"] == 18
    assert updated["resources"]["second_wind_uses"] == 0
    assert result.update["hp_changes"][0]["old_hp"] == 10
    assert result.update["hp_changes"][0]["new_hp"] == 18


def test_second_wind_consumes_bonus_action_in_combat():
    """PHB 中回气是附赠动作，战斗内使用后应消耗 bonus_action_available。"""
    fighter = _fighter()
    state = {"player": fighter, "combat": _combat_state_for(fighter)}

    with patch("app.services.class_actions.fighter.d20.roll") as roll:
        roll.return_value.total = 4
        roll.return_value.__str__ = lambda self: "1d10 (4)"
        result = use_class_action.func(action_id="second_wind", state=state, tool_call_id="call-test")

    assert result.update["player"]["bonus_action_available"] is False
    assert result.update["player"]["resources"]["second_wind_uses"] == 0


def test_second_wind_rejects_when_bonus_action_spent_in_combat():
    """战斗内没有附赠动作时，回气不能消耗资源或回血。"""
    fighter = _fighter()
    fighter["bonus_action_available"] = False
    state = {"player": fighter, "combat": _combat_state_for(fighter)}

    result = use_class_action.func(action_id="second_wind", state=state, tool_call_id="call-test")

    assert "附赠动作已用尽" in result.update["messages"][0].content
    assert result.update["player"]["hp"] == 10
    assert result.update["player"]["resources"]["second_wind_uses"] == 1


def test_second_wind_rejects_when_resource_empty():
    """回气次数为 0 时不能回血。"""
    fighter = _fighter()
    fighter["resources"]["second_wind_uses"] = 0
    state = {"player": fighter}

    result = use_class_action.func(action_id="second_wind", state=state, tool_call_id="call-test")

    assert "已用尽" in result.update["messages"][0].content
    assert result.update["player"]["hp"] == 10
    assert result.update["player"]["resources"]["second_wind_uses"] == 0


def test_action_surge_rejects_outside_combat():
    """动作如潮依赖战斗动作经济，战斗外拒绝使用。"""
    state = {"player": _fighter()}

    result = use_class_action.func(action_id="action_surge", state=state, tool_call_id="call-test")

    assert "不在战斗中" in result.update["messages"][0].content
    assert result.update["player"]["action_available"] is False
    assert result.update["player"]["resources"]["action_surge_uses"] == 1


def test_action_surge_consumes_resource_and_restores_action_in_combat():
    """战斗中动作如潮应消耗资源并提供额外动作。"""
    fighter = _fighter()
    state = {
        "player": fighter,
        "combat": _combat_state_for(fighter),
    }

    result = use_class_action.func(action_id="action_surge", state=state, tool_call_id="call-test")

    updated = result.update["player"]
    assert updated["action_available"] is False
    assert updated["extra_action_available"] is True
    assert updated["resources"]["action_surge_uses"] == 0


def test_battle_master_lists_choose_maneuvers_action():
    """战斗大师拥有卓越战技后，应能看到选择战技动作。"""
    state = {"player": _battle_master()}

    result = use_class_action.func(action_id="", state=state, tool_call_id="call-test")

    content = result.update["messages"][0].content
    assert "choose_maneuvers" in content


def test_choose_maneuvers_records_valid_selection():
    """选择战技只写入 maneuvers 字段，不消耗卓越骰。"""
    battle_master = _battle_master()
    state = {"player": battle_master}

    result = use_class_action.func(
        action_id="choose_maneuvers",
        payload={"maneuvers": ["trip_attack", "precision_attack", "menacing_attack"]},
        state=state,
        tool_call_id="call-test",
    )

    updated = result.update["player"]
    assert updated["maneuvers"] == ["trip_attack", "precision_attack", "menacing_attack"]
    assert updated["resources"]["superiority_dice"] == 4
    assert "摔绊攻击" in result.update["messages"][0].content


def test_choose_maneuvers_rejects_duplicate_selection():
    """同一个战技不能重复选择。"""
    battle_master = _battle_master()
    state = {"player": battle_master}

    result = use_class_action.func(
        action_id="choose_maneuvers",
        payload={"maneuvers": ["trip_attack", "trip_attack"]},
        state=state,
        tool_call_id="call-test",
    )

    assert "不能重复选择" in result.update["messages"][0].content
    assert result.update["player"]["maneuvers"] == []


def test_choose_maneuvers_rejects_too_many_selection():
    """战斗大师 3 级只能选择 maneuvers_known_count 指定数量的战技。"""
    battle_master = _battle_master()
    state = {"player": battle_master}

    result = use_class_action.func(
        action_id="choose_maneuvers",
        payload={"maneuvers": ["trip_attack", "precision_attack", "menacing_attack", "pushing_attack"]},
        state=state,
        tool_call_id="call-test",
    )

    assert "最多只能选择 3 个战技" in result.update["messages"][0].content
    assert result.update["player"]["maneuvers"] == []


def test_choose_maneuvers_rejects_unknown_selection():
    """未注册的战技 ID 不应写入角色。"""
    battle_master = _battle_master()
    state = {"player": battle_master}

    result = use_class_action.func(
        action_id="choose_maneuvers",
        payload={"maneuvers": ["trip_attack", "unknown_maneuver"]},
        state=state,
        tool_call_id="call-test",
    )

    assert "未知战技" in result.update["messages"][0].content
    assert result.update["player"]["maneuvers"] == []


def test_choose_maneuvers_rejects_non_battle_master():
    """非战斗大师即使伪造字段，也不能选择战技。"""
    fighter = _fighter()
    fighter["class_features"].append("combat_superiority")
    fighter["maneuvers_known_count"] = 3
    fighter["maneuvers"] = []
    state = {"player": fighter}

    result = use_class_action.func(
        action_id="choose_maneuvers",
        payload={"maneuvers": ["trip_attack"]},
        state=state,
        tool_call_id="call-test",
    )

    assert "只有战斗大师范型" in result.update["messages"][0].content
    assert result.update["player"]["maneuvers"] == []


def test_trip_attack_consumes_superiority_die_and_damages_combat_target():
    """摔绊攻击第一版作为命中后手动结算，消耗卓越骰并追加伤害。"""
    battle_master = _battle_master()
    battle_master["maneuvers"] = ["trip_attack"]
    goblin = {"id": "goblin_1", "name": "Goblin", "hp": 9, "max_hp": 9}
    combat = _combat_state_for(battle_master)
    combat.participants["goblin_1"] = goblin
    state = {
        "player": battle_master,
        "combat": combat,
    }

    with patch("app.services.class_actions.battle_master.d20.roll") as roll:
        roll.return_value.total = 5
        roll.return_value.__str__ = lambda self: "1d8 (5)"
        result = use_class_action.func(
            action_id="trip_attack",
            target_id="goblin_1",
            payload={"attack_hit": True, "damage_type": "slashing"},
            state=state,
            tool_call_id="call-test",
        )

    updated_actor = result.update["player"]
    updated_target = result.update["combat"]["participants"]["goblin_1"]
    content = result.update["messages"][0].content
    assert updated_actor["resources"]["superiority_dice"] == 3
    assert updated_target["hp"] == 4
    assert result.update["hp_changes"][0]["old_hp"] == 9
    assert result.update["hp_changes"][0]["new_hp"] == 4
    assert "DC 13" in content
    assert "失败则倒地" in content


def test_trip_attack_rejects_when_not_selected():
    """战斗大师必须先在 maneuvers 中选择摔绊攻击。"""
    battle_master = _battle_master()
    goblin = {"id": "goblin_1", "name": "Goblin", "hp": 9, "max_hp": 9}
    combat = _combat_state_for(battle_master)
    combat.participants["goblin_1"] = goblin
    state = {
        "player": battle_master,
        "combat": combat,
    }

    result = use_class_action.func(
        action_id="trip_attack",
        target_id="goblin_1",
        payload={"attack_hit": True},
        state=state,
        tool_call_id="call-test",
    )

    assert "尚未选择摔绊攻击" in result.update["messages"][0].content
    assert result.update["player"]["resources"]["superiority_dice"] == 4
    assert result.update["combat"]["participants"]["goblin_1"]["hp"] == 9


def test_trip_attack_rejects_when_attack_did_not_hit():
    """摔绊攻击必须在命中后声明，未命中不消耗卓越骰。"""
    battle_master = _battle_master()
    battle_master["maneuvers"] = ["trip_attack"]
    goblin = {"id": "goblin_1", "name": "Goblin", "hp": 9, "max_hp": 9}
    combat = _combat_state_for(battle_master)
    combat.participants["goblin_1"] = goblin
    state = {
        "player": battle_master,
        "combat": combat,
    }

    result = use_class_action.func(
        action_id="trip_attack",
        target_id="goblin_1",
        payload={"attack_hit": False},
        state=state,
        tool_call_id="call-test",
    )

    assert "命中后使用" in result.update["messages"][0].content
    assert result.update["player"]["resources"]["superiority_dice"] == 4
    assert result.update["combat"]["participants"]["goblin_1"]["hp"] == 9


def test_trip_attack_rejects_when_superiority_dice_empty():
    """没有卓越骰时不能使用摔绊攻击。"""
    battle_master = _battle_master()
    battle_master["maneuvers"] = ["trip_attack"]
    battle_master["resources"]["superiority_dice"] = 0
    goblin = {"id": "goblin_1", "name": "Goblin", "hp": 9, "max_hp": 9}
    combat = _combat_state_for(battle_master)
    combat.participants["goblin_1"] = goblin
    state = {
        "player": battle_master,
        "combat": combat,
    }

    result = use_class_action.func(
        action_id="trip_attack",
        target_id="goblin_1",
        payload={"attack_hit": True},
        state=state,
        tool_call_id="call-test",
    )

    assert "卓越骰已用尽" in result.update["messages"][0].content
    assert result.update["player"]["resources"]["superiority_dice"] == 0
    assert result.update["combat"]["participants"]["goblin_1"]["hp"] == 9


def test_rally_grants_temp_hp_and_consumes_bonus_action():
    """鼓舞应消耗卓越骰和附赠动作，并为目标提供临时 HP。"""
    battle_master = _battle_master()
    battle_master["maneuvers"] = ["rally"]
    battle_master["modifiers"] = {"cha": 1}
    ally = {"id": "ally_1", "name": "Ally", "hp": 8, "max_hp": 10, "temp_hp": 0}
    combat = _combat_state_for(battle_master)
    combat.participants["ally_1"] = ally
    state = {
        "player": battle_master,
        "combat": combat,
    }

    with patch("app.services.class_actions.battle_master.d20.roll") as roll:
        roll.return_value.total = 5
        roll.return_value.__str__ = lambda self: "1d8 (5)"
        result = use_class_action.func(
            action_id="rally",
            target_id="ally_1",
            state=state,
            tool_call_id="call-test",
        )

    updated_actor = result.update["player"]
    updated_target = result.update["combat"]["participants"]["ally_1"]
    assert updated_actor["resources"]["superiority_dice"] == 3
    assert updated_actor["bonus_action_available"] is False
    assert updated_target["temp_hp"] == 6
    assert "鼓舞" in result.update["messages"][0].content


def test_rally_rejects_when_not_selected():
    """战斗大师必须先选择鼓舞才能使用。"""
    battle_master = _battle_master()
    ally = {"id": "ally_1", "name": "Ally", "hp": 8, "max_hp": 10, "temp_hp": 0}
    combat = _combat_state_for(battle_master)
    combat.participants["ally_1"] = ally
    state = {
        "player": battle_master,
        "combat": combat,
    }

    result = use_class_action.func(
        action_id="rally",
        target_id="ally_1",
        state=state,
        tool_call_id="call-test",
    )

    assert "尚未选择鼓舞" in result.update["messages"][0].content
    assert result.update["player"]["resources"]["superiority_dice"] == 4
    assert result.update["combat"]["participants"]["ally_1"]["temp_hp"] == 0


def test_arcane_recovery_lists_and_restores_spell_slot():
    """奥术回想作为主动职业动作暴露，并按恢复总环级结算法术位。"""
    wizard = {
        "id": "wizard_1",
        "name": "Wizard",
        "role_class": "法师",
        "level": 2,
        "class_features": ["arcane_recovery"],
        "resources": {"spell_slot_lv1": 1, "arcane_recovery_uses": 1},
        "resource_caps": {"spell_slot_lv1": 3, "arcane_recovery_uses": 1},
    }

    listed = use_class_action.func(action_id="", state={"player": wizard}, tool_call_id="call-list")
    assert "arcane_recovery" in listed.update["messages"][0].content

    result = use_class_action.func(
        action_id="arcane_recovery",
        payload={"restore_slots": {"spell_slot_lv1": 1}},
        state={"player": wizard},
        tool_call_id="call-use",
    )

    updated = result.update["player"]
    assert updated["resources"]["spell_slot_lv1"] == 2
    assert updated["resources"]["arcane_recovery_uses"] == 0
    assert "奥术回想" in result.update["messages"][0].content


def test_arcane_recovery_rejects_over_budget_request():
    """奥术回想不能恢复超过法师等级一半向上取整的总环级。"""
    wizard = {
        "id": "wizard_1",
        "name": "Wizard",
        "role_class": "法师",
        "level": 3,
        "class_features": ["arcane_recovery"],
        "resources": {"spell_slot_lv1": 0, "spell_slot_lv2": 0, "arcane_recovery_uses": 1},
        "resource_caps": {"spell_slot_lv1": 4, "spell_slot_lv2": 2, "arcane_recovery_uses": 1},
    }

    result = use_class_action.func(
        action_id="arcane_recovery",
        payload={"restore_slots": {"spell_slot_lv2": 2}},
        state={"player": wizard},
        tool_call_id="call-use",
    )

    assert "最多为 2" in result.update["messages"][0].content
    assert result.update["player"]["resources"]["spell_slot_lv2"] == 0
    assert result.update["player"]["resources"]["arcane_recovery_uses"] == 1
