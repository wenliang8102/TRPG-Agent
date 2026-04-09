# 检定计算模块 — 签名不变，底层已切换到 d20
from typing import Literal
from app.graph.state import CheckState, RollResultState, AbilityBlock
from app.calculation.dice import roll_d20
from app.calculation.abilities import get_ability_modifier
from app.calculation.proficiency import calculate_proficiency_bonus


def perform_check(
    check: CheckState,
    abilities: AbilityBlock,
    level: int = 1,
    has_proficiency: bool = False,
    additional_modifiers: int = 0,
) -> RollResultState:
    """执行通用 D&D 能力检定"""
    ability = check.ability if hasattr(check, "ability") else check["ability"]
    ability_modifier = get_ability_modifier(abilities, ability)
    proficiency_bonus = calculate_proficiency_bonus(level) if has_proficiency else 0
    total_modifier = ability_modifier + proficiency_bonus + additional_modifiers

    advantage = check.advantage if hasattr(check, "advantage") else check.get("advantage", "normal")
    if advantage not in ("normal", "advantage", "disadvantage"):
        advantage = "normal"

    raw_roll = roll_d20(advantage)
    total = raw_roll + total_modifier
    dc = check.dc if hasattr(check, "dc") else check.get("dc", 10)

    return RollResultState(
        dice="1d20",
        raw=raw_roll,
        modifier=total_modifier,
        total=total,
        success=total >= dc,
    )

def perform_attack_check(
    attacker_abilities: AbilityBlock,
    level: int = 1,
    weapon_type: Literal["melee", "ranged", "finesse", "thrown"] = "melee",
    weapon_enhancement: int = 0,
    has_proficiency: bool = False,
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal",
) -> RollResultState:
    """攻击检定（根据武器类型自动选择使用属性）"""
    if weapon_type == "ranged":
        ability_modifier = get_ability_modifier(attacker_abilities, "dex")
        used_ability = "dex"
    elif weapon_type == "thrown":
        ability_modifier = get_ability_modifier(attacker_abilities, "str")
        used_ability = "str"
    elif weapon_type == "finesse":
        str_mod = get_ability_modifier(attacker_abilities, "str")
        dex_mod = get_ability_modifier(attacker_abilities, "dex")
        if str_mod >= dex_mod:
            ability_modifier, used_ability = str_mod, "str"
        else:
            ability_modifier, used_ability = dex_mod, "dex"
    else:
        ability_modifier = get_ability_modifier(attacker_abilities, "str")
        used_ability = "str"

    proficiency_bonus = calculate_proficiency_bonus(level) if has_proficiency else 0
    total_modifier = ability_modifier + proficiency_bonus + weapon_enhancement
    raw_roll = roll_d20(advantage)

    result = RollResultState(
        dice="1d20",
        raw=raw_roll,
        modifier=total_modifier,
        total=raw_roll + total_modifier,
        success=False,
    )
    # 附加使用的属性信息供伤害计算
    result.used_ability = used_ability
    return result

def perform_saving_throw(
    abilities: AbilityBlock,
    ability: Literal["str", "dex", "con", "int", "wis", "cha"],
    dc: int,
    level: int = 1,
    has_proficiency: bool = False,
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal",
) -> RollResultState:
    """豁免检定"""
    ability_modifier = get_ability_modifier(abilities, ability)
    proficiency_bonus = calculate_proficiency_bonus(level) if has_proficiency else 0
    total_modifier = ability_modifier + proficiency_bonus
    raw_roll = roll_d20(advantage)
    total = raw_roll + total_modifier

    return RollResultState(
        dice="1d20",
        raw=raw_roll,
        modifier=total_modifier,
        total=total,
        success=total >= dc,
    )


def perform_skill_check(
    abilities: AbilityBlock,
    skill: str,
    dc: int,
    level: int = 1,
    has_proficiency: bool = False,
    expertise: bool = False,
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal",
) -> RollResultState:
    """技能检定"""
    # 技能 → 依赖属性映射
    skill_abilities = {
        "acrobatics": "dex", "animal_handling": "wis", "arcana": "int",
        "athletics": "str", "deception": "cha", "history": "int",
        "insight": "wis", "intimidation": "cha", "investigation": "int",
        "medicine": "wis", "nature": "int", "perception": "wis",
        "performance": "cha", "persuasion": "cha", "religion": "int",
        "sleight_of_hand": "dex", "stealth": "dex", "survival": "wis",
    }

    ability = skill_abilities.get(skill.lower(), "dex")
    ability_modifier = get_ability_modifier(abilities, ability)

    base_proficiency = calculate_proficiency_bonus(level) if has_proficiency else 0
    final_proficiency = base_proficiency * 2 if expertise else base_proficiency
    total_modifier = ability_modifier + final_proficiency

    raw_roll = roll_d20(advantage)
    total = raw_roll + total_modifier

    return RollResultState(
        dice="1d20",
        raw=raw_roll,
        modifier=total_modifier,
        total=total,
        success=total >= dc,
    )


def calculate_passive_check(
    abilities: AbilityBlock,
    ability: Literal["str", "dex", "con", "int", "wis", "cha"],
    level: int = 1,
    has_proficiency: bool = False,
) -> int:
    """被动检定值: 10 + 能力修正 + 熟练加值"""
    ability_modifier = get_ability_modifier(abilities, ability)
    proficiency = calculate_proficiency_bonus(level) if has_proficiency else 0
    return 10 + ability_modifier + proficiency