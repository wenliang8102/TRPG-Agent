"""休息工具测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.tools.rest_tools import take_rest


def test_short_rest_restores_short_rest_resources_but_not_spell_slots():
    """短休只恢复短休资源，普通法术位必须等长休。"""
    player = {
        "id": "fighter",
        "name": "Fighter",
        "role_class": "战士",
        "level": 3,
        "hp": 18,
        "max_hp": 28,
        "modifiers": {"con": 2},
        "resources": {
            "second_wind_uses": 0,
            "action_surge_uses": 0,
            "superiority_dice": 1,
            "spell_slot_lv1": 0,
        },
        "resource_caps": {
            "second_wind_uses": 1,
            "action_surge_uses": 1,
            "superiority_dice": 4,
            "spell_slot_lv1": 2,
        },
        "hit_die": "1d10",
        "hit_dice_total": 3,
        "hit_dice_remaining": 3,
    }

    result = take_rest.func(
        rest_type="short",
        target_ids=["fighter"],
        hit_dice_to_spend=0,
        state={"player": player},
        tool_call_id="call-test",
    )

    resources = result.update["player"]["resources"]
    assert resources["second_wind_uses"] == 1
    assert resources["action_surge_uses"] == 1
    assert resources["superiority_dice"] == 4
    assert resources["spell_slot_lv1"] == 0


def test_rest_player_alias_resolves_to_real_player_id():
    """休息目标写 player 时应回写真实玩家 ID。"""
    player = {
        "id": "温良",
        "name": "温良",
        "role_class": "战士",
        "level": 1,
        "hp": 8,
        "max_hp": 12,
        "resources": {"second_wind_uses": 0},
        "resource_caps": {"second_wind_uses": 1},
    }

    result = take_rest.func(
        rest_type="short",
        target_ids=["player"],
        state={"player": player},
        tool_call_id="call-test",
    )

    assert result.update["player"]["id"] == "温良"
    assert result.update["player"]["resources"]["second_wind_uses"] == 1


def test_short_rest_spends_hit_dice_to_heal_target():
    """短休可消耗生命骰治疗，治疗量包含 CON 调整值。"""
    player = {
        "id": "wizard",
        "name": "Wizard",
        "role_class": "法师",
        "level": 2,
        "hp": 2,
        "max_hp": 12,
        "modifiers": {"con": 2},
        "resources": {},
        "resource_caps": {},
        "hit_die": "1d6",
        "hit_dice_total": 2,
        "hit_dice_remaining": 2,
    }

    with patch("app.services.tools.rest_tools.d20.roll") as roll:
        roll.return_value.total = 4
        roll.return_value.__str__ = lambda self: "1d6 (4)"
        result = take_rest.func(
            rest_type="short",
            target_ids=["wizard"],
            hit_dice_to_spend=1,
            state={"player": player},
            tool_call_id="call-test",
        )

    updated = result.update["player"]
    assert updated["hp"] == 8
    assert updated["hit_dice_remaining"] == 1
    assert result.update["hp_changes"][0]["old_hp"] == 2
    assert result.update["hp_changes"][0]["new_hp"] == 8


def test_long_rest_restores_resources_hp_and_half_spent_hit_dice():
    """长休恢复 HP、资源、生命骰，并清除临时状态与专注。"""
    player = {
        "id": "wizard",
        "name": "Wizard",
        "role_class": "法师",
        "level": 4,
        "hp": 3,
        "max_hp": 24,
        "temp_hp": 5,
        "modifiers": {"con": 2},
        "resources": {"spell_slot_lv1": 0, "spell_slot_lv2": 0},
        "resource_caps": {"spell_slot_lv1": 4, "spell_slot_lv2": 3},
        "hit_die": "1d6",
        "hit_dice_total": 4,
        "hit_dice_remaining": 1,
        "conditions": [{"id": "mage_armor"}, {"id": "frightened"}, {"id": "arcane_ward"}],
        "concentrating_on": "blur",
        "death_save_successes": 1,
        "death_save_failures": 2,
        "is_stable": True,
    }

    result = take_rest.func(
        rest_type="long",
        target_ids=["wizard"],
        state={"player": player},
        tool_call_id="call-test",
    )

    updated = result.update["player"]
    assert updated["hp"] == 24
    assert updated["temp_hp"] == 0
    assert updated["resources"]["spell_slot_lv1"] == 4
    assert updated["resources"]["spell_slot_lv2"] == 3
    assert updated["hit_dice_remaining"] == 3
    assert updated["conditions"] == []
    assert updated["concentrating_on"] is None
    assert updated["death_save_successes"] == 0
    assert updated["death_save_failures"] == 0
    assert updated["is_stable"] is False


def test_long_rest_restores_arcane_recovery_uses():
    """奥术回想每次长休周期恢复一次使用次数。"""
    player = {
        "id": "wizard",
        "name": "Wizard",
        "role_class": "法师",
        "level": 2,
        "hp": 12,
        "max_hp": 12,
        "resources": {"arcane_recovery_uses": 0},
        "resource_caps": {"arcane_recovery_uses": 1},
    }

    result = take_rest.func(
        rest_type="long",
        target_ids=["wizard"],
        state={"player": player},
        tool_call_id="call-test",
    )

    assert result.update["player"]["resources"]["arcane_recovery_uses"] == 1


def test_rest_targets_scene_ally_only_when_id_is_included():
    """友方是否参与休息由 target_ids 控制，不隐式全队恢复。"""
    player = {
        "id": "player",
        "name": "Player",
        "role_class": "战士",
        "level": 1,
        "hp": 8,
        "max_hp": 12,
        "resources": {"second_wind_uses": 0},
        "resource_caps": {"second_wind_uses": 1},
    }
    ally = {
        "id": "fighter_companion",
        "name": "Companion",
        "role_class": "战士",
        "level": 1,
        "hp": 7,
        "max_hp": 12,
        "resources": {"second_wind_uses": 0},
        "resource_caps": {"second_wind_uses": 1},
    }

    result = take_rest.func(
        rest_type="short",
        target_ids=["fighter_companion"],
        state={"player": player, "scene_units": {"fighter_companion": ally}},
        tool_call_id="call-test",
    )

    assert "player" not in result.update
    assert result.update["scene_units"]["fighter_companion"]["resources"]["second_wind_uses"] == 1


def test_rest_rejects_during_combat():
    """战斗中不能直接休息，避免绕过回合与资源消耗。"""
    result = take_rest.func(
        rest_type="short",
        target_ids=["player"],
        state={"combat": {"round": 1}, "player": {"id": "player", "name": "Player"}},
        tool_call_id="call-test",
    )

    assert "不能进行休息" in result.update["messages"][0].content
