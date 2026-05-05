"""战斗工具链 — 生成怪物、开始/结束战斗、攻击、回合推进"""

from __future__ import annotations

from typing import Annotated, Literal

import d20
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.calculation.bestiary import spawn_combatants
from app.space.geometry import build_space_state
from app.services.tools._helpers import (
    advance_turn,
    apply_hp_change,
    apply_attack_damage,
    build_attack_roll_event_payload,
    build_pending_reaction_state,
    clear_player_combat_fields,
    choose_attack,
    available_attack_names,
    get_all_combatants,
    get_combatant,
    prepare_player_for_combat,
    roll_attack_hit,
    validate_attack_distance,
)
from app.services.tools.reactions import get_available_reactions


def _message_count(state: dict | None) -> int:
    messages = state.get("messages") or [] if state else []
    return len(messages)


def _combat_archive_start_index(state: dict | None) -> int:
    """战斗归档从触发 start_combat 的 AIMessage 开始，保证 tool_calls 与 ToolMessage 同生共死。"""
    messages = state.get("messages") or [] if state else []
    if messages and getattr(messages[-1], "tool_calls", None):
        return len(messages) - 1
    return len(messages)


def _combat_archives_from_state(state: dict | None) -> list[dict]:
    if not state:
        return []

    raw_archives = state.get("combat_archives") or []
    archives: list[dict] = []
    for archive in raw_archives:
        if hasattr(archive, "model_dump"):
            archives.append(archive.model_dump())
        elif hasattr(archive, "items"):
            archives.append(dict(archive))
    return archives


def _build_combat_archive(summary: str, start_index: int, end_index: int) -> dict:
    """归档只保留区间锚点与高密度摘要，供后续 prompt 折叠使用。"""
    safe_start = max(start_index, 0)
    safe_end = max(end_index, safe_start)
    return {
        "summary": summary.strip(),
        "start_index": safe_start,
        "end_index": safe_end,
    }


def _remove_space_units(space_raw: dict | None, unit_ids: list[str]) -> dict | None:
    """战斗收尾时把尸体从空间落点里真正移除，避免地图上只剩“摆角落”的假清理。"""
    if not space_raw:
        return None

    space = build_space_state(space_raw)
    for unit_id in unit_ids:
        space.placements.pop(unit_id, None)
    return space.model_dump()


def _validate_combat_space(state: dict, unit_ids: list[str]) -> str | None:
    """战斗必须绑定客观地图，否则距离、移动和范围规则会失去事实来源。"""
    space = build_space_state(state.get("space"))
    if not space.maps or not space.active_map_id or space.active_map_id not in space.maps:
        return "无法开始战斗：当前没有可用平面地图。请先用 manage_space 创建或切换地图，并放置参战单位。"

    missing = [unit_id for unit_id in unit_ids if unit_id not in space.placements]
    if missing:
        return f"无法开始战斗：以下参战单位尚未放置到当前平面地图: {', '.join(missing)}。请先用 manage_space 放置单位。"

    wrong_map = [unit_id for unit_id in unit_ids if space.placements[unit_id].map_id != space.active_map_id]
    if wrong_map:
        active_map = space.maps[space.active_map_id]
        return f"无法开始战斗：以下参战单位不在当前地图 {active_map.name} [ID:{space.active_map_id}]: {', '.join(wrong_map)}。请先切换地图或重新放置单位。"

    return None


@tool
def spawn_monsters(
    monster_index: str,
    count: int = 1,
    faction: str = "enemy",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None
) -> Command:
    """根据怪物图鉴生成战斗单位实例并加入当前场景。
    怪物数据来自 Open5e SRD（使用英文 slug，如 "goblin", "owlbear", "adult-red-dragon"）。
    生成后的单位进入场景单位池（scene_units），需要通过 start_combat 指定参战。

    Args:
        monster_index: 怪物的 Open5e slug（如 "goblin"）。必须输入其英文代号。
        count: 生成该单位的数量。默认为 1。
        faction: 阵营，通常为 "enemy", "ally" 或 "neutral"。默认 "enemy"。
    """
    try:
        new_combatants = spawn_combatants(monster_index, count, faction)
    except Exception as e:
        return f"生成战斗单位失败: {str(e)}"

    scene_units: dict = state.get("scene_units") or {}
    if hasattr(scene_units, "items"):
        scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()}
    else:
        scene_raw = {}

    for c in new_combatants:
        scene_raw[c.id] = c.model_dump()

    names = [f"{c.name} [ID: {c.id}]" for c in new_combatants]

    return Command(
        update={
            "scene_units": scene_raw,
            "messages": [
                ToolMessage(
                    content=f"成功在场景中生成了 {count} 只 {monster_index}: {', '.join(names)}。\n可用 start_combat 指定哪些单位参加战斗。",
                    tool_call_id=tool_call_id
                )
            ]
        }
    )


