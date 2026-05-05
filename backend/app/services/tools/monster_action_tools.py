"""怪物结构化动作工具。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.services.tools._helpers import get_all_combatants, get_combatant, get_condition_action_block_reason
from app.services.tools._helpers import build_pending_reaction_state
from app.services.tools.reactions import get_available_reactions
from app.services.tools.monster_action_resolvers import (
    available_action_labels,
    can_use_legacy_reaction_pause,
    consume_action_resource,
    get_monster_action,
    legacy_attack_to_action,
    roll_monster_attack_hit,
    roll_reaction_pause_attack,
    resolve_monster_action,
    validate_action_resource,
)


@tool
def use_monster_action(
    actor_id: str,
    target_ids: list[str] | None = None,
    action_id: str | None = None,
    advantage: str = "normal",
    target_point: dict[str, float] | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """执行怪物/NPC结构化动作，LLM 只负责选择动作，规则结算在工具内完成。

    Args:
        actor_id: 行动单位 ID。
        target_ids: 目标单位 ID 列表；多重攻击可传多个，范围能力可留空让空间系统展开。
        action_id: actions 中展示的动作 ID 或名称。
        advantage: 命中优劣势，normal / advantage / disadvantage。
        target_point: 点选范围法术或瞬移法术的目标坐标。
    """

    def _reject(msg: str) -> Command:
        return Command(update={"messages": [ToolMessage(content=f"[动作失败] {msg}", tool_call_id=tool_call_id)]})

    combat_raw = state.get("combat")
    if not combat_raw:
        return _reject("当前不在战斗中。")

    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    actor = get_combatant(combat_dict, player_dict, actor_id)
    if not actor:
        return _reject(f"找不到行动者 '{actor_id}'。")
    if combat_dict.get("current_actor_id") != actor_id:
        return _reject(f"现在不是 {actor.get('name', actor_id)} 的回合，当前行动者为 {combat_dict.get('current_actor_id')}。")
    if actor.get("hp", 0) <= 0:
        return _reject(f"{actor.get('name', actor_id)} 已经倒下，无法行动。")

    action = get_monster_action(actor, action_id)
    if action is None:
        legacy_action = legacy_attack_to_action(actor, action_id)
        if legacy_action is None:
            available = ", ".join(available_action_labels(actor)) or "无"
            return _reject(f"未知动作 '{action_id}'。可用动作: {available}。")
        action = legacy_action

    if block_reason := get_condition_action_block_reason(actor, action.action_type):
        return _reject(block_reason)
    if resource_error := validate_action_resource(actor, action):
        return _reject(resource_error)

    all_combatants = get_all_combatants(combat_dict, player_dict)
    target_ids = list(target_ids or [])
    if action.kind == "spell":
        from app.spells import get_spell_def

        spell_def = get_spell_def(action.spell_id)
        spell_range = str(spell_def.get("range", "")).lower() if spell_def else ""
        area_def = spell_def.get("area") if spell_def else None
        needs_explicit_target = not (
            spell_range.startswith("self")
            or (area_def and area_def.get("origin", "point") == "point" and target_point)
            or (action.spell_id == "misty_step" and target_point)
        )
    else:
        needs_explicit_target = True

    if action.kind not in {"special", "bonus_action", "reaction"} and action.target.kind in {"single", "multi"} and not target_ids and needs_explicit_target:
        return _reject(f"{action.name} 需要至少一个目标。")

    targets_by_id: dict[str, dict] = {}
    for target_id in target_ids:
        target = all_combatants.get(target_id)
        if not target:
            return _reject(f"找不到目标 '{target_id}'。")
        if target.get("hp", 0) <= 0:
            return _reject(f"目标 {target.get('name', target_id)} 已经倒下。")
        targets_by_id[target_id] = target

    # 范围能力可能由空间系统展开全场目标，因此 resolver 需要完整单位索引。
    if action.target.kind in {"cone", "radius"} or action.kind == "spell":
        targets_by_id = all_combatants

    if player_dict and actor.get("side") != player_dict.get("side", "player") and action.kind == "spell":
        from app.spells import get_spell_def

        spell_def = get_spell_def(action.spell_id)
        reaction_context = {
            "trigger_caster_id": actor.get("id", actor_id),
            "trigger_caster_name": actor.get("name", actor_id),
            "trigger_spell_name_cn": spell_def["name_cn"],
            "trigger_spell_level": max(action.slot_level, spell_def["level"]),
            "space": state.get("space"),
        }
        available_reactions = get_available_reactions(player_dict, "on_enemy_cast", reaction_context)
        if available_reactions:
            pending_reaction_msg = ToolMessage(
                content=(
                    f"{actor.get('name', actor_id)} 正在施放 {spell_def['name_cn']}，"
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
                    "pending_reaction": {
                        "type": "reaction_prompt",
                        "trigger": "on_enemy_cast",
                        "attacker_id": actor.get("id", actor_id),
                        "attacker_name": actor.get("name", actor_id),
                        "target_id": player_dict.get("id", ""),
                        "target_name": player_dict.get("name", ""),
                        "available_reactions": available_reactions,
                        "spell_action": action.model_dump(),
                        "target_ids": target_ids,
                        "target_point": target_point,
                        "space": state.get("space"),
                    },
                    "reaction_choice": None,
                }
            )

    if player_dict and target_ids and actor.get("side") != "player" and targets_by_id[target_ids[0]] is player_dict:
        # 对玩家命中的怪物攻击先暂停，确保护盾术能在伤害和命中后效果前改判。
        if can_use_legacy_reaction_pause(action):
            roll_info = roll_reaction_pause_attack(actor, player_dict, action, state, advantage)
            pending_attack_action = None
        elif action.kind == "attack":
            roll_info = roll_monster_attack_hit(actor, player_dict, action, state, advantage)
            pending_attack_action = action.model_dump()
        else:
            roll_info = None
            pending_attack_action = None

        if roll_info is not None:
            if roll_info.get("blocked"):
                return _reject(roll_info["block_reason"])
            if roll_info.get("hit"):
                reaction_context = {
                    "attacker": actor.get("name", actor_id),
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
                    pending_state = build_pending_reaction_state(actor, player_dict, roll_info, available_reactions)
                    if pending_attack_action:
                        pending_state["monster_attack_action"] = pending_attack_action
                    pending_reaction_msg = ToolMessage(
                        content=(
                            f"{actor.get('name', actor_id)} 的 {action.name} 命中了 {player_dict.get('name', target_ids[0])}，"
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
                            "pending_reaction": pending_state,
                            "reaction_choice": None,
                        }
                    )

    result = resolve_monster_action(
        actor,
        targets_by_id,
        target_ids,
        action,
        state,
        advantage=advantage,
        target_point=target_point,
    )
    if result.get("blocked"):
        return _reject("\n".join(result["lines"]))

    consume_action_resource(actor, action)

    update: dict = {
        "combat": combat_dict,
        "messages": [ToolMessage(content="\n".join(result["lines"]), tool_call_id=tool_call_id)],
    }
    if player_dict:
        update["player"] = player_dict
    if result.get("hp_changes"):
        update["hp_changes"] = result["hp_changes"]
    if result.get("space"):
        update["space"] = result["space"]
    if result.get("attack_roll"):
        update["messages"][0].additional_kwargs = {"attack_roll": result["attack_roll"]}

    return Command(update=update)
