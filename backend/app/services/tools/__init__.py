"""工具注册入口 — 统一导出全部 LangGraph 工具"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.tools import BaseTool

from app.services.tools.dice_tools import request_dice_roll, weather
from app.services.tools.character_tools import (
    inspect_unit,
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

# 供外部模块直接引用的战斗计算函数
from app.services.tools._helpers import (
    advance_turn,
    prepare_player_for_combat,
    resolve_single_attack,
)

# 向后兼容旧名称
build_player_combatant = prepare_player_for_combat


@lru_cache(maxsize=1)
def get_tools() -> list[BaseTool]:
    return [
        weather,
        request_dice_roll,
        load_character_profile,
        modify_character_state,
        spawn_monsters,
        start_combat,
        attack_action,
        next_turn,
        end_combat,
        clear_dead_units,
        cast_spell,
        inspect_unit,
        apply_condition,
        remove_condition,
    ]
