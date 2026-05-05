"""平面空间工具 — 地图、落点、移动和测距。"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.graph.state import PlaneMapState, Point2D, SpaceState, UnitPlacementState
from app.services.skills import load_skill_content
from app.services.tools._helpers import apply_condition_movement_cost, get_combatant, get_condition_movement_block_reason
from app.space.geometry import (
    build_space_state,
    distance_between,
    distance_to_point,
    point_in_map,
    units_in_radius,
)


_SPACE_PAYLOAD_KEYS = {
    "create_map": frozenset({"name", "width", "height", "grid_size", "description", "activate"}),
    "switch_map": frozenset({"map_id"}),
    "place_unit": frozenset({"unit_id", "x", "y", "map_id", "facing_deg", "footprint_radius", "reason"}),
    "move_unit": frozenset({"unit_id", "x", "y"}),
    "approach_unit": frozenset({"unit_id", "target_id", "desired_distance", "attack_name"}),
    "remove_unit": frozenset({"unit_id", "unit_ids"}),
    "measure_distance": frozenset({"source_id", "target_id"}),
    "query_radius": frozenset({"x", "y", "radius", "map_id"}),
}


def _invalid_payload_message(action: str, payload: dict) -> str | None:
    """合并技能入口必须拒绝错字段，否则默认值会掩盖模型参数错误。"""
    allowed = _SPACE_PAYLOAD_KEYS[action]
    invalid = sorted(set(payload) - allowed)
    if invalid:
        return f"空间操作参数无效：action={action} 不支持 payload 字段 {', '.join(invalid)}。本次未执行；使用 action=\"help\" 查看完整说明。"
    return None


def _space_to_update(space: SpaceState) -> dict:
    """把 SpaceState 转成 LangGraph 可持久化的普通 dict。"""
    return space.model_dump()


def _active_map(space: SpaceState, map_id: str | None = None) -> PlaneMapState:
    """解析当前操作地图，所有空间工具共享同一套 active_map 语义。"""
    resolved_map_id = map_id or space.active_map_id
    return space.maps[resolved_map_id]


def _unit_label(unit_id: str, state: dict | None) -> str:
    """优先用现有单位状态里的名字，让空间日志更贴近战报。"""
    if not state:
        return unit_id

    player = state.get("player")
    player_dict = player.model_dump() if hasattr(player, "model_dump") else dict(player) if player else None
    if player_dict and unit_id == player_dict.get("id"):
        return player_dict.get("name", unit_id)

    combat = state.get("combat")
    combat_dict = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat) if combat else None
    if combat_dict:
        combatant = get_combatant(combat_dict, player_dict, unit_id)
        if combatant:
            return combatant.get("name", unit_id)

    scene_units = state.get("scene_units") or {}
    if hasattr(scene_units, "items") and unit_id in scene_units:
        unit = scene_units[unit_id]
        unit_dict = unit.model_dump() if hasattr(unit, "model_dump") else dict(unit)
        return unit_dict.get("name", unit_id)

    return unit_id


def _unit_identity_index(state: dict | None) -> dict[str, str]:
    """把玩家/怪物的常见称呼收束到真实 unit_id，避免模型用显示名创建孤儿落点。"""
    if not state:
        return {}

    index: dict[str, str] = {}

    def _add(unit: dict | None) -> None:
        if not unit or not unit.get("id"):
            return
        unit_id = str(unit["id"])
        aliases = {unit_id, unit_id.lower()}
        if unit.get("name"):
            name = str(unit["name"])
            aliases.update({name, name.lower()})
        if unit.get("side") == "player":
            aliases.update({"player", "PLAYER", "玩家", "当前玩家"})
        for alias in aliases:
            index[alias] = unit_id

    player = state.get("player")
    player_dict = player.model_dump() if hasattr(player, "model_dump") else dict(player) if player else None
    _add(player_dict)

    combat = state.get("combat")
    combat_dict = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat) if combat else None
    if combat_dict:
        for unit in combat_dict.get("participants", {}).values():
            _add(unit.model_dump() if hasattr(unit, "model_dump") else dict(unit))

    scene_units = state.get("scene_units") or {}
    if hasattr(scene_units, "items"):
        for unit in scene_units.values():
            _add(unit.model_dump() if hasattr(unit, "model_dump") else dict(unit))

    return index


def _resolve_unit_id(unit_id: str, state: dict | None) -> str:
    """空间工具入口只接受真实单位 ID；常见别名在这里统一翻译。"""
    index = _unit_identity_index(state)
    return index.get(unit_id) or index.get(unit_id.lower()) or unit_id


def _unknown_unit_command(unit_id: str, state: dict | None, tool_call_id: str | None) -> Command | None:
    """拒绝把未知显示名落到地图上，防止出现 PLAYER 这类无业务归属的节点。"""
    index = _unit_identity_index(state)
    if not index:
        return None
    if unit_id in index or unit_id.lower() in index:
        return None

    if build_space_state(state.get("space") if state else None).placements.get(unit_id):
        return None

    available = ", ".join(sorted(set(index.values()))) or "无"
    return Command(update={"messages": [ToolMessage(
        content=f"找不到单位 '{unit_id}'，本次未更新地图。可用单位 ID: {available}。",
        tool_call_id=tool_call_id,
    )]})


def _combat_actor(state: dict | None, unit_id: str) -> tuple[dict | None, dict | None, dict | None]:
    """获取战斗移动所需的 combat/player/actor 三元组。"""
    if not state:
        return None, None, None

    combat_raw = state.get("combat")
    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw) if combat_raw else None

    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    actor = get_combatant(combat_dict, player_dict, unit_id) if combat_dict else None
    return combat_dict, player_dict, actor


def _desired_distance_for_actor(actor: dict | None, desired_distance: float | None, attack_name: str | None) -> float:
    """战斗靠近优先使用攻击自身射程，避免模型反复手算几格距离。"""
    if desired_distance is not None:
        return desired_distance
    if not actor:
        return 5

    attacks = actor.get("attacks", [])
    attack = None
    if attack_name:
        attack = next((item for item in attacks if item.get("name", "").lower() == attack_name.lower()), None)
    elif attacks:
        attack = attacks[0]
    if not attack:
        return 5

    return float(attack.get("normal_range_feet") or attack.get("reach_feet", 5))


def _facing_degrees(source: Point2D, target: Point2D) -> float:
    """把二维向量转为地图朝向角度，供后续锥形法术和前端小地图使用。"""
    import math

    return math.degrees(math.atan2(target.y - source.y, target.x - source.x))


def _remove_unit_from_space(space: SpaceState, unit_ids: list[str]) -> list[str]:
    """直接从空间落点里移除单位；死亡清理和撤离都应走这里，而不是挪到角落。"""
    removed: list[str] = []
    for unit_id in unit_ids:
        if unit_id in space.placements:
            del space.placements[unit_id]
            removed.append(unit_id)
    return removed


def _create_plane_map_command(
    name: str,
    width: float,
    height: float,
    grid_size: float = 5,
    description: str = "",
    activate: bool = True,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """创建地图的共享实现，保证新旧工具入口行为一致。"""
    space = build_space_state(state.get("space") if state else None)
    map_id = f"map_{uuid4().hex[:8]}"
    plane_map = PlaneMapState(
        id=map_id,
        name=name,
        width=width,
        height=height,
        grid_size=grid_size,
        description=description,
    )
    space.maps[map_id] = plane_map
    if activate or not space.active_map_id:
        space.active_map_id = map_id

    status = "并设为当前地图" if space.active_map_id == map_id else "已创建"
    return Command(update={
        "space": _space_to_update(space),
        "messages": [ToolMessage(
            content=f"已创建平面地图 {name} [ID:{map_id}]，尺寸 {width:g}x{height:g} 尺，网格 {grid_size:g} 尺，{status}。",
            tool_call_id=tool_call_id,
        )],
    })


def _switch_plane_map_command(
    map_id: str,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """切换地图的共享实现，旧工具只作为兼容入口保留。"""
    space = build_space_state(state.get("space") if state else None)
    if map_id not in space.maps:
        return Command(update={"messages": [ToolMessage(content=f"找不到地图 '{map_id}'。", tool_call_id=tool_call_id)]})

    space.active_map_id = map_id
    plane_map = space.maps[map_id]
    return Command(update={
        "space": _space_to_update(space),
        "messages": [ToolMessage(content=f"当前地图已切换为 {plane_map.name} [ID:{map_id}]。", tool_call_id=tool_call_id)],
    })


def _place_unit_command(
    unit_id: str,
    x: float,
    y: float,
    map_id: str | None = None,
    facing_deg: float = 0,
    footprint_radius: float = 2.5,
    reason: str = "",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """剧情摆放的共享实现，和战斗移动保持明确边界。"""
    raw_unit_id = unit_id
    unit_id = _resolve_unit_id(unit_id, state)
    if unknown := _unknown_unit_command(raw_unit_id, state, tool_call_id):
        return unknown

    space = build_space_state(state.get("space") if state else None)
    plane_map = _active_map(space, map_id)
    position = Point2D(x=x, y=y)
    if not point_in_map(plane_map, position):
        return Command(update={"messages": [ToolMessage(
            content=f"坐标 ({x:g}, {y:g}) 超出地图 {plane_map.name} 的边界。",
            tool_call_id=tool_call_id,
        )]})

    space.placements[unit_id] = UnitPlacementState(
        unit_id=unit_id,
        map_id=plane_map.id,
        position=position,
        facing_deg=facing_deg,
        footprint_radius=footprint_radius,
    )

    label = _unit_label(unit_id, state)
    suffix = f"（{reason}）" if reason else ""
    return Command(update={
        "space": _space_to_update(space),
        "messages": [ToolMessage(
            content=f"{label} [ID:{unit_id}] 已位于 {plane_map.name} ({x:g}, {y:g})。{suffix}",
            tool_call_id=tool_call_id,
        )],
    })


def _move_unit_command(
    unit_id: str,
    x: float,
    y: float,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """战斗移动的共享实现，集中维护移动力扣减规则。"""
    raw_unit_id = unit_id
    unit_id = _resolve_unit_id(unit_id, state)
    if unknown := _unknown_unit_command(raw_unit_id, state, tool_call_id):
        return unknown

    space = build_space_state(state.get("space") if state else None)
    placement = space.placements[unit_id]
    plane_map = _active_map(space, placement.map_id)
    destination = Point2D(x=x, y=y)
    if not point_in_map(plane_map, destination):
        return Command(update={"messages": [ToolMessage(
            content=f"坐标 ({x:g}, {y:g}) 超出地图 {plane_map.name} 的边界。",
            tool_call_id=tool_call_id,
        )]})

    origin = placement.position
    movement_cost = distance_between(placement, UnitPlacementState(unit_id=unit_id, map_id=placement.map_id, position=destination))

    combat_dict, player_dict, actor = _combat_actor(state, unit_id)
    if combat_dict:
        if combat_dict.get("current_actor_id") != unit_id:
            return Command(update={"messages": [ToolMessage(content="当前不是该单位的回合，不能移动。", tool_call_id=tool_call_id)]})
        if not actor:
            return Command(update={"messages": [ToolMessage(content=f"找不到参战单位 '{unit_id}'。", tool_call_id=tool_call_id)]})
        if block_reason := get_condition_movement_block_reason(actor, state, destination):
            return Command(update={"messages": [ToolMessage(content=block_reason, tool_call_id=tool_call_id)]})
        movement_cost = apply_condition_movement_cost(actor, movement_cost)
        movement_left = actor.get("movement_left", actor.get("speed", 30))
        if movement_cost > movement_left:
            return Command(update={"messages": [ToolMessage(
                content=f"移动距离 {movement_cost:.1f} 尺，超过剩余移动力 {movement_left:g} 尺。",
                tool_call_id=tool_call_id,
            )]})
        actor["movement_left"] = round(movement_left - movement_cost, 2)

    placement.position = destination
    space.placements[unit_id] = placement

    update: dict = {"space": _space_to_update(space)}
    if combat_dict:
        update["combat"] = combat_dict
    if player_dict:
        update["player"] = player_dict

    label = _unit_label(unit_id, state)
    remaining = ""
    if actor:
        remaining = f"，剩余移动力 {actor.get('movement_left', 0):g} 尺"
    update["messages"] = [ToolMessage(
        content=f"{label} 从 ({origin.x:g}, {origin.y:g}) 移动到 ({x:g}, {y:g})，移动 {movement_cost:.1f} 尺{remaining}。",
        tool_call_id=tool_call_id,
    )]
    return Command(update=update)


def _approach_unit_command(
    unit_id: str,
    target_id: str,
    desired_distance: float | None = None,
    attack_name: str | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """沿直线靠近目标到指定距离；当前没有障碍物系统，先保持极简路径语义。"""
    raw_unit_id = unit_id
    raw_target_id = target_id
    unit_id = _resolve_unit_id(unit_id, state)
    target_id = _resolve_unit_id(target_id, state)
    if unknown := _unknown_unit_command(raw_unit_id, state, tool_call_id):
        return unknown
    if unknown := _unknown_unit_command(raw_target_id, state, tool_call_id):
        return unknown

    space = build_space_state(state.get("space") if state else None)
    source = space.placements[unit_id]
    target = space.placements[target_id]
    if source.map_id != target.map_id:
        return Command(update={"messages": [ToolMessage(content="两个单位不在同一张地图上，无法靠近。", tool_call_id=tool_call_id)]})

    combat_dict, player_dict, actor = _combat_actor(state, unit_id)
    if combat_dict:
        if combat_dict.get("current_actor_id") != unit_id:
            return Command(update={"messages": [ToolMessage(content="当前不是该单位的回合，不能移动。", tool_call_id=tool_call_id)]})
        if not actor:
            return Command(update={"messages": [ToolMessage(content=f"找不到参战单位 '{unit_id}'。", tool_call_id=tool_call_id)]})

    goal_distance = max(0, _desired_distance_for_actor(actor, desired_distance, attack_name))
    current_distance = distance_between(source, target)
    source.position = source.position.model_copy()
    source.facing_deg = _facing_degrees(source.position, target.position)

    if current_distance <= goal_distance:
        space.placements[unit_id] = source
        update: dict = {"space": _space_to_update(space)}
        if combat_dict:
            update["combat"] = combat_dict
        if player_dict:
            update["player"] = player_dict
        update["messages"] = [ToolMessage(
            content=f"{_unit_label(unit_id, state)} 已在 {_unit_label(target_id, state)} {goal_distance:g} 尺范围内，当前距离 {current_distance:.1f} 尺。",
            tool_call_id=tool_call_id,
        )]
        return Command(update=update)

    movement_needed = current_distance - goal_distance
    movement_left = movement_needed
    if actor:
        movement_left = actor.get("movement_left", actor.get("speed", 30))
    raw_movement_cost = min(movement_needed, movement_left)
    movement_cost = apply_condition_movement_cost(actor, raw_movement_cost) if actor else raw_movement_cost
    if movement_cost <= 0:
        return Command(update={"messages": [ToolMessage(
            content=f"{_unit_label(unit_id, state)} 没有剩余移动力，仍距离 {_unit_label(target_id, state)} {current_distance:.1f} 尺。",
            tool_call_id=tool_call_id,
        )]})
    if actor and movement_cost > movement_left:
        raw_movement_cost = movement_left / movement_cost * raw_movement_cost
        movement_cost = movement_left

    ratio = raw_movement_cost / current_distance
    destination = Point2D(
        x=source.position.x + (target.position.x - source.position.x) * ratio,
        y=source.position.y + (target.position.y - source.position.y) * ratio,
    )
    if actor and (block_reason := get_condition_movement_block_reason(actor, state, destination)):
        return Command(update={"messages": [ToolMessage(content=block_reason, tool_call_id=tool_call_id)]})
    plane_map = _active_map(space, source.map_id)
    if not point_in_map(plane_map, destination):
        return Command(update={"messages": [ToolMessage(
            content=f"自动靠近目标点 ({destination.x:g}, {destination.y:g}) 超出地图 {plane_map.name} 的边界。",
            tool_call_id=tool_call_id,
        )]})

    origin = source.position
    source.position = destination
    space.placements[unit_id] = source
    remaining_distance = distance_to_point(source, target.position)

    if actor:
        actor["movement_left"] = round(movement_left - movement_cost, 2)

    update = {"space": _space_to_update(space)}
    if combat_dict:
        update["combat"] = combat_dict
    if player_dict:
        update["player"] = player_dict

    remaining_movement = f"，剩余移动力 {actor.get('movement_left', 0):g} 尺" if actor else ""
    status = "已进入目标距离" if remaining_distance <= goal_distance else "尚未进入目标距离"
    update["messages"] = [ToolMessage(
        content=(
            f"{_unit_label(unit_id, state)} 朝 {_unit_label(target_id, state)} 靠近，"
            f"从 ({origin.x:g}, {origin.y:g}) 移动到 ({destination.x:g}, {destination.y:g})，"
            f"移动 {movement_cost:.1f} 尺，当前距离 {remaining_distance:.1f} 尺，目标距离 {goal_distance:g} 尺，{status}{remaining_movement}。"
        ),
        tool_call_id=tool_call_id,
    )]
    return Command(update=update)


def _remove_unit_command(
    unit_id: str | None = None,
    unit_ids: list[str] | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """把单位从当前空间中彻底移除；这是死亡结算、撤离和场景回收的正式出口。"""
    space = build_space_state(state.get("space") if state else None)
    targets = [_resolve_unit_id(unit_id, state)] if unit_id else [_resolve_unit_id(uid, state) for uid in list(unit_ids or [])]
    removed = _remove_unit_from_space(space, [uid for uid in targets if uid])

    if not removed:
        return Command(update={"messages": [ToolMessage(
            content="没有找到可移除的空间单位。",
            tool_call_id=tool_call_id,
        )]})

    names = ", ".join(removed)
    return Command(update={
        "space": _space_to_update(space),
        "messages": [ToolMessage(
            content=f"已将以下单位从空间中移除: {names}。",
            tool_call_id=tool_call_id,
        )],
    })


def _measure_distance_command(
    source_id: str,
    target_id: str,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """测距的共享实现，避免技能入口和旧工具结果漂移。"""
    raw_source_id = source_id
    raw_target_id = target_id
    source_id = _resolve_unit_id(source_id, state)
    target_id = _resolve_unit_id(target_id, state)
    if unknown := _unknown_unit_command(raw_source_id, state, tool_call_id):
        return unknown
    if unknown := _unknown_unit_command(raw_target_id, state, tool_call_id):
        return unknown

    space = build_space_state(state.get("space") if state else None)
    source = space.placements[source_id]
    target = space.placements[target_id]
    if source.map_id != target.map_id:
        return Command(update={"messages": [ToolMessage(content="两个单位不在同一张地图上，无法测距。", tool_call_id=tool_call_id)]})

    distance = distance_between(source, target)
    source_label = _unit_label(source_id, state)
    target_label = _unit_label(target_id, state)
    return Command(update={"messages": [ToolMessage(
        content=f"{source_label} 与 {target_label} 的平面距离为 {distance:.1f} 尺。",
        tool_call_id=tool_call_id,
    )]})


def _query_units_in_radius_command(
    x: float,
    y: float,
    radius: float,
    map_id: str | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """范围查询的共享实现，供 AoE 和光环判断复用。"""
    space = build_space_state(state.get("space") if state else None)
    plane_map = _active_map(space, map_id)
    origin = Point2D(x=x, y=y)
    if not point_in_map(plane_map, origin):
        return Command(update={"messages": [ToolMessage(
            content=f"圆心 ({x:g}, {y:g}) 超出地图 {plane_map.name} 的边界。",
            tool_call_id=tool_call_id,
        )]})

    matches = units_in_radius(space.placements, map_id=plane_map.id, origin=origin, radius=radius)
    if not matches:
        content = f"{plane_map.name} ({x:g}, {y:g}) 半径 {radius:g} 尺内没有已放置单位。"
    else:
        desc = ", ".join(f"{_unit_label(uid, state)}[ID:{uid}, {distance:.1f}尺]" for uid, distance in matches)
        content = f"{plane_map.name} ({x:g}, {y:g}) 半径 {radius:g} 尺内单位: {desc}"

    return Command(update={"messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]})


@tool
def manage_space(
    action: Literal[
        "help",
        "create_map",
        "switch_map",
        "place_unit",
        "move_unit",
        "approach_unit",
        "remove_unit",
        "measure_distance",
        "query_radius",
    ],
    payload: dict | None = None,
    reason: str = "",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """平面空间管理技能。用于地图、单位落点、移动、测距和范围查询。
    如不确定 action 或 payload 写法，先用 action="help" 查看完整技能说明。

    Args:
        action: 空间操作动作；用 "help" 获取完整说明。
        payload: 对应动作的参数字典。
        reason: 本次空间变化的叙事原因。
    """
    payload = payload or {}

    # 空间工具的详细手册按需返回，避免模型常驻吞下整组参数说明。
    if action == "help":
        return Command(update={"messages": [
            ToolMessage(content=load_skill_content("space_management"), tool_call_id=tool_call_id)
        ]})

    if invalid_message := _invalid_payload_message(action, payload):
        return Command(update={"messages": [ToolMessage(content=invalid_message, tool_call_id=tool_call_id)]})

    # 同类空间能力收口到一个模型可见入口，内部仍复用原生 LangChain tool/Command 形态。
    if action == "create_map":
        return _create_plane_map_command(
            name=str(payload["name"]),
            width=float(payload["width"]),
            height=float(payload["height"]),
            grid_size=float(payload.get("grid_size", 5)),
            description=str(payload.get("description", "")),
            activate=bool(payload.get("activate", True)),
            state=state,
            tool_call_id=tool_call_id,
        )
    if action == "switch_map":
        return _switch_plane_map_command(str(payload["map_id"]), state, tool_call_id)
    if action == "place_unit":
        return _place_unit_command(
            unit_id=str(payload["unit_id"]),
            x=float(payload["x"]),
            y=float(payload["y"]),
            map_id=payload.get("map_id"),
            facing_deg=float(payload.get("facing_deg", 0)),
            footprint_radius=float(payload.get("footprint_radius", 2.5)),
            reason=str(payload.get("reason", reason)),
            state=state,
            tool_call_id=tool_call_id,
        )
    if action == "move_unit":
        return _move_unit_command(
            unit_id=str(payload["unit_id"]),
            x=float(payload["x"]),
            y=float(payload["y"]),
            state=state,
            tool_call_id=tool_call_id,
        )
    if action == "approach_unit":
        return _approach_unit_command(
            unit_id=str(payload["unit_id"]),
            target_id=str(payload["target_id"]),
            desired_distance=float(payload["desired_distance"]) if "desired_distance" in payload else None,
            attack_name=str(payload["attack_name"]) if "attack_name" in payload else None,
            state=state,
            tool_call_id=tool_call_id,
        )
    if action == "remove_unit":
        unit_ids = payload.get("unit_ids")
        if unit_ids is not None:
            return _remove_unit_command(
                unit_ids=[str(uid) for uid in unit_ids],
                state=state,
                tool_call_id=tool_call_id,
            )
        return _remove_unit_command(
            unit_id=str(payload["unit_id"]),
            state=state,
            tool_call_id=tool_call_id,
        )
    if action == "measure_distance":
        return _measure_distance_command(
            source_id=str(payload["source_id"]),
            target_id=str(payload["target_id"]),
            state=state,
            tool_call_id=tool_call_id,
        )
    if action == "query_radius":
        return _query_units_in_radius_command(
            x=float(payload["x"]),
            y=float(payload["y"]),
            radius=float(payload["radius"]),
            map_id=payload.get("map_id"),
            state=state,
            tool_call_id=tool_call_id,
        )

    raise ValueError(f"Unknown space action: {action}")


@tool
def create_plane_map(
    name: str,
    width: float,
    height: float,
    grid_size: float = 5,
    description: str = "",
    activate: bool = True,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """兼容旧调用：创建一张没有 z 轴概念的平面地图。新模型可见入口是 manage_space。"""
    return _create_plane_map_command(name, width, height, grid_size, description, activate, state, tool_call_id)


@tool
def switch_plane_map(
    map_id: str,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """兼容旧调用：切换当前剧情所在的平面地图。新模型可见入口是 manage_space。"""
    return _switch_plane_map_command(map_id, state, tool_call_id)


@tool
def place_unit(
    unit_id: str,
    x: float,
    y: float,
    map_id: str | None = None,
    facing_deg: float = 0,
    footprint_radius: float = 2.5,
    reason: str = "",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """兼容旧调用：剧情摆放、传送和初始入场。新模型可见入口是 manage_space。"""
    return _place_unit_command(unit_id, x, y, map_id, facing_deg, footprint_radius, reason, state, tool_call_id)


@tool
def move_unit(
    unit_id: str,
    x: float,
    y: float,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """兼容旧调用：移动单位并在战斗中扣除移动力。新模型可见入口是 manage_space。"""
    return _move_unit_command(unit_id, x, y, state, tool_call_id)


@tool
def measure_distance(
    source_id: str,
    target_id: str,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """兼容旧调用：测量两个已放置单位之间的平面距离。新模型可见入口是 manage_space。"""
    return _measure_distance_command(source_id, target_id, state, tool_call_id)


@tool
def query_units_in_radius(
    x: float,
    y: float,
    radius: float,
    map_id: str | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """兼容旧调用：查询当前平面某点半径内的单位。新模型可见入口是 manage_space。"""
    return _query_units_in_radius_command(x, y, radius, map_id, state, tool_call_id)


@tool
def remove_unit(
    unit_id: str | None = None,
    unit_ids: list[str] | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """兼容旧调用：从空间中直接移除一个或多个单位。新模型可见入口是 manage_space。"""
    return _remove_unit_command(unit_id, unit_ids, state, tool_call_id)
