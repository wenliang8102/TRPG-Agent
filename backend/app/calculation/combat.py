# 战斗计算模块 — 基于 d20 库
from typing import Literal, Any

import d20

from app.graph.state import CombatantState, RollResultState
from app.calculation.dice import roll_expr


def roll_initiative(
    dex_modifier: int,
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal",
) -> RollResultState:
    """先攻检定，使用 d20 库"""
    result = roll_expr("1d20", advantage)
    raw = result.total
    total = raw + dex_modifier

    return RollResultState(
        dice="1d20",
        raw=raw,
        modifier=dex_modifier,
        total=total,
        success=False,
    )


def next_combat_turn(current_round: int, current_combatant_index: int, total_combatants: int) -> tuple[int, int]:
    """计算下一个行动者索引和回合数"""
    next_index = (current_combatant_index + 1) % total_combatants
    next_round = current_round + 1 if next_index == 0 else current_round
    return next_round, next_index


def check_combatant_status(combatant: CombatantState) -> dict[str, Any]:
    """检查战斗单位生命状态"""
    hp = combatant.hp if hasattr(combatant, "hp") else combatant["hp"]
    max_hp = combatant.max_hp if hasattr(combatant, "max_hp") else combatant["max_hp"]

    return {
        "alive": hp > 0,
        "bloodied": hp <= max_hp // 2,
        "unconscious": hp <= 0,
        "current_hp": hp,
        "max_hp": max_hp,
        "hp_percentage": (hp / max_hp) * 100 if max_hp > 0 else 0,
    }