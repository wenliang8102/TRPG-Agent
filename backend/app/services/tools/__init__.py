"""工具注册入口 — 统一导出全部 LangGraph 工具"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain_core.tools import BaseTool

from app.services.tools.dice_tools import request_dice_roll
from app.services.tools.character_tools import (
    inspect_unit,
    load_character_profile,
    modify_character_state,
)
from app.services.tools.combat_tools import (
    attack_action,
    delegate_combat_turn,
    end_combat,
    manage_scene_units,
    next_turn,
    prepare_combat_end,
    prepare_combat_start,
    start_combat,
)
from app.services.tools.item_tools import manage_inventory
from app.services.tools.spell_tools import cast_spell
from app.services.tools.rag_tools import consult_rules_handbook
from app.services.tools.rest_tools import take_rest
from app.services.tools.space_tools import (
    manage_space,
)
from app.services.tools.monster_action_tools import use_monster_action
from app.services.tools.class_action_tools import use_class_action
from app.services.tools.adventure_tools import (
    claim_adventure_reward,
)

# 供外部模块直接引用的战斗计算函数
from app.services.tools._helpers import (
    advance_turn,
    apply_attack_damage,
    prepare_character_for_combat,
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

ToolProfile = Literal["narrative", "combat"]

_NARRATIVE_TOOLS: tuple[BaseTool, ...] = (
    request_dice_roll,
    load_character_profile,
    modify_character_state,
    manage_scene_units,
    prepare_combat_start,
    cast_spell,
    manage_inventory,
    use_class_action,
    inspect_unit,
    consult_rules_handbook,
    take_rest,
    manage_space,
    claim_adventure_reward,
)

_COMBAT_TOOLS: tuple[BaseTool, ...] = (
    request_dice_roll,
    modify_character_state,
    delegate_combat_turn,
    prepare_combat_end,
    manage_scene_units,
    next_turn,
    inspect_unit,
    consult_rules_handbook,
    manage_space,
)

_RUNTIME_ONLY_TOOLS: tuple[BaseTool, ...] = (
    start_combat,
    end_combat,
    attack_action,
    use_monster_action,
)

_ALL_TOOLS: tuple[BaseTool, ...] = _NARRATIVE_TOOLS + tuple(
    tool for tool in _COMBAT_TOOLS if tool not in _NARRATIVE_TOOLS
) + tuple(
    tool for tool in _RUNTIME_ONLY_TOOLS if tool not in _NARRATIVE_TOOLS and tool not in _COMBAT_TOOLS
)


# 模型只看 profile，ToolNode 额外保留 workflow/执行器仍会落到的底层战斗工具。
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
