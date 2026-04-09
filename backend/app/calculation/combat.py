# 战斗计算模块 — 基于 d20 库
from typing import Literal, Optional, Any

import d20

from app.graph.state import CombatantState, RollResultState, AbilityBlock
from app.calculation.dice import roll_expr


def resolve_attack(
    attacker: CombatantState,
    defender: CombatantState,
    attack_roll: RollResultState,
    attacker_abilities: Optional[AbilityBlock] = None,
    is_ranged: bool = False,
    weapon_damage: str = "1d8",
    weapon_enhancement: int = 0,
) -> dict[str, Any]:
    """解析攻击结果（遵循 D&D 5e 天然 1/20 铁规则）"""
    natural_roll = attack_roll.raw if hasattr(attack_roll, "raw") else attack_roll["raw"]

    if natural_roll == 1:
        hit, critical_hit = False, False
    elif natural_roll == 20:
        hit, critical_hit = True, True
    else:
        total = attack_roll.total if hasattr(attack_roll, "total") else attack_roll["total"]
        ac = defender.ac if hasattr(defender, "ac") else defender["ac"]
        hit = total >= ac
        critical_hit = False

    result: dict[str, Any] = {
        "hit": hit,
        "critical": critical_hit,
        "attack_roll": attack_roll,
        "defender_ac": defender.ac if hasattr(defender, "ac") else defender["ac"],
        "damage": 0,
        "damage_roll": None,
    }

    if hit:
        weapon_type = "ranged" if is_ranged else "melee"
        used_ability = attack_roll.get("used_ability") if isinstance(attack_roll, dict) else getattr(attack_roll, "used_ability", None)
        damage_info = calculate_damage(
            attacker=attacker,
            defender=defender,
            weapon_damage=weapon_damage,
            weapon_type=weapon_type,
            weapon_enhancement=weapon_enhancement,
            attacker_abilities=attacker_abilities,
            critical_hit=critical_hit,
            used_ability=used_ability,
        )
        result["damage"] = damage_info["total_damage"]
        result["damage_roll"] = damage_info["damage_roll"]

    return result


def calculate_damage(
    attacker: CombatantState,
    defender: CombatantState,
    weapon_damage: str = "1d8",
    weapon_type: Literal["melee", "ranged", "finesse", "thrown"] = "melee",
    weapon_enhancement: int = 0,
    attacker_abilities: Optional[AbilityBlock] = None,
    critical_hit: bool = False,
    used_ability: Literal["str", "dex"] | None = None,
) -> dict[str, Any]:
    """用 d20 库计算伤害（暴击时骰子数翻倍）"""
    from app.calculation.abilities import get_ability_modifier

    # 构建暴击伤害表达式：骰子数翻倍但固定修正不变
    if critical_hit:
        damage_expr = _double_dice(weapon_damage)
    else:
        damage_expr = weapon_damage

    damage_result = d20.roll(damage_expr)
    damage_roll = damage_result.total

    # 能力修正值
    ability_modifier = 0
    if attacker_abilities:
        if weapon_type == "finesse" and used_ability:
            ability_modifier = get_ability_modifier(attacker_abilities, used_ability)
        elif weapon_type == "ranged":
            ability_modifier = get_ability_modifier(attacker_abilities, "dex")
        else:
            ability_modifier = get_ability_modifier(attacker_abilities, "str")

    total_damage = damage_roll + ability_modifier + weapon_enhancement

    return {
        "damage_roll": damage_roll,
        "ability_modifier": ability_modifier,
        "weapon_enhancement": weapon_enhancement,
        "used_ability": used_ability,
        "total_damage": max(1, total_damage),
        "critical_hit": critical_hit,
        "weapon_damage": weapon_damage,
    }


def _double_dice(notation: str) -> str:
    """暴击时将骰子表达式中的骰子数翻倍，如 '2d6+3' → '4d6+3'"""
    import re
    return re.sub(r"(\d+)d(\d+)", lambda m: f"{int(m.group(1)) * 2}d{m.group(2)}", notation)


def calculate_ac(
    base_ac: int,
    dex_modifier: int,
    armor_type: Literal["none", "light", "medium", "heavy"] = "none",
    shield_bonus: int = 0,
    other_bonuses: int = 0,
) -> int:
    """计算总护甲等级（D&D 5e 护甲规则）"""
    if armor_type == "heavy":
        dex_contribution = 0
    elif armor_type == "medium":
        dex_contribution = min(dex_modifier, 2)
    else:
        dex_contribution = dex_modifier

    return base_ac + dex_contribution + shield_bonus + other_bonuses


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


def apply_health_change(combatant: CombatantState, change: int) -> CombatantState:
    """应用生命值变化（正数治疗，负数伤害），返回新的 CombatantState"""
    if hasattr(combatant, "model_copy"):
        # Pydantic model
        new = combatant.model_copy(deep=True)
        new.hp = max(0, min(new.hp + change, new.max_hp))
        return new
    # dict 兼容
    new = combatant.copy()
    new["hp"] = max(0, min(new["hp"] + change, new["max_hp"]))
    return new


def determine_advantage(
    attacker: CombatantState,
    defender: CombatantState,
    conditions: dict[str, bool] | None = None,
) -> Literal["normal", "advantage", "disadvantage"]:
    """根据战斗条件判断攻击是否有优势或劣势"""
    conditions = conditions or {}
    advantage_count = 0
    disadvantage_count = 0

    def _get_conditions(c: CombatantState) -> list[str]:
        return c.conditions if hasattr(c, "conditions") else c.get("conditions", [])

    d_conds = _get_conditions(defender)
    a_conds = _get_conditions(attacker)

    if "prone" in d_conds:
        advantage_count += 1
    if "restrained" in d_conds:
        advantage_count += 1
    if "blinded" in a_conds:
        disadvantage_count += 1
    if conditions.get("invisible_attacker"):
        advantage_count += 1
    if conditions.get("hidden_attacker"):
        advantage_count += 1

    if advantage_count > disadvantage_count:
        return "advantage"
    if disadvantage_count > advantage_count:
        return "disadvantage"
    return "normal"