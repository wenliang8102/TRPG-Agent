"""空间工具测试 — 平面地图、单位落点、移动力和范围查询。"""

from pathlib import Path
import sys

from langgraph.types import Command

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.graph.state import AttackInfo, CombatantState, CombatState
from app.services.tool_service import (
    create_plane_map,
    manage_space,
    measure_distance,
    move_unit,
    place_unit,
    remove_unit,
    query_units_in_radius,
)


def _invoke_tool(tool_func, *, tool_input: dict) -> object:
    """用 ToolCall 格式调用 LangChain 工具，保持和现有测试一致。"""
    return tool_func.invoke({
        "name": tool_func.name,
        "args": tool_input,
        "id": "space-test-call",
        "type": "tool_call",
    })


def _make_unit(uid: str, side: str = "enemy") -> dict:
    """构造空间测试需要的最小战斗单位。"""
    return CombatantState(
        id=uid,
        name=uid,
        side=side,
        hp=10,
        max_hp=10,
        speed=30,
        movement_left=30,
        attacks=[AttackInfo(name="Strike", attack_bonus=2, damage_dice="1d6")],
    ).model_dump()


def test_create_map_place_units_and_measure_distance():
    result = _invoke_tool(
        create_plane_map,
        tool_input={"name": "酒馆一楼", "width": 80, "height": 60, "state": {}},
    )

    assert isinstance(result, Command)
    space = result.update["space"]
    map_id = space["active_map_id"]

    first = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "hero", "x": 10, "y": 10, "state": {"space": space}},
    )
    second = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "goblin", "x": 13, "y": 14, "state": {"space": first.update["space"]}},
    )
    measured = _invoke_tool(
        measure_distance,
        tool_input={"source_id": "hero", "target_id": "goblin", "state": {"space": second.update["space"]}},
    )

    assert second.update["space"]["placements"]["hero"]["map_id"] == map_id
    assert "5.0 尺" in measured.update["messages"][0].content


def test_place_unit_resolves_player_alias_without_creating_player_node():
    space = _invoke_tool(
        create_plane_map,
        tool_input={"name": "荒野小径", "width": 80, "height": 60, "state": {}},
    ).update["space"]
    player = _make_unit("player_预设-法师", side="player")
    player["name"] = "预设-法师"

    result = _invoke_tool(
        manage_space,
        tool_input={
            "action": "place_unit",
            "payload": {"unit_id": "PLAYER", "x": 15, "y": 20},
            "state": {"space": space, "player": player},
        },
    )

    placements = result.update["space"]["placements"]
    assert "PLAYER" not in placements
    assert placements["player_预设-法师"]["position"] == {"x": 15.0, "y": 20.0}


def test_place_unit_rejects_unknown_unit_when_units_are_known():
    space = _invoke_tool(
        create_plane_map,
        tool_input={"name": "荒野小径", "width": 80, "height": 60, "state": {}},
    ).update["space"]
    player = _make_unit("player_预设-法师", side="player")
    player["name"] = "预设-法师"

    result = _invoke_tool(
        manage_space,
        tool_input={
            "action": "place_unit",
            "payload": {"unit_id": "mystery_token", "x": 15, "y": 20},
            "state": {"space": space, "player": player},
        },
    )

    assert "找不到单位 'mystery_token'" in result.update["messages"][0].content
    assert "space" not in result.update


def test_move_unit_consumes_current_actor_movement_in_combat():
    space = _invoke_tool(
        create_plane_map,
        tool_input={"name": "林间空地", "width": 100, "height": 100, "state": {}},
    ).update["space"]
    space = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "goblin_1", "x": 0, "y": 0, "state": {"space": space}},
    ).update["space"]

    goblin = _make_unit("goblin_1")
    combat = CombatState(
        round=1,
        participants={"goblin_1": CombatantState(**goblin)},
        initiative_order=["goblin_1"],
        current_actor_id="goblin_1",
    )

    result = _invoke_tool(
        move_unit,
        tool_input={"unit_id": "goblin_1", "x": 6, "y": 8, "state": {"space": space, "combat": combat}},
    )

    assert result.update["combat"]["participants"]["goblin_1"]["movement_left"] == 20
    assert result.update["space"]["placements"]["goblin_1"]["position"] == {"x": 6.0, "y": 8.0}


def test_move_unit_rejects_wrong_turn_and_excess_distance():
    space = _invoke_tool(
        create_plane_map,
        tool_input={"name": "石桥", "width": 100, "height": 100, "state": {}},
    ).update["space"]
    space = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "goblin_1", "x": 0, "y": 0, "state": {"space": space}},
    ).update["space"]

    goblin = _make_unit("goblin_1")
    orc = _make_unit("orc_1")
    combat = CombatState(
        round=1,
        participants={
            "goblin_1": CombatantState(**goblin),
            "orc_1": CombatantState(**orc),
        },
        initiative_order=["orc_1", "goblin_1"],
        current_actor_id="orc_1",
    )

    wrong_turn = _invoke_tool(
        move_unit,
        tool_input={"unit_id": "goblin_1", "x": 5, "y": 0, "state": {"space": space, "combat": combat}},
    )
    assert "当前不是该单位的回合" in wrong_turn.update["messages"][0].content

    combat.current_actor_id = "goblin_1"
    too_far = _invoke_tool(
        move_unit,
        tool_input={"unit_id": "goblin_1", "x": 60, "y": 0, "state": {"space": space, "combat": combat}},
    )
    assert "超过剩余移动力" in too_far.update["messages"][0].content


