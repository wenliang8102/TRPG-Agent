"""道具工具测试 — 三种基础药水与 2024 动作经济。"""

import copy
import sys
from pathlib import Path
from unittest.mock import patch

import d20

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.allies.profiles import get_ally_profile
from app.calculation.predefined_characters import PREDEFINED_CHARACTERS
from app.services.tools import get_tool_profile
from app.services.tools.character_tools import load_character_profile
from app.services.tools.item_tools import manage_inventory
from app.services.tools.rest_tools import take_rest
from app.services.tools._helpers import prepare_player_for_combat


def _invoke_tool(tool_func, *, tool_input: dict) -> object:
    """用 LangChain ToolCall 格式调用工具。"""
    return tool_func.invoke({
        "name": tool_func.name,
        "args": tool_input,
        "id": "item-test-call",
        "type": "tool_call",
    })


def _manage_inventory(tool_input: dict) -> object:
    """测试统一走当前模型可见的背包入口。"""
    return _invoke_tool(manage_inventory, tool_input=tool_input)


def _space_state(unit_positions: dict[str, tuple[int, int]]) -> dict:
    return {
        "active_map_id": "map_1",
        "maps": {"map_1": {"id": "map_1", "name": "道具测试地图", "width": 100, "height": 100, "grid_size": 5}},
        "placements": {
            unit_id: {"unit_id": unit_id, "map_id": "map_1", "position": {"x": x, "y": y}}
            for unit_id, (x, y) in unit_positions.items()
        },
    }


def test_default_player_and_fighter_companion_carry_healing_potions():
    """玩家预设和初始战士友方默认各有两瓶治疗药水。"""
    loaded = _invoke_tool(load_character_profile, tool_input={"role_class": "战士", "character_name": "温良"})
    player = loaded.update["player"]
    ally = get_ally_profile("fighter_companion")

    assert player["inventory"][0]["id"] == "potion_of_healing"
    assert player["inventory"][0]["quantity"] == 2
    assert ally["inventory"][0]["id"] == "potion_of_healing"
    assert ally["inventory"][0]["quantity"] == 2


def test_drinking_healing_potion_uses_bonus_action_in_combat():
    """2024 规则：自己喝药消耗附赠动作，不消耗动作。"""
    player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
    player.update({"name": "温良", "id": "温良", "hp": 4})
    prepare_player_for_combat(player)
    combat = {"round": 1, "participants": {}, "initiative_order": ["温良"], "current_actor_id": "温良"}

    with patch("app.services.tools.item_tools.d20.roll", return_value=d20.roll("6")):
        result = _invoke_tool(
            manage_inventory,
            tool_input={"action": "use", "item_id": "potion_of_healing", "actor_id": "player", "target_id": "player", "mode": "drink", "state": {"player": player, "combat": combat}},
        )

    updated = result.update["player"]
    assert updated["hp"] == 10
    assert updated["action_available"] is True
    assert updated["bonus_action_available"] is False
    assert updated["inventory"][0]["quantity"] == 1
    assert "温良 饮下 治疗药水" in result.update["messages"][0].content


def test_ally_drink_without_target_uses_actor_as_target():
    """队友自己喝药时即使省略 target_id，也不应默认治疗玩家。"""
    player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
    player.update({"name": "温良", "id": "温良", "hp": 4})
    prepare_player_for_combat(player)
    ally = get_ally_profile("fighter_companion")
    ally["hp"] = 5
    combat = {
        "round": 1,
        "participants": {"fighter_companion": ally},
        "initiative_order": ["fighter_companion", "温良"],
        "current_actor_id": "fighter_companion",
    }
    state = {
        "player": player,
        "combat": combat,
        "space": _space_state({"fighter_companion": (0, 0), "温良": (30, 0)}),
    }

    with patch("app.services.tools.item_tools.d20.roll", return_value=d20.roll("5")):
        result = _invoke_tool(
            manage_inventory,
            tool_input={"action": "use", "item_id": "potion_of_healing", "actor_id": "fighter_companion", "mode": "drink", "state": state},
        )

    updated_ally = result.update["combat"]["participants"]["fighter_companion"]
    assert updated_ally["hp"] == 10
    assert "player" not in result.update
    assert player["hp"] == 4
    assert updated_ally["bonus_action_available"] is False
    assert "格林 饮下 治疗药水" in result.update["messages"][0].content