@tool
def start_combat(
    combatant_ids: list[str],
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """开始战斗：从场景单位池中选取指定 ID 的单位作为参战者，投先攻骰并排定行动顺序。
    前置条件：必须先用 spawn_monsters 生成单位到场景中。
    玩家角色会自动加入，无需在 combatant_ids 中指定。

    Args:
        combatant_ids: 从场景中参加本次战斗的单位 ID 列表（如 ["goblin_1", "goblin_2"]）。
    """
    scene_units: dict = state.get("scene_units") or {}
    if hasattr(scene_units, "items"):
        scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()}
    else:
        scene_raw = {}

    if not combatant_ids and not scene_raw:
        return "场景中没有任何单位。请先使用 spawn_monsters 生成怪物。"

    participants: dict[str, dict] = {}
    missing: list[str] = []
    for uid in combatant_ids:
        unit = scene_raw.get(uid)
        if unit:
            participants[uid] = unit
        else:
            missing.append(uid)

    if missing:
        available = ", ".join(scene_raw.keys()) or "无"
        return f"找不到以下单位: {', '.join(missing)}。场景中可用单位: {available}"

    # 玩家自动入场 — 直接在 player_dict 上叠加战斗字段，不再复制到 participants
    player_raw = state.get("player")
    player_dict: dict | None = None
    if player_raw:
        player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw)
        prepare_player_for_combat(player_dict)

    if not participants and not player_dict:
        return "没有参战者，请先生成怪物或加载角色卡。"

    # 为所有参战单位投先攻（含玩家）
    all_units: dict[str, dict] = dict(participants)
    if player_dict:
        all_units[player_dict["id"]] = player_dict

    if space_error := _validate_combat_space(state, list(all_units)):
        return Command(update={"messages": [ToolMessage(content=space_error, tool_call_id=tool_call_id)]})

    initiative_list: list[tuple[str, int]] = []
    for uid, p in all_units.items():
        dex_mod = p.get("modifiers", {}).get("dex", 0)
        init_roll = d20.roll(f"1d20+{dex_mod}")
        p["initiative"] = init_roll.total
        initiative_list.append((uid, init_roll.total))

    initiative_list.sort(key=lambda x: x[1], reverse=True)
    order = [uid for uid, _ in initiative_list]

    # combat.participants 仅存 NPC/怪物
    combat_dict = {
        "round": 1,
        "participants": participants,
        "initiative_order": order,
        "current_actor_id": order[0],
    }

    order_desc = "\n".join(
        f"  {i+1}. {all_units[uid].get('name', uid)} [ID: {uid}] (先攻 {init})"
        for i, (uid, init) in enumerate(initiative_list)
    )

    update: dict = {
        "combat": combat_dict,
        "phase": "combat",
        "active_combat_message_start": _combat_archive_start_index(state),
        "messages": [
            ToolMessage(
                content=f"战斗开始！第 1 回合。\n先攻顺序：\n{order_desc}\n\n当前行动者：{all_units[order[0]].get('name', order[0])} [ID: {order[0]}]",
                tool_call_id=tool_call_id,
            )
        ],
    }
    if player_dict:
        update["player"] = player_dict

    return Command(update=update)


