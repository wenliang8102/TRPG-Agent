"""二维平面几何辅助函数。"""

from __future__ import annotations

import math

from shapely import Point, box
from shapely.affinity import rotate, translate
from shapely.geometry.base import BaseGeometry
from shapely.geometry import Polygon

from app.graph.state import PlaneMapState, Point2D, SpaceState, UnitPlacementState


def build_space_state(raw_space: dict | SpaceState | None) -> SpaceState:
    """把 LangGraph 注入的 dict/Pydantic 状态统一还原为 SpaceState。"""
    if raw_space is None:
        return SpaceState()
    if isinstance(raw_space, SpaceState):
        return raw_space.model_copy(deep=True)
    return SpaceState.model_validate(raw_space)


def map_bounds(map_state: PlaneMapState) -> BaseGeometry:
    """用地图宽高生成 Shapely 边界面，后续边界判断只依赖这一处。"""
    return box(0, 0, map_state.width, map_state.height)


def point_in_map(map_state: PlaneMapState, position: Point2D) -> bool:
    """确认坐标落在地图边界内，允许贴边站位。"""
    return map_bounds(map_state).covers(Point(position.x, position.y))


def distance_between(first: UnitPlacementState, second: UnitPlacementState) -> float:
    """计算两个单位中心点的欧氏距离。"""
    p1 = Point(first.position.x, first.position.y)
    p2 = Point(second.position.x, second.position.y)
    return p1.distance(p2)


def distance_to_point(placement: UnitPlacementState, point: Point2D) -> float:
    """计算单位中心点到任意空间坐标的距离，供点选法术和陷阱复用。"""
    return Point(placement.position.x, placement.position.y).distance(Point(point.x, point.y))


def units_in_radius(
    placements: dict[str, UnitPlacementState],
    *,
    map_id: str,
    origin: Point2D,
    radius: float,
) -> list[tuple[str, float]]:
    """查询同一平面内指定半径覆盖到的单位，返回按距离排序的结果。"""
    area = Point(origin.x, origin.y).buffer(radius)
    matches: list[tuple[str, float]] = []

    for unit_id, placement in placements.items():
        if placement.map_id != map_id:
            continue
        point = Point(placement.position.x, placement.position.y)
        if area.covers(point):
            matches.append((unit_id, Point(origin.x, origin.y).distance(point)))

    matches.sort(key=lambda item: (item[1], item[0]))
    return matches


def units_in_geometry(
    placements: dict[str, UnitPlacementState],
    *,
    map_id: str,
    area: BaseGeometry,
    origin: Point2D,
) -> list[tuple[str, float]]:
    """查询几何范围覆盖到的单位；按到范围原点的距离排序，保持战报目标顺序稳定。"""
    origin_point = Point(origin.x, origin.y)
    matches: list[tuple[str, float]] = []
    for unit_id, placement in placements.items():
        if placement.map_id != map_id:
            continue
        point = Point(placement.position.x, placement.position.y)
        if area.covers(point):
            matches.append((unit_id, origin_point.distance(point)))

    matches.sort(key=lambda item: (item[1], item[0]))
    return matches


def cone_area(origin: Point2D, facing_deg: float, length: float, angle_deg: float = 53.13) -> BaseGeometry:
    """生成面朝方向的锥形区域；默认角度让 15 尺锥形在末端约有 15 尺宽。"""
    half_angle = math.radians(angle_deg / 2)
    points = [
        (0, 0),
        (length, math.tan(half_angle) * length),
        (length, -math.tan(half_angle) * length),
    ]
    area = Polygon(points)
    return translate(rotate(area, facing_deg, origin=(0, 0), use_radians=False), xoff=origin.x, yoff=origin.y)


def square_area(origin: Point2D, facing_deg: float, size: float) -> BaseGeometry:
    """生成从施法者向前展开的方形区域，用于雷鸣波这类贴身立方效果。"""
    area = box(0, -size / 2, size, size / 2)
    return translate(rotate(area, facing_deg, origin=(0, 0), use_radians=False), xoff=origin.x, yoff=origin.y)


def placement_distance(space: SpaceState, source_id: str, target_id: str) -> tuple[float, str | None]:
    """计算两个单位的空间距离；不同地图时返回原因文本。"""
    source = space.placements[source_id]
    target = space.placements[target_id]
    if source.map_id != target.map_id:
        return 0, "两个单位不在同一张地图上。"
    return distance_between(source, target), None


def validate_unit_distance(
    space_raw: dict | SpaceState | None,
    source_id: str,
    target_id: str,
    max_distance: float,
    *,
    action_label: str,
) -> str | None:
    """按最大距离校验两个单位位置；空间未启用时保持旧规则行为。"""
    if not space_raw:
        return None

    space = build_space_state(space_raw)
    if not space.maps:
        return None
    if source_id not in space.placements:
        return f"发起者 '{source_id}' 尚未放置到当前平面地图。"
    if target_id not in space.placements:
        return f"目标 '{target_id}' 尚未放置到当前平面地图。"

    distance, reason = placement_distance(space, source_id, target_id)
    if reason:
        return reason
    if distance > max_distance:
        return f"{action_label} 距离不足：目标距离 {distance:.1f} 尺，最大距离 {max_distance:g} 尺。"
    return None


def validate_point_distance(
    space_raw: dict | SpaceState | None,
    source_id: str,
    target_point: Point2D,
    max_distance: float,
    *,
    action_label: str,
) -> tuple[str | None, SpaceState | None]:
    """校验单位到目标点的施法距离；同时返回解析后的 SpaceState 供后续范围筛选复用。"""
    if not space_raw:
        return None, None

    space = build_space_state(space_raw)
    if not space.maps:
        return None, space
    if source_id not in space.placements:
        return f"发起者 '{source_id}' 尚未放置到当前平面地图。", space

    source = space.placements[source_id]
    plane_map = space.maps[source.map_id]
    if not point_in_map(plane_map, target_point):
        return f"目标点 ({target_point.x:g}, {target_point.y:g}) 超出地图 {plane_map.name} 的边界。", space

    distance = distance_to_point(source, target_point)
    if distance > max_distance:
        return f"{action_label} 距离不足：目标点距离 {distance:.1f} 尺，最大距离 {max_distance:g} 尺。", space
    return None, space