def test_drink_rejects_targeting_other_unit_at_any_distance():
    """drink 不能被误用成远程给别人喝药；给别人必须 feed 或 throw。"""
    player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
    player.update({"name": "温良", "id": "温良", "hp": 4})
    prepare_player_for_combat(player)
    ally = get_ally_profile("fighter_companion")
    combat = {
        "round": 1,
        "participants": {"fighter_companion": ally},
        "initiative_order": ["fighter_companion", "温良"],
        "current_actor_id": "fighter_companion",
    }

    result = _invoke_tool(
        manage_inventory,
        tool_input={
            "action": "use",
            "item_id": "potion_of_healing",
            "actor_id": "fighter_companion",
            "target_id": "player",
            "mode": "drink",
            "state": {"player": player, "combat": combat, "space": _space_state({"fighter_companion": (0, 0), "温良": (30, 0)})},
        },
    )

    assert "drink 只能由使用者自己饮用" in result.update["messages"][0].content
    assert "player" not in result.update
    assert "combat" not in result.update


def test_use_item_player_alias_resolves_to_real_player_id_for_range():
    """喂药距离校验应使用真实玩家 ID，而不是字面 player。"""
    player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
    player.update({"name": "温良", "id": "温良"})
    prepare_player_for_combat(player)
    ally = get_ally_profile("fighter_companion")
    ally["hp"] = 5
    combat = {
        "round": 1,
        "participants": {"fighter_companion": ally},
        "initiative_order": ["温良", "fighter_companion"],
        "current_actor_id": "温良",
    }
    state = {
        "player": player,
        "combat": combat,
        "space": _space_state({"温良": (0, 0), "fighter_companion": (5, 0)}),
    }

    with patch("app.services.tools.item_tools.d20.roll", return_value=d20.roll("5")):
        result = _invoke_tool(
            manage_inventory,
            tool_input={"action": "use", "item_id": "potion_of_healing", "actor_id": "player", "target_id": "fighter_companion", "mode": "feed", "state": state},
        ).update

    assert result["player"]["id"] == "温良"
    assert result["player"]["action_available"] is False
    assert result["combat"]["participants"]["fighter_companion"]["hp"] == 10
    assert "温良 贴身给 格林 喂下 治疗药水" in result["messages"][0].content


def test_greater_healing_potion_restores_4d4_plus_4_hp():
    """强效治疗药水使用 5e 对应的 4d4+4 治疗公式。"""
    player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
    player.update({
        "name": "温良",
        "id": "温良",
        "hp": 4,
        "max_hp": 30,
        "inventory": [{"id": "potion_of_greater_healing", "name": "强效治疗药水", "type": "potion", "quantity": 1}],
    })

    with patch("app.services.tools.item_tools.d20.roll", return_value=d20.roll("12")) as roll_mock:
        result = _invoke_tool(
            manage_inventory,
            tool_input={"action": "use", "item_id": "potion_of_greater_healing", "state": {"player": player}},
        )

    roll_mock.assert_called_once_with("4d4+4")
    assert result.update["player"]["hp"] == 16
    assert result.update["player"]["inventory"] == []


