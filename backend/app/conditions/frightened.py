"""恐慌 (Frightened) 条件 — 攻击受压制，且不能主动靠近恐惧来源。"""

from app.conditions._base import CombatEffects, ConditionDef

CONDITION_DEF = ConditionDef(
    id="frightened",
    name_cn="恐慌",
    description="恐慌目标攻击具有劣势，并且不能主动靠近恐惧来源。",
    effects=CombatEffects(attack_advantage="disadvantage"),
)


def on_movement_eligibility(condition: dict, actor: dict, state: dict, destination) -> str | None:
    """恐慌的关键战术约束：不能让自己离来源更近。"""
    source_id = condition.get("source_id", "")
    if not source_id or not state.get("space"):
        return None

    from app.space.geometry import build_space_state, distance_to_point

    space = build_space_state(state.get("space"))
    actor_id = actor.get("id", "")
    if actor_id not in space.placements or source_id not in space.placements:
        return None

    actor_placement = space.placements[actor_id]
    source_placement = space.placements[source_id]
    if actor_placement.map_id != source_placement.map_id:
        return None

    current_distance = distance_to_point(source_placement, actor_placement.position)
    next_distance = distance_to_point(source_placement, destination)
    if next_distance < current_distance:
        return f"{actor.get('name', '?')} 处于恐慌状态，不能主动靠近 {source_id}。"
    return None
