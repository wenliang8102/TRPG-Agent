"""平面空间系统公共入口。"""

from app.space.geometry import (
    build_space_state,
    cone_area,
    distance_between,
    distance_to_point,
    map_bounds,
    placement_distance,
    point_in_map,
    square_area,
    units_in_geometry,
    units_in_radius,
    validate_point_distance,
    validate_unit_distance,
)

__all__ = [
    "build_space_state",
    "cone_area",
    "distance_between",
    "distance_to_point",
    "map_bounds",
    "placement_distance",
    "point_in_map",
    "square_area",
    "units_in_geometry",
    "units_in_radius",
    "validate_point_distance",
    "validate_unit_distance",
]
