"""职业动作工具入口。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.services.class_actions import (
    ClassActionContext,
    available_class_actions,
    has_class_action,
    run_class_action,
)
from app.services.tools._helpers import (
    get_combatant,
    get_player_identity,
    is_player_reference,
    resolve_player_reference_id,
)

TARGETED_ACTION_IDS = {"trip_attack", "rally"}


def _state_value_to_dict(value) -> dict | None:
    """工具层统一把状态对象投影成普通字典，便于回写。"""
    if not value:
        return None
    return value.model_dump() if hasattr(value, "model_dump") else dict(value)


def _mapping_state_to_dict(value) -> dict:
    """场景单位可能来自 Pydantic 映射或普通字典，这里统一形态。"""
    if not value:
        return {}
    if hasattr(value, "items"):
        return {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in value.items()}
    return {}


def _resolve_action_actor(state: dict, player_dict: dict, target_id: str) -> tuple[dict | None, str, dict]:
    """职业动作默认由玩家使用，也允许显式指定战斗或场景友方。"""
    update: dict = {}
    target_id = resolve_player_reference_id(player_dict, target_id)
    if not target_id or is_player_reference(player_dict, target_id):
        return player_dict, "player", update

    combat_dict = _state_value_to_dict(state.get("combat"))
    if combat_dict:
        actor = get_combatant(combat_dict, player_dict, target_id)
        if actor:
            update["combat"] = combat_dict
            return actor, "combat", update

    scene_units = _mapping_state_to_dict(state.get("scene_units"))
    if target_id in scene_units:
        update["scene_units"] = scene_units
        return scene_units[target_id], "scene", update

    return None, "", update


def _write_actor_update(update: dict, actor: dict, actor_source: str, actor_id: str) -> dict:
    """根据动作使用者来源回写，保持玩家、场景友方和战斗友方一致。"""
    if actor_source == "player":
        update["player"] = actor
    elif actor_source == "combat" and "combat" in update:
        update["combat"]["participants"][actor_id] = actor
    elif actor_source == "scene" and "scene_units" in update:
        update["scene_units"][actor_id] = actor
    return update


def _resolve_action_target(state: dict, player_dict: dict, target_id: str, update: dict) -> tuple[dict | None, str]:
    """职业动作的目标只从战斗参与者或场景单位中解析，不覆盖动作使用者。"""
    target_id = resolve_player_reference_id(player_dict, target_id)
    combat_dict = update.get("combat") or _state_value_to_dict(state.get("combat"))
    if combat_dict:
        target = get_combatant(combat_dict, player_dict, target_id)
        if target:
            update["combat"] = combat_dict
            return target, "player" if target is player_dict else "combat"

    scene_units = update.get("scene_units") or _mapping_state_to_dict(state.get("scene_units"))
    if target_id in scene_units:
        update["scene_units"] = scene_units
        return scene_units[target_id], "scene"

    return None, ""


def _write_target_update(update: dict, target: dict, target_source: str, target_id: str) -> dict:
    """命中后战技可能修改敌方 HP，需要把目标写回其来源容器。"""
    if target_source == "player":
        update["player"] = target
    elif target_source == "combat" and "combat" in update:
        update["combat"]["participants"][target_id] = target
    elif target_source == "scene" and "scene_units" in update:
        update["scene_units"][target_id] = target
    return update


@tool
def use_class_action(
    action_id: str = "",
    target_id: str = "",
    payload: dict | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """使用主动职业动作；action_id 为空时列出当前角色可用职业动作。"""
    player_dict = _state_value_to_dict(state.get("player"))
    if not player_dict:
        return Command(update={"messages": [
            ToolMessage(content="玩家尚未加载角色卡。", tool_call_id=tool_call_id)
        ]})

    actor_target_id = "" if action_id in TARGETED_ACTION_IDS else target_id
    actor, actor_source, base_update = _resolve_action_actor(state, player_dict, actor_target_id)
    if not actor:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到职业动作使用者 '{target_id}'。", tool_call_id=tool_call_id)
        ]})

    actor_id = str(actor.get("id") or target_id or get_player_identity(player_dict)[0])
    actor_name = str(actor.get("name") or actor_id)
    actor["id"] = actor_id
    actor["name"] = actor_name

    usable = available_class_actions(actor)
    if not action_id:
        content = f"[职业动作] {actor_name} 可用: {', '.join(usable) or '无'}。"
        update = _write_actor_update(base_update, actor, actor_source, actor_id)
        update["messages"] = [ToolMessage(content=content, tool_call_id=tool_call_id)]
        return Command(update=update)

    if not has_class_action(action_id):
        content = f"未知职业动作: {action_id}。当前可用: {', '.join(usable) or '无'}。"
        return Command(update={"messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]})

    if action_id not in usable:
        content = f"{actor_name} 当前不能使用职业动作: {action_id}。可用: {', '.join(usable) or '无'}。"
        return Command(update={"messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]})

    action_target = None
    action_target_source = ""
    if action_id in TARGETED_ACTION_IDS:
        action_target, action_target_source = _resolve_action_target(state, player_dict, target_id, base_update)

    result = run_class_action(
        action_id,
        ClassActionContext(
            actor=actor,
            target=action_target,
            state=state,
            payload=payload or {},
        ),
    )
    update = {**base_update, **result.update}
    update = _write_actor_update(update, actor, actor_source, actor_id)
    if action_target:
        target_identity = str(action_target.get("id") or target_id)
        action_target["id"] = target_identity
        update = _write_target_update(update, action_target, action_target_source, target_identity)
    update["messages"] = [ToolMessage(content="\n".join(result.lines), tool_call_id=tool_call_id)]
    return Command(update=update)