def test_manage_space_approach_unit_moves_to_attack_reach():
    space = _invoke_tool(
        create_plane_map,
        tool_input={"name": "石桥", "width": 100, "height": 100, "state": {}},
    ).update["space"]
    space = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "goblin_1", "x": 20, "y": 20, "state": {"space": space}},
    ).update["space"]
    space = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "player_hero", "x": 10, "y": 20, "state": {"space": space}},
    ).update["space"]

    goblin = _make_unit("goblin_1")
    combat = CombatState(
        round=1,
        participants={"goblin_1": CombatantState(**goblin)},
        initiative_order=["goblin_1", "player_hero"],
        current_actor_id="goblin_1",
    )

    result = _invoke_tool(
        manage_space,
        tool_input={
            "action": "approach_unit",
            "payload": {"unit_id": "goblin_1", "target_id": "player_hero", "attack_name": "Strike"},
            "state": {"space": space, "combat": combat},
        },
    )

    assert result.update["space"]["placements"]["goblin_1"]["position"] == {"x": 15.0, "y": 20.0}
    assert result.update["combat"]["participants"]["goblin_1"]["movement_left"] == 25
    assert "已进入目标距离" in result.update["messages"][0].content


def test_manage_space_approach_unit_uses_remaining_movement_when_target_too_far():
    space = _invoke_tool(
        create_plane_map,
        tool_input={"name": "旷野", "width": 200, "height": 100, "state": {}},
    ).update["space"]
    space = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "goblin_1", "x": 70, "y": 20, "state": {"space": space}},
    ).update["space"]
    space = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "player_hero", "x": 10, "y": 20, "state": {"space": space}},
    ).update["space"]

    goblin = _make_unit("goblin_1")
    goblin["movement_left"] = 30
    combat = CombatState(
        round=1,
        participants={"goblin_1": CombatantState(**goblin)},
        initiative_order=["goblin_1", "player_hero"],
        current_actor_id="goblin_1",
    )

    result = _invoke_tool(
        manage_space,
        tool_input={
            "action": "approach_unit",
            "payload": {"unit_id": "goblin_1", "target_id": "player_hero", "desired_distance": 5},
            "state": {"space": space, "combat": combat},
        },
    )

    assert result.update["space"]["placements"]["goblin_1"]["position"] == {"x": 40.0, "y": 20.0}
    assert result.update["combat"]["participants"]["goblin_1"]["movement_left"] == 0
    assert "尚未进入目标距离" in result.update["messages"][0].content


def test_query_units_in_radius_filters_by_map_and_distance():
    space = _invoke_tool(
        create_plane_map,
        tool_input={"name": "大厅", "width": 100, "height": 100, "state": {}},
    ).update["space"]
    space = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "near", "x": 5, "y": 0, "state": {"space": space}},
    ).update["space"]
    space = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "far", "x": 30, "y": 0, "state": {"space": space}},
    ).update["space"]

    result = _invoke_tool(
        query_units_in_radius,
        tool_input={"x": 0, "y": 0, "radius": 10, "state": {"space": space}},
    )

    content = result.update["messages"][0].content
    assert "near" in content
    assert "far" not in content


def test_manage_space_help_returns_skill_instructions():
    result = _invoke_tool(
        manage_space,
        tool_input={"action": "help"},
    )

    content = result.update["messages"][0].content
    assert "平面空间管理技能" in content
    assert "manage_space" in content
    assert 'action="move_unit"' in content


def test_manage_space_create_place_and_measure_distance():
    result = _invoke_tool(
        manage_space,
        tool_input={
            "action": "create_map",
            "payload": {"name": "废弃神殿", "width": 80, "height": 60},
            "state": {},
        },
    )
    space = result.update["space"]

    first = _invoke_tool(
        manage_space,
        tool_input={
            "action": "place_unit",
            "payload": {"unit_id": "hero", "x": 10, "y": 10},
            "state": {"space": space},
        },
    )
    second = _invoke_tool(
        manage_space,
        tool_input={
            "action": "place_unit",
            "payload": {"unit_id": "goblin", "x": 16, "y": 18},
            "state": {"space": first.update["space"]},
        },
    )
    measured = _invoke_tool(
        manage_space,
        tool_input={
            "action": "measure_distance",
            "payload": {"source_id": "hero", "target_id": "goblin"},
            "state": {"space": second.update["space"]},
        },
    )

    assert "10.0 尺" in measured.update["messages"][0].content


def test_manage_space_rejects_unknown_payload_keys():
    result = _invoke_tool(
        manage_space,
        tool_input={
            "action": "create_map",
            "payload": {"name": "废弃神殿", "width": 80, "height": 60, "grid": 10},
            "state": {},
        },
    )

    assert "不支持 payload 字段 grid" in result.update["messages"][0].content
    assert "space" not in result.update


def test_remove_unit_deletes_placement_from_space():
    space = _invoke_tool(
        create_plane_map,
        tool_input={"name": "密室", "width": 40, "height": 40, "state": {}} ,
    ).update["space"]
    space = _invoke_tool(
        place_unit,
        tool_input={"unit_id": "corpse", "x": 10, "y": 10, "state": {"space": space}},
    ).update["space"]

    result = _invoke_tool(
        remove_unit,
        tool_input={"unit_id": "corpse", "state": {"space": space}},
    )

    assert "corpse" not in result.update["space"]["placements"]
    assert "已将以下单位从空间中移除" in result.update["messages"][0].content