def test_throwing_healing_potion_uses_action_and_has_range_limit():
    """投掷药水消耗动作，且目标必须在 20 尺内。"""
    player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
    player.update({"name": "温良", "id": "温良"})
    prepare_player_for_combat(player)
    ally = get_ally_profile("fighter_companion")
    ally["hp"] = 5
    combat = {
        "round": 1,
        "participants": {"fighter_companion": ally},
        "initiative_order": ["温良", "fighter_companion"],
        "current_actor_id": "温良",
    }
    state = {
        "player": player,
        "combat": combat,
        "space": _space_state({"温良": (0, 0), "fighter_companion": (30, 0)}),
    }

    blocked = _invoke_tool(
        manage_inventory,
        tool_input={"action": "use", "item_id": "potion_of_healing", "actor_id": "player", "target_id": "fighter_companion", "mode": "throw", "state": state},
    )
    assert "距离不足" in blocked.update["messages"][0].content

    state["space"] = _space_state({"温良": (0, 0), "fighter_companion": (20, 0)})
    with patch("app.services.tools.item_tools.d20.roll", return_value=d20.roll("5")):
        result = _invoke_tool(
            manage_inventory,
            tool_input={"action": "use", "item_id": "potion_of_healing", "actor_id": "player", "target_id": "fighter_companion", "mode": "throw", "state": state},
        )

    assert result.update["player"]["action_available"] is False
    assert result.update["player"]["bonus_action_available"] is True
    assert result.update["combat"]["participants"]["fighter_companion"]["hp"] == 10
    assert "温良 将 治疗药水 投掷给 格林" in result.update["messages"][0].content


def test_invisibility_potion_adds_breaking_invisible_condition():
    """隐身药水添加可被攻击或施法打断的隐形状态。"""
    player = copy.deepcopy(PREDEFINED_CHARACTERS["法师"])
    player.update({"name": "温良", "id": "温良"})

    result = _invoke_tool(
        manage_inventory,
        tool_input={"action": "use", "item_id": "potion_of_invisibility", "state": {"player": {**player, "inventory": [{"id": "potion_of_invisibility", "name": "隐身药水", "type": "potion", "quantity": 1}]}}},
    )

    condition = result.update["player"]["conditions"][0]
    assert condition["id"] == "invisible"
    assert condition["extra"]["break_on"] == ["attack", "spell"]
    assert result.update["player"]["inventory"] == []


def test_vitality_potion_clears_exhaustion_poison_and_disease():
    """活力药水清理力竭、毒素和疾病，并记录生命骰治疗最大化。"""
    player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
    player.update({
        "name": "温良",
        "id": "温良",
        "inventory": [{"id": "potion_of_vitality", "name": "活力药水", "type": "potion", "quantity": 1}],
        "conditions": [{"id": "exhausted"}, {"id": "poisoned"}, {"id": "diseased"}, {"id": "prone"}],
    })

    result = _invoke_tool(manage_inventory, tool_input={"action": "use", "item_id": "potion_of_vitality", "state": {"player": player}})

    remaining = [condition["id"] for condition in result.update["player"]["conditions"]]
    assert remaining == ["prone"]
    assert result.update["player"]["hit_dice_healing_maximized_until"] == "24h"


def test_vitality_potion_maximizes_short_rest_hit_die_healing():
    """活力药水后的 24 小时内，短休生命骰治疗按最大值计算。"""
    player = copy.deepcopy(PREDEFINED_CHARACTERS["战士"])
    player.update({
        "name": "温良",
        "id": "温良",
        "hp": 1,
        "hit_die": "1d10",
        "hit_dice_total": 1,
        "hit_dice_remaining": 1,
        "hit_dice_healing_maximized_until": "24h",
    })

    with patch("app.services.tools.rest_tools.d20.roll", return_value=d20.roll("1")):
        result = take_rest.func(rest_type="short", target_ids=["player"], hit_dice_to_spend=1, state={"player": player}, tool_call_id="rest-call")

    assert result.update["player"]["hp"] == 12
    assert "活力药水取最大值" in result.update["messages"][0].content


def test_buy_item_spends_gp_and_stacks_inventory():
    """购物工具只负责简单药水交易：扣 GP 并叠加同 ID 背包条目。"""
    player = {
        "name": "温良",
        "id": "温良",
        "coins": {"gp": 125},
        "inventory": [{"id": "potion_of_healing", "name": "治疗药水", "type": "potion", "quantity": 1}],
    }

    result = _invoke_tool(
        manage_inventory,
        tool_input={"action": "buy", "item_id": "potion_of_healing", "quantity": 2, "state": {"player": player}},
    )

    updated = result.update["player"]
    assert updated["coins"]["gp"] == 25
    assert updated["inventory"][0]["id"] == "potion_of_healing"
    assert updated["inventory"][0]["quantity"] == 3
    message = result.update["messages"][0]
    assert message.name == "manage_inventory"
    assert "[商店待售清单]" not in message.content
    assert "购物完成" in message.content


