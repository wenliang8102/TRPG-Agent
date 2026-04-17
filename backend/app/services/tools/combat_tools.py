"""战斗工具链 — 生成怪物、开始/结束战斗、攻击、回合推进"""

from __future__ import annotations

from typing import Annotated, Literal

import d20
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.calculation.bestiary import spawn_combatants
from app.services.tools._helpers import (
    advance_turn,
    apply_hp_change,
    clear_player_combat_fields,
    get_all_combatants,
    get_combatant,
    prepare_player_for_combat,
    resolve_single_attack,
)


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

    lines, _, hp_change, extra_info = resolve_single_attack(attacker, target, attack_name, advantage)

    tool_msg = ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)
    tool_msg.artifact = {"raw_roll": extra_info.get("raw_roll")}

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
    """推进到下一个行动者的回合。如果所有人都行动过，则进入新的回合。"""
    combat_raw = state.get("combat")
    if not combat_raw:
        return "当前不在战斗中。"

    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)

    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    if not combat_dict.get("initiative_order"):
        return "先攻顺序为空，请先调用 start_combat。"

    result_text = advance_turn(combat_dict, player_dict)

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
    update: dict = {"combat": None, "phase": "exploration"}

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

        update["scene_units"] = scene_raw
        update["dead_units"] = dead_raw

    if player_dict:
        update["player"] = player_dict

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
