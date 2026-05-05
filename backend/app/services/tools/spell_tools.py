"""法术施放工具"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.services.tools._helpers import (
    get_combatant,
    get_condition_action_block_reason,
    refresh_arcane_ward_on_abjuration,
    remove_action_breaking_conditions,
)
from app.spells import get_spell_module
from app.spells._base import get_spell_range_feet
from app.graph.state import Point2D
from app.space.geometry import (
    build_space_state,
    cone_area,
    point_in_map,
    square_area,
    units_in_geometry,
    units_in_radius,
    validate_point_distance,
    validate_unit_distance,
)


def _resolve_area_target_ids(
    area_def: dict,
    state: dict,
    caster_id: str,
    target_ids: list[str],
    area_point: Point2D | None,
) -> list[str] | None:
    """按法术范围形状从空间系统自动展开目标；无空间时交还旧手动目标流程。"""
    space_raw = state.get("space")
    if not space_raw:
        return None
    space = build_space_state(space_raw)
    if not space.maps:
        return None
    caster_placement = space.placements[caster_id]
    shape = area_def["shape"]
    origin_kind = area_def.get("origin", "point")

    if origin_kind == "point":
        if not area_point:
            return None
        return [
            unit_id for unit_id, _ in units_in_radius(
                space.placements,
                map_id=caster_placement.map_id,
                origin=area_point,
                radius=area_def["radius"],
            )
        ]

    if origin_kind == "target":
        if not target_ids:
            return None
        primary = space.placements[target_ids[0]]
        return [
            unit_id for unit_id, _ in units_in_radius(
                space.placements,
                map_id=primary.map_id,
                origin=primary.position,
                radius=area_def["radius"],
            )
        ]

    origin = caster_placement.position
    if shape == "cone":
        area = cone_area(
            origin,
            caster_placement.facing_deg,
            area_def["length"],
            area_def.get("angle_deg", 53.13),
        )
    elif shape == "square":
        area = square_area(origin, caster_placement.facing_deg, area_def["size"])
    else:
        return None

    return [
        unit_id for unit_id, _ in units_in_geometry(
            space.placements,
            map_id=caster_placement.map_id,
            area=area,
            origin=origin,
        )
        if unit_id != caster_id
    ]


def _cantrip_dice_count(character_level: int) -> int:
    """戏法伤害骰数随角色等级缩放：1级=1, 5级=2, 11级=3, 17级=4"""
    if character_level >= 17:
        return 4
    if character_level >= 11:
        return 3
    if character_level >= 5:
        return 2
    return 1


def _break_concentration(
    player_dict: dict,
    lines: list[str],
    *,
    combat_dict: dict | None = None,
    scene_raw: dict[str, dict] | None = None,
) -> bool:
    """丢弃当前专注法术，并清掉全场由该专注挂出的条件。"""
    old_spell = player_dict.get("concentrating_on")
    if not old_spell:
        return False
    caster_name = player_dict.get("name", "?")
    source_id = f"concentration:{old_spell}"

    units = [player_dict]
    if combat_dict:
        units.extend(combat_dict.get("participants", {}).values())
    if scene_raw:
        units.extend(scene_raw.values())

    for unit in units:
        conditions = unit.get("conditions", [])
        unit["conditions"] = [c for c in conditions if c.get("source_id") != source_id]

    player_dict["concentrating_on"] = None
    lines.append(f"（{caster_name} 不再专注于 {old_spell}）")
    return True


def _resolve_enemy_counterspell_against_player(
    player_dict: dict,
    combat_dict: dict | None,
    spell_def: dict,
    slot_level: int,
    state: dict,
) -> tuple[bool, list[str]]:
    """玩家施法时给敌对施法者一次 on_enemy_cast 反应窗口。"""
    if not combat_dict:
        return False, []

    from app.services.tools.reactions import resolve_npc_reaction

    context = {
        "trigger_caster_id": player_dict.get("id", ""),
        "trigger_caster_name": player_dict.get("name", "?"),
        "trigger_spell_name_cn": spell_def["name_cn"],
        "trigger_spell_level": slot_level,
        "space": state.get("space"),
        "targets": [player_dict],
    }
    for npc in combat_dict.get("participants", {}).values():
        if npc.get("hp", 0) <= 0 or npc.get("side") == player_dict.get("side", "player"):
            continue
        reaction = resolve_npc_reaction(npc, "on_enemy_cast", context)
        if reaction.used:
            return reaction.blocked_action, reaction.lines
    return False, []


def _move_caster_by_spell(state: dict, caster_id: str, target_point: Point2D, spell_name_cn: str) -> tuple[str | None, dict | None, str]:
    """让瞬移类法术复用空间模型校验边界，并把新 space 交回施法入口持久化。"""
    space = build_space_state(state.get("space"))
    if not space.maps:
        return f"{spell_name_cn} 需要已启用的平面空间。", None, ""
    if caster_id not in space.placements:
        return f"发起者 '{caster_id}' 尚未放置到当前平面地图。", None, ""

    placement = space.placements[caster_id]
    plane_map = space.maps[placement.map_id]
    if not point_in_map(plane_map, target_point):
        return f"目标点 ({target_point.x:g}, {target_point.y:g}) 超出地图 {plane_map.name} 的边界。", None, ""

    origin = placement.position
    placement.position = target_point
    space.placements[caster_id] = placement
    line = f"空间位置：({origin.x:g}, {origin.y:g}) → ({target_point.x:g}, {target_point.y:g})"
    return None, space.model_dump(), line


@tool
def cast_spell(
    spell_id: str,
    target_ids: list[str],
    slot_level: int = 0,
    target_point: dict[str, float] | None = None,
    end_concentration: bool = False,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """施放法术，或主动结束当前专注。
    本工具会写回命中、伤害、治疗、资源与状态结果。

    当角色想提前关闭正在维持的专注法术（如 hold_person、blur、darkness）时，
    也使用本工具：传 end_concentration=True 即可；此时 spell_id/target_ids 只作占位，不会实际施法。

    Args:
        spell_id: 法术标识符（如 "magic_missile", "fire_bolt", "shield"）。
        target_ids: 目标单位 ID 列表。对自身施法传 ["self"]。
        slot_level: 使用的法术位等级。0 表示使用该法术最低环位。
        target_point: 点选范围法术的目标坐标，如 {"x": 30, "y": 20}。
        end_concentration: 主动结束当前专注时传 True；优先于 spell_id，不消耗动作或法术位。
    """
    def _reject(msg: str) -> Command:
        return Command(update={"messages": [ToolMessage(content=msg, tool_call_id=tool_call_id)]})

    player_raw = state.get("player")
    if not player_raw:
        return _reject("玩家尚未加载角色卡。")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw)
    player_id = f"player_{player_dict.get('name', 'player')}"
    player_dict.setdefault("id", player_id)

    combat_raw = state.get("combat")
    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw) if combat_raw else None
    scene_units_raw = state.get("scene_units") or {}
    scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units_raw.items()} if hasattr(scene_units_raw, "items") else {}

    if end_concentration:
        lines: list[str] = []
        did_break = _break_concentration(player_dict, lines, combat_dict=combat_dict, scene_raw=scene_raw)
        if not lines:
            lines.append(f"（{player_dict.get('name', '?')} 当前没有维持专注法术。）")
        update: dict = {
            "player": player_dict,
            "messages": [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
        }
        if combat_dict:
            update["combat"] = combat_dict
        if did_break and scene_raw:
            update["scene_units"] = scene_raw
        return Command(update=update)

    spell_mod = get_spell_module(spell_id)
    if not spell_mod:
        return _reject(f"未知法术 '{spell_id}'。")

    spell_def = spell_mod.SPELL_DEF
    min_level = spell_def["level"]
    is_cantrip = min_level == 0

    if is_cantrip:
        slot_level = 0
    else:
        slot_level = slot_level or min_level
        if slot_level < min_level:
            return _reject(f"{spell_def['name_cn']}至少需要{min_level}环法术位。")

    # 戏法从 known_cantrips 校验，有环法术从 known_spells 校验
    if is_cantrip:
        if spell_id not in player_dict.get("known_cantrips", []):
            return _reject(f"角色不会戏法 '{spell_def['name_cn']}'。已知戏法: {player_dict.get('known_cantrips', [])}")
    else:
        if spell_id not in player_dict.get("known_spells", []):
            return _reject(f"角色不会 '{spell_def['name_cn']}'。已知法术: {player_dict.get('known_spells', [])}")

    # 戏法不消耗法术位
    consume_key = None
    if not is_cantrip:
        from app.services.tools._helpers import consume_spell_slot
        resources = player_dict.get("resources", {})
        consume_key = consume_spell_slot(resources, slot_level)
        if not consume_key:
            return _reject(f"{slot_level}环法术位已耗尽。")

    # 动作经济
    casting_time = spell_def.get("casting_time", "action")
    if player_dict.get("id"):
        if casting_time in ("action", "bonus_action") and combat_dict and combat_dict.get("current_actor_id") != player_id:
            return _reject(f"当前不是 {player_dict.get('name')} 的回合。")
        action_map = {"action": "action_available", "bonus_action": "bonus_action_available", "reaction": "reaction_available"}
        action_key = action_map[casting_time]
        if not player_dict.get(action_key, True):
            label = {"action": "动作", "bonus_action": "附赠动作", "reaction": "反应"}[casting_time]
            return _reject(f"本回合的{label}已用尽。")
        if block_reason := get_condition_action_block_reason(player_dict, casting_time):
            return _reject(block_reason)
        player_dict[action_key] = False

    # 解析目标
    targets: list[dict] = []
    has_scene_target = False
    resolved_target_ids: list[str] = []

    spell_range = get_spell_range_feet(spell_def)
    area_def = spell_def.get("area")
    area_point = Point2D(**target_point) if target_point else None
    explicit_target_ids = list(target_ids)
    if area_def and area_def.get("origin", "point") == "point" and area_point:
        if spell_range is not None:
            distance_error, space_state = validate_point_distance(
                state.get("space"),
                player_dict["id"],
                area_point,
                spell_range,
                action_label=spell_def["name_cn"],
            )
            if distance_error:
                return _reject(distance_error)
        else:
            _, space_state = validate_point_distance(
                state.get("space"),
                player_dict["id"],
                area_point,
                0,
                action_label=spell_def["name_cn"],
            )
        if not space_state or not space_state.maps:
            return _reject(f"{spell_def['name_cn']} 需要已启用的平面空间来解析目标点范围。")

    if area_def:
        try:
            auto_target_ids = _resolve_area_target_ids(area_def, state, player_dict["id"], target_ids, area_point)
        except KeyError as exc:
            return _reject(f"范围法术缺少空间落点：{exc.args[0]}。")
        if auto_target_ids is not None:
            target_ids = list(dict.fromkeys([*target_ids, *auto_target_ids]))

    for tid in target_ids:
        if tid in ("self", player_dict["id"]):
            targets.append(player_dict)
            resolved_target_ids.append(player_dict["id"])
        elif combat_dict:
            found = get_combatant(combat_dict, player_dict, tid)
            if found:
                targets.append(found)
                resolved_target_ids.append(tid)
            elif tid in scene_raw:
                targets.append(scene_raw[tid])
                has_scene_target = True
                resolved_target_ids.append(tid)
            else:
                return _reject(f"找不到目标 '{tid}'。")
        elif tid in scene_raw:
            targets.append(scene_raw[tid])
            has_scene_target = True
            resolved_target_ids.append(tid)
        else:
            return _reject(f"找不到目标 '{tid}'。")

    if spell_range is not None and not area_point:
        range_target_ids = list(resolved_target_ids)
        if area_def and area_def.get("origin") == "self":
            range_target_ids = []
        elif area_def and area_def.get("origin") == "target":
            range_target_ids = explicit_target_ids[:1]

        for resolved_target_id in range_target_ids:
            if resolved_target_id == player_dict["id"]:
                continue
            if distance_error := validate_unit_distance(
                state.get("space"),
                player_dict["id"],
                resolved_target_id,
                spell_range,
                action_label=spell_def["name_cn"],
            ):
                return _reject(distance_error)

    if spell_id == "misty_step":
        if not area_point:
            return _reject("迷踪步需要 target_point 指定瞬移落点。")
        distance_error, _ = validate_point_distance(
            state.get("space"),
            player_dict["id"],
            area_point,
            spell_range or 30,
            action_label=spell_def["name_cn"],
        )
        if distance_error:
            return _reject(distance_error)

    # 消耗法术位
    resources = player_dict.get("resources", {})
    if consume_key:
        resources[consume_key] -= 1
        player_dict["resources"] = resources

    countered, counterspell_lines = _resolve_enemy_counterspell_against_player(
        player_dict,
        combat_dict,
        spell_def,
        slot_level,
        state,
    )
    if countered:
        lines = counterspell_lines + [f"{spell_def['name_cn']} 被法术反制打断，没有产生效果。"]
        if consume_key:
            lines.append(f"（剩余{slot_level}环法术位: {resources.get(consume_key, 0)}）")
        update: dict = {
            "player": player_dict,
            "messages": [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
        }
        if combat_dict:
            update["combat"] = combat_dict
        return Command(update=update)

    # 专注管理：施放新专注法术时丢弃旧专注
    extra_lines: list[str] = list(counterspell_lines)
    is_concentration = spell_def.get("concentration", False)
    if is_concentration:
        _break_concentration(player_dict, extra_lines, combat_dict=combat_dict, scene_raw=scene_raw)

    # 执行法术，传入戏法缩放信息
    kwargs = {}
    if is_cantrip:
        kwargs["cantrip_scale"] = _cantrip_dice_count(player_dict.get("level", 1))
    if spell_id == "misty_step" and area_point:
        space_error, space_update, move_line = _move_caster_by_spell(state, player_dict["id"], area_point, spell_def["name_cn"])
        if space_error:
            return _reject(space_error)
        kwargs["space_update"] = space_update
        kwargs["move_line"] = move_line
    if spell_id == "flaming_sphere" and area_point:
        kwargs["target_point"] = area_point.model_dump()
    extra_lines.extend(remove_action_breaking_conditions(player_dict, event="spell"))
    result = spell_mod.execute(caster=player_dict, targets=targets, slot_level=slot_level, **kwargs)

    # 标记专注
    if is_concentration:
        player_dict["concentrating_on"] = spell_id

    # 防护学派结界刷新：普通施法与反应施法共享同一条规则入口
    refresh_arcane_ward_on_abjuration(player_dict, spell_def, slot_level, extra_lines)

    update: dict = {"player": player_dict}
    if combat_dict:
        update["combat"] = combat_dict
    if has_scene_target:
        update["scene_units"] = scene_raw

    if hp_changes := result.get("hp_changes"):
        update["hp_changes"] = hp_changes
    if space_update := result.get("space"):
        update["space"] = space_update

    lines = extra_lines + result.get("lines", [])
    if consume_key:
        lines.append(f"（剩余{slot_level}环法术位: {resources.get(consume_key, 0)}）")
    update["messages"] = [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)]
    return Command(update=update)
