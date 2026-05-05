"""状态效果管理工具 — 施加/移除状态"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.conditions import get_condition_def, has_condition, list_condition_defs, remove_condition_by_id, upsert_condition
from app.conditions._base import create_condition
from app.services.tools._helpers import get_combatant, sync_ac_state, sync_movement_state


def _locate_target(state: dict, target_id: str) -> tuple[dict | None, dict, str]:
    """定位目标单位，返回 (target_dict, context_dict, resolved_target_id)。
    context_dict 内部字段用于 _build_update 回写状态。"""
    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    if target_id == "player" and player_dict:
        target_id = f"player_{player_dict.get('name', 'player')}"

    # 战斗中：通过统一接口查找（玩家从 player_dict，NPC 从 participants）
    combat_raw = state.get("combat")
    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw) if combat_raw else None
    if combat_dict:
        target = get_combatant(combat_dict, player_dict, target_id)
        if target:
            is_player = (player_dict and target is player_dict)
            return target, {
                "_combat_dict": combat_dict,
                "_player_dict": player_dict,
                "_target_id": target_id,
                "_is_player": is_player,
            }, target_id

    # 场景单位
    scene_units = state.get("scene_units") or {}
    scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()} if hasattr(scene_units, "items") else {}
    if target_id in scene_raw:
        return scene_raw[target_id], {"_scene_raw": scene_raw, "_player_dict": player_dict, "_target_id": target_id}, target_id

    # 玩家本体（非战斗状态）
    if player_dict and target_id == f"player_{player_dict.get('name', 'player')}":
        return player_dict, {"_player_dict": player_dict, "_target_id": target_id}, target_id

    return None, {}, target_id


def _build_update(target: dict, ctx: dict) -> dict:
    """根据 _locate_target 返回的上下文，构建状态回写字典"""
    update: dict = {}
    target_id = ctx.get("_target_id", "")
    player_dict = ctx.get("_player_dict")
    is_player = ctx.get("_is_player", False)

    if "_combat_dict" in ctx and not is_player:
        combat_dict = ctx["_combat_dict"]
        combat_dict["participants"][target_id] = target
        update["combat"] = combat_dict
    elif "_combat_dict" in ctx and is_player:
        # NPC 参与者可能被间接修改（如回合推进），仍需回写 combat
        update["combat"] = ctx["_combat_dict"]

    if "_scene_raw" in ctx:
        scene_raw = ctx["_scene_raw"]
        scene_raw[target_id] = target
        update["scene_units"] = scene_raw

    # 玩家数据直接在 player_dict 上修改，无需额外同步
    if player_dict and (is_player or target is player_dict):
        update["player"] = player_dict

    return update


@tool
def apply_condition(
    target_id: str,
    condition_id: str,
    source_id: str = "",
    duration: int | None = None,
    reason: str = "",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """对目标单位施加一个状态效果（如目盲、魅惑、隐形等）。
    受到该状态影响的单位在战斗中会自动应用相应的优劣势与限制规则。
    已注册状态: blinded(目盲), charmed(魅惑), incapacitated(失能), invisible(隐形)。
    也可施加自定义状态 ID（如 hunters_mark），系统会记录但不自动应用战斗效果。

    Args:
        target_id: 目标单位 ID，或 "player" 表示当前玩家。
        condition_id: 状态 ID（如 "blinded"）。
        source_id: 施加来源的单位 ID（如 "goblin_1"），用于追踪魅惑等依赖来源的状态。
        duration: 持续回合数。不传或 null 表示手动移除前一直生效。
        reason: 施加原因的简短描述。
    """
    target, ctx, resolved_id = _locate_target(state, target_id)
    if not target:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到目标 '{target_id}'。", tool_call_id=tool_call_id)
        ]})

    conds: list[dict] = target.setdefault("conditions", [])

    # 同 ID 不重复添加
    if has_condition(conds, condition_id):
        return Command(update={"messages": [
            ToolMessage(content=f"{target.get('name', resolved_id)} 已处于 {condition_id} 状态。", tool_call_id=tool_call_id)
        ]})

    upsert_condition(
        target,
        create_condition(condition_id, source_id=source_id, duration=duration),
    )
    sync_ac_state(target)
    sync_movement_state(target)

    # 状态元数据用于日志
    cdef = get_condition_def(condition_id)
    cond_label = cdef.name_cn if cdef else condition_id
    dur_text = f"，持续 {duration} 回合" if duration else ""
    src_text = f"（来源: {source_id}）" if source_id else ""

    msg_parts = [f"{target.get('name', resolved_id)} 获得状态: {cond_label}{src_text}{dur_text}"]
    if reason:
        msg_parts.insert(0, f"[{reason}]")
    if cdef:
        msg_parts.append(f"效果: {cdef.description}")

    update = _build_update(target, ctx)
    update["messages"] = [ToolMessage(content="\n".join(msg_parts), tool_call_id=tool_call_id)]
    return Command(update=update)


@tool
def remove_condition(
    target_id: str,
    condition_id: str,
    reason: str = "",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """移除目标单位身上的一个状态效果。

    Args:
        target_id: 目标单位 ID，或 "player" 表示当前玩家。
        condition_id: 要移除的状态 ID（如 "blinded"）。
        reason: 移除原因的简短描述。
    """
    target, ctx, resolved_id = _locate_target(state, target_id)
    if not target:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到目标 '{target_id}'。", tool_call_id=tool_call_id)
        ]})

    conds: list[dict] = target.get("conditions", [])
    if not has_condition(conds, condition_id):
        return Command(update={"messages": [
            ToolMessage(content=f"{target.get('name', resolved_id)} 当前没有 {condition_id} 状态。", tool_call_id=tool_call_id)
        ]})

    stand_up_line = ""
    if condition_id == "prone" and "_combat_dict" in ctx:
        movement_cost = target.get("speed", 30) / 2
        movement_left = target.get("movement_left", target.get("speed", 30))
        target["movement_left"] = max(0, round(movement_left - movement_cost, 2))
        stand_up_line = f"（站起消耗 {movement_cost:g} 尺移动力，剩余 {target['movement_left']:g} 尺）"

    remove_condition_by_id(target, condition_id)
    sync_ac_state(target)
    sync_movement_state(target)

    cdef = get_condition_def(condition_id)
    cond_label = cdef.name_cn if cdef else condition_id
    msg = f"{target.get('name', resolved_id)} 移除状态: {cond_label}"
    if stand_up_line:
        msg = f"{msg}\n{stand_up_line}"
    if reason:
        msg = f"[{reason}] {msg}"

    update = _build_update(target, ctx)
    update["messages"] = [ToolMessage(content=msg, tool_call_id=tool_call_id)]
    return Command(update=update)
