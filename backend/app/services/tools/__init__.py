"""工具注册入口 — 统一导出全部 LangGraph 工具"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_core.tools import BaseTool

from app.services.tools.dice_tools import request_dice_roll
from app.services.tools.character_tools import (
    choose_arcane_tradition,
    grant_xp,
    inspect_unit,
    level_up,
    load_character_profile,
    modify_character_state,
)
from app.services.tools.combat_tools import (
    attack_action,
    clear_dead_units,
    end_combat,
    next_turn,
    spawn_monsters,
    start_combat,
)
from app.services.tools.spell_tools import cast_spell
from app.services.tools.condition_tools import apply_condition, remove_condition
from app.services.tools.rag_tools import consult_rules_handbook
from app.services.tools.skill_tools import load_skill
from app.services.tools.space_tools import (
    create_plane_map,
    manage_space,
    measure_distance,
    move_unit,
    remove_unit,
    place_unit,
    query_units_in_radius,
    switch_plane_map,
)
from app.services.tools.monster_action_tools import use_monster_action

# 供外部模块直接引用的战斗计算函数
from app.services.tools._helpers import (
    advance_turn,
    apply_attack_damage,
    prepare_player_for_combat,
    resolve_single_attack,
    roll_attack_hit,
)

# 反应调度器
from app.services.tools.reactions import (
    get_available_reactions,
    build_interrupt_payload,
    execute_player_reaction,
    resolve_npc_reaction,
)

# 向后兼容旧名称
build_player_combatant = prepare_player_for_combat

ToolProfile = Literal["narrative", "combat"]

_NARRATIVE_TOOLS: tuple[BaseTool, ...] = (
    request_dice_roll,
    load_character_profile,
    modify_character_state,
    spawn_monsters,
    start_combat,
    end_combat,
    clear_dead_units,
    cast_spell,
    inspect_unit,
    consult_rules_handbook,
    manage_space,
    remove_unit,
)

_COMBAT_TOOLS: tuple[BaseTool, ...] = (
    request_dice_roll,
    modify_character_state,
    attack_action,
    use_monster_action,
    next_turn,
    end_combat,
    cast_spell,
    inspect_unit,
    consult_rules_handbook,
    manage_space,
    remove_unit,
)

_COMPATIBILITY_TOOLS: tuple[BaseTool, ...] = (
    load_skill,
    apply_condition,
    remove_condition,
    grant_xp,
    level_up,
    choose_arcane_tradition,
    create_plane_map,
    switch_plane_map,
    place_unit,
    move_unit,
    remove_unit,
    measure_distance,
    query_units_in_radius,
)

_ALL_TOOLS: tuple[BaseTool, ...] = _NARRATIVE_TOOLS + tuple(
    tool for tool in _COMBAT_TOOLS if tool not in _NARRATIVE_TOOLS
) + tuple(
    tool for tool in _COMPATIBILITY_TOOLS if tool not in _NARRATIVE_TOOLS and tool not in _COMBAT_TOOLS
)


# 模型只看 profile，ToolNode 仍保留全量工具以执行历史调用。
@lru_cache(maxsize=None)
def get_tool_profile(profile: ToolProfile) -> list[BaseTool]:
    if profile == "narrative":
        return list(_NARRATIVE_TOOLS)
    if profile == "combat":
        return list(_COMBAT_TOOLS)
    raise ValueError(f"Unknown tool profile: {profile}")


@lru_cache(maxsize=1)
def get_tools() -> list[BaseTool]:
    return list(_ALL_TOOLS)