@tool
def attack_action(
    attacker_id: str,
    target_id: str,
    attack_name: str | None = None,
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """执行一次攻击动作：命中判定 → 暴击检测 → 伤害结算 → 扣血。
    状态效果（如目盲、隐形等）的优劣势会自动叠加计算。
    玩家攻击结束后如果没有其他额外动作，可以询问玩家或代表玩家调用 `next_turn`。

    Args:
        attacker_id: 攻击者的 ID。
        target_id: 目标的 ID。
        attack_name: 使用的攻击名称（可选，默认使用攻击者的第一个攻击方式）。
        advantage: 攻击优劣势，"normal" / "advantage" / "disadvantage"。
    """
    combat_raw = state.get("combat")
    if not combat_raw:
        return "当前不在战斗中。"

    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)

    # 获取玩家字典（如有）
    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    # 通过统一接口获取攻防双方
    attacker = get_combatant(combat_dict, player_dict, attacker_id)
    target = get_combatant(combat_dict, player_dict, target_id)

    def _reject(msg: str) -> Command:
        return Command(update={"messages": [
            ToolMessage(content=f"[攻击失败] {msg}", tool_call_id=tool_call_id)
        ]})

    if not attacker:
        return _reject(f"找不到攻击者 '{attacker_id}'。")
    if not target:
        return _reject(f"找不到目标 '{target_id}'。")
    if combat_dict.get("current_actor_id") != attacker_id:
        return _reject(f"现在不是 {attacker.get('name', attacker_id)} 的回合，当前行动者为 {combat_dict.get('current_actor_id')}。")
    if target.get("hp", 0) <= 0:
        return _reject(f"目标 {target.get('name', target_id)} 已经倒下，无法攻击。")
    if not attacker.get("action_available", True):
        return _reject(f"{attacker.get('name', attacker_id)} 本回合的动作已用尽。")

    chosen_attack = choose_attack(attacker, attack_name)
    if attack_name and chosen_attack is None:
        available = ", ".join(available_attack_names(attacker)) or "无"
        return _reject(f"未知攻击 '{attack_name}'。可用攻击: {available}。")
    if distance_error := validate_attack_distance(state.get("space"), attacker_id, target_id, chosen_attack):
        return _reject(distance_error)

    roll_info = roll_attack_hit(attacker, target, attack_name, advantage, state)

    if (
        player_dict
        and attacker.get("side") != "player"
        and target is player_dict
        and roll_info.get("hit")
        and not roll_info.get("blocked")
    ):
        reaction_context = {
            "attacker": attacker.get("name", attacker_id),
            "attack_roll": {
                "raw_roll": roll_info.get("raw_roll", roll_info.get("natural", 0)),
                "attack_bonus": roll_info.get("attack_bonus", 0),
                "final_total": roll_info.get("hit_total", 0),
                "hit_total": roll_info.get("hit_total", 0),
                "target_ac": roll_info.get("target_ac", 10),
            },
        }
        available_reactions = get_available_reactions(player_dict, "on_hit", reaction_context)
        if available_reactions:
            pending_reaction_msg = ToolMessage(
                content=(
                    f"{attacker.get('name', attacker_id)} 的攻击命中了 {target.get('name', target_id)}，"
                    "已进入反应判定，等待玩家选择。"
                ),
                tool_call_id=tool_call_id,
                additional_kwargs={"hidden_from_ui": True},
            )
            return Command(
                update={
                    "combat": combat_dict,
                    "player": player_dict,
                    "messages": [pending_reaction_msg],
                    "pending_reaction": build_pending_reaction_state(attacker, target, roll_info, available_reactions),
                    "reaction_choice": None,
                }
            )

    lines, _, hp_change, _ = apply_attack_damage(attacker, target, roll_info)

    attack_roll_payload = build_attack_roll_event_payload(roll_info)

    tool_message_kwargs = {}
    if attack_roll_payload:
        tool_message_kwargs["additional_kwargs"] = {"attack_roll": attack_roll_payload}

    tool_msg = ToolMessage(
        content="\n".join(lines),
        tool_call_id=tool_call_id,
        **tool_message_kwargs,
    )

    # 玩家数据已在 player_dict 上原地修改，无需手动同步
    update: dict = {
        "combat": combat_dict,
        "messages": [tool_msg],
    }
    if player_dict:
        update["player"] = player_dict
    if hp_change:
        update["hp_changes"] = [hp_change]

    return Command(update=update)