def test_buy_item_without_item_id_returns_catalog_without_purchase():
    """不传 item_id 时只查看价目表，不扣钱也不改背包。"""
    player = {"name": "温良", "id": "温良", "coins": {"gp": 125}, "inventory": []}

    result = _invoke_tool(manage_inventory, tool_input={"action": "list_shop", "state": {"player": player}})

    assert "player" not in result.update
    message = result.update["messages"][0]
    assert message.name == "manage_inventory"
    assert "[商店待售清单]" in message.content
    assert "potion_of_healing" in message.content
    assert "50 gp" in message.content
    assert "potion_of_greater_healing" in message.content
    assert "200 gp" in message.content
    assert player["coins"]["gp"] == 125
    assert player["inventory"] == []


def test_buy_greater_healing_potion_uses_uncommon_consumable_price():
    """强效治疗药水按 uncommon 消耗品折半后的 200 gp 定价。"""
    player = {"name": "温良", "id": "温良", "coins": {"gp": 250}, "inventory": []}

    result = _invoke_tool(
        manage_inventory,
        tool_input={"action": "buy", "item_id": "potion_of_greater_healing", "state": {"player": player}},
    )

    updated = result.update["player"]
    assert updated["coins"]["gp"] == 50
    assert updated["inventory"][0]["id"] == "potion_of_greater_healing"
    assert updated["inventory"][0]["price_gp"] == 200


def test_buy_item_rejects_insufficient_gp_without_mutating_player():
    """余额不足时只返回失败消息，不改金币或背包。"""
    player = {"name": "温良", "id": "温良", "coins": {"gp": 49}, "inventory": []}

    result = _invoke_tool(
        manage_inventory,
        tool_input={"action": "buy", "item_id": "potion_of_healing", "state": {"player": player}},
    )

    assert "GP 不足" in result.update["messages"][0].content
    assert "[商店待售清单]" not in result.update["messages"][0].content
    assert "player" not in result.update
    assert player["coins"]["gp"] == 49
    assert player["inventory"] == []


def test_manage_inventory_is_narrative_and_combat_executor_tool():
    """主 Agent 只在叙事阶段管理背包；战斗使用由执行器窄上下文处理。"""
    narrative_tool_names = {tool.name for tool in get_tool_profile("narrative")}
    combat_tool_names = {tool.name for tool in get_tool_profile("combat")}

    assert "manage_inventory" in narrative_tool_names
    assert "manage_inventory" not in combat_tool_names
    assert "buy_item" not in narrative_tool_names
    assert "use_item" not in narrative_tool_names


def test_add_item_records_story_pickup_without_spending_gp():
    """剧情拾取和赠予可以直接加入已定义消耗品，但不伪装成购物或节点奖励。"""
    player = {"name": "温良", "id": "温良", "coins": {"gp": 0}, "inventory": []}

    result = _manage_inventory({
        "action": "add",
        "item_id": "potion_of_healing",
        "quantity": 2,
        "reason": "药剂师临时赠予的旅途补给",
        "state": {"player": player},
    })

    updated = result.update["player"]
    assert updated["coins"]["gp"] == 0
    assert updated["inventory"][0]["id"] == "potion_of_healing"
    assert updated["inventory"][0]["quantity"] == 2
    assert "药剂师临时赠予" in result.update["messages"][0].content


def test_add_item_requires_reason_to_keep_node_rewards_separate():
    """没有剧情来源时拒绝 add，避免替代 claim_adventure_reward。"""
    player = {"name": "温良", "id": "温良", "inventory": []}

    result = _manage_inventory({
        "action": "add",
        "item_id": "potion_of_healing",
        "state": {"player": player},
    })

    assert "claim_adventure_reward" in result.update["messages"][0].content
    assert "player" not in result.update