@tool
def next_turn(
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """结束当前行动者回合，并推进到下一个存活单位。如果所有人都行动过，则进入新的回合。"""
    combat_raw = state.get("combat")
    if not combat_raw:
        return "当前不在战斗中。"

    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)

    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    if not combat_dict.get("initiative_order"):
        return "先攻顺序为空，请先调用 start_combat。"

    result_text = advance_turn(combat_dict, player_dict, state)

    update: dict = {
        "combat": combat_dict,
        "messages": [
            ToolMessage(content=result_text, tool_call_id=tool_call_id)
        ],
    }
    if player_dict:
        update["player"] = player_dict

    return Command(update=update)


@tool
def end_combat(
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """结束当前战斗。存活的非玩家单位回归场景，死亡单位归入死亡档案（可搜尸等）。"""
    combat_raw = state.get("combat")
    summary = "战斗结束。"
    update: dict = {
        "combat": None,
        "phase": "exploration",
        "active_combat_message_start": None,
    }

    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    if combat_raw:
        combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
        rounds = combat_dict.get("round", 0)
        participants = combat_dict.get("participants", {})

        alive_names: list[str] = []
        fallen_names: list[str] = []

        scene_units: dict = state.get("scene_units") or {}
        scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()} if hasattr(scene_units, "items") else {}
        dead_units: dict = state.get("dead_units") or {}
        dead_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in dead_units.items()} if hasattr(dead_units, "items") else {}

        # 处理玩家 — HP 已在 player_dict 上保持最新，只需清除战斗覆盖字段
        if player_dict:
            if player_dict.get("hp", 0) > 0:
                alive_names.append(player_dict.get("name", "player"))
            else:
                fallen_names.append(player_dict.get("name", "player"))
            clear_player_combat_fields(player_dict)

        # 处理 NPC（仅存于 participants）
        for uid, p in participants.items():
            name = p.get("name", uid)
            if p.get("hp", 0) > 0:
                alive_names.append(name)
                scene_raw[uid] = p
            else:
                fallen_names.append(name)
                dead_raw[uid] = p
                scene_raw.pop(uid, None)

        parts = [f"共进行了 {rounds} 回合。"]
        if alive_names:
            parts.append(f"存活: {', '.join(alive_names)}")
        if fallen_names:
            parts.append(f"倒下: {', '.join(fallen_names)}")
        summary = " ".join(parts)

        dead_unit_ids = list(dead_raw.keys())
        if dead_unit_ids:
            space_raw = state.get("space")
            cleaned_space = _remove_space_units(space_raw, dead_unit_ids)
            if cleaned_space is not None:
                update["space"] = cleaned_space

        update["scene_units"] = scene_raw
        update["dead_units"] = dead_raw

    if player_dict:
        update["player"] = player_dict

    active_start = state.get("active_combat_message_start") if state else None
    combat_archives = _combat_archives_from_state(state)
    if isinstance(active_start, int):
        combat_archives.append(_build_combat_archive(summary, active_start, _message_count(state)))
        update["combat_archives"] = combat_archives

    update["messages"] = [ToolMessage(content=summary, tool_call_id=tool_call_id)]
    return Command(update=update)


@tool
def clear_dead_units(
    unit_ids: list[str] | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """清除死亡单位档案。可指定 ID 列表部分清除，或不传参数清除全部。
    适用于剧情上玩家已完成搜刮尸体、处理遗骸等环节后的清理。

    Args:
        unit_ids: 要清除的死亡单位 ID 列表。为空则清除全部。
    """
    dead_units: dict = state.get("dead_units") or {}
    dead_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in dead_units.items()} if hasattr(dead_units, "items") else {}

    if not dead_raw:
        return Command(update={"messages": [
            ToolMessage(content="当前没有死亡单位。", tool_call_id=tool_call_id)
        ]})

    if unit_ids:
        removed = [uid for uid in unit_ids if uid in dead_raw]
        for uid in removed:
            del dead_raw[uid]
        msg = f"已清除死亡单位: {', '.join(removed)}" if removed else "指定的 ID 不在死亡单位列表中。"
    else:
        count = len(dead_raw)
        dead_raw.clear()
        msg = f"已清除全部 {count} 个死亡单位。"

    return Command(update={
        "dead_units": dead_raw,
        "messages": [ToolMessage(content=msg, tool_call_id=tool_call_id)],
    })
