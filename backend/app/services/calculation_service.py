# 数值计算服务 - 整合所有D&D计算模块
from typing import Literal, Optional, Dict, Any
from app.graph.state import (
    PlayerState, CheckState, RollResultState, CombatantState, AbilityBlock
)
from app.calculation.dice import roll_dice, roll_d20, roll_with_notation
from app.calculation.abilities import (
    ability_to_modifier, calculate_modifiers, get_ability_modifier,
    calculate_passive_perception, validate_ability_scores, increase_ability_score
)
from app.calculation.proficiency import (
    calculate_proficiency_bonus, calculate_total_proficiencies,
    get_saving_throw_proficiencies, get_skill_proficiencies
)
from app.calculation.checks import (
    perform_check, perform_attack_check, perform_saving_throw,
    perform_skill_check, calculate_passive_check
)
from app.calculation.combat import (
    resolve_attack, calculate_damage, calculate_ac, roll_initiative,
    next_combat_turn, check_combatant_status, apply_health_change,
    determine_advantage
)


class CalculationService:
    """
    D&D 5e 数值计算服务
    提供完整的TRPG数值计算功能
    """

    # ======================
    # 骰子相关功能
    # ======================

    def roll(self, dice_notation: str) -> RollResultState:
        """
        投掷骰子表达式
        例如: "1d20", "2d6+3", "1d8-1"
        """
        return roll_with_notation(dice_notation)

    def roll_d20(self, advantage: Literal["normal", "advantage", "disadvantage"] = "normal") -> int:
        """
        投掷d20，支持优势/劣势
        """
        from app.calculation.dice import roll_d20 as dice_roll_d20
        return dice_roll_d20(advantage)

    # ======================
    # 角色能力值相关
    # ======================

    def calculate_ability_modifiers(self, abilities: AbilityBlock) -> Dict[str, int]:
        """
        计算所有能力值的修正值
        """
        return calculate_modifiers(abilities)

    def get_modifier(self, ability_score: int) -> int:
        """
        获取单个能力值的修正值
        """
        return ability_to_modifier(ability_score)

    def validate_character_abilities(self, abilities: AbilityBlock) -> bool:
        """
        验证角色能力值是否有效
        """
        return validate_ability_scores(abilities)

    def calculate_passive_perception(self, player: PlayerState) -> int:
        """
        计算角色的被动感知
        """
        level = player.get("level", 1)
        abilities = player.get("abilities", {})
        role_class = player.get("role_class", "")

        proficiency_bonus = calculate_proficiency_bonus(level)
        # 根据职业判断是否有感知熟练
        has_proficiency = self._has_perception_proficiency(role_class)

        return calculate_passive_perception(abilities, proficiency_bonus, has_proficiency)

    def _has_perception_proficiency(self, role_class: str) -> bool:
        """
        判断职业是否有感知技能熟练
        """
        perception_classes = ["ranger", "druid", "cleric", "paladin", "rogue"]
        return role_class.lower() in perception_classes

    # ======================
    # 熟练加值相关
    # ======================

    def get_proficiency_bonus(self, level: int) -> int:
        """
        根据等级获取熟练加值
        """
        return calculate_proficiency_bonus(level)

    def get_class_proficiencies(self, role_class: str, level: int) -> Dict[str, Any]:
        """
        获取职业相关的熟练项信息
        """
        return {
            "proficiency_bonus": calculate_proficiency_bonus(level),
            "total_proficiencies": calculate_total_proficiencies(level, role_class),
            "saving_throws": get_saving_throw_proficiencies(role_class),
            "available_skills": get_skill_proficiencies(role_class)
        }

    # ======================
    # 检定相关功能
    # ======================

    def perform_ability_check(
        self,
        check: CheckState,
        player: PlayerState,
        has_proficiency: bool = False,
        additional_modifiers: int = 0
    ) -> RollResultState:
        """
        执行能力检定
        """
        abilities = player.get("abilities", {})
        level = player.get("level", 1)
        return perform_check(
            check=check,
            abilities=abilities,
            level=level,
            has_proficiency=has_proficiency,
            additional_modifiers=additional_modifiers
        )

    def perform_skill_check(
        self,
        skill: str,
        dc: int,
        player: PlayerState,
        has_proficiency: bool = False,
        expertise: bool = False,
        advantage: Literal["normal", "advantage", "disadvantage"] = "normal"
    ) -> RollResultState:
        """
        执行技能检定
        """
        abilities = player.get("abilities", {})
        level = player.get("level", 1)

        return perform_skill_check(
            abilities=abilities,
            skill=skill,
            dc=dc,
            level=level,
            has_proficiency=has_proficiency,
            expertise=expertise,
            advantage=advantage
        )

    def perform_saving_throw(
        self,
        ability: Literal["str", "dex", "con", "int", "wis", "cha"],
        dc: int,
        player: PlayerState,
        has_proficiency: bool = False,
        advantage: Literal["normal", "advantage", "disadvantage"] = "normal"
    ) -> RollResultState:
        """
        执行豁免检定
        """
        abilities = player.get("abilities", {})
        level = player.get("level", 1)

        return perform_saving_throw(
            abilities=abilities,
            ability=ability,
            dc=dc,
            level=level,
            has_proficiency=has_proficiency,
            advantage=advantage
        )

    # ======================
    # 战斗相关功能
    # ======================

    def perform_attack(
        self,
        attacker: CombatantState,
        defender: CombatantState,
        player: PlayerState,
        is_ranged: bool = False,
        weapon_damage: str = "1d8"
    ) -> Dict[str, Any]:
        """
        执行完整的攻击流程
        """
        abilities = player.get("abilities", {})
        level = player.get("level", 1)
        proficiency_bonus = calculate_proficiency_bonus(level)

        # 确定使用哪个能力值修正（力量或敏捷）
        if is_ranged:
            weapon_modifier = get_ability_modifier(abilities, "dex")
        else:
            weapon_modifier = get_ability_modifier(abilities, "str")

        # 投掷攻击检定
        # 注意：这里简化处理，实际应用中需要更详细的武器类型信息
        weapon_type = "ranged" if is_ranged else "melee"
        attack_roll = perform_attack_check(
            attacker_abilities=abilities,
            level=level,
            weapon_type=weapon_type,
            weapon_enhancement=0,  # 默认无魔法增强
            has_proficiency=True  # 假设角色对武器熟练
        )

        # 解析攻击结果
        result = resolve_attack(
            attacker=attacker,
            defender=defender,
            attack_roll=attack_roll,
            attacker_abilities=abilities,
            is_ranged=is_ranged,
            weapon_damage=weapon_damage,
            weapon_enhancement=0
        )

        # 如果有伤害，应用到目标
        if result["hit"] and result["damage"] > 0:
            result["updated_defender"] = apply_health_change(defender, -result["damage"])

        return result

    def roll_initiative(self, player: PlayerState) -> RollResultState:
        """
        投掷角色的先攻
        """
        abilities = player.get("abilities", {})
        dex_modifier = get_ability_modifier(abilities, "dex")
        return roll_initiative(dex_modifier)

    def calculate_armor_class(self, player: PlayerState) -> int:
        """
        计算角色的护甲等级
        """
        abilities = player.get("abilities", {})
        base_ac = player.get("ac", 10)
        dex_modifier = get_ability_modifier(abilities, "dex")
        armor_type = player.get("armor_type", "none")  # 获取护甲类型

        # 这里可以根据装备计算盾牌和其他加值
        shield_bonus = player.get("shield_bonus", 0)
        other_bonuses = player.get("other_ac_bonuses", 0)

        return calculate_ac(base_ac, dex_modifier, armor_type, shield_bonus, other_bonuses)

    def get_combatant_status(self, combatant: CombatantState) -> Dict[str, Any]:
        """
        获取战斗单位的详细状态
        """
        return check_combatant_status(combatant)

    def heal_combatant(self, combatant: CombatantState, healing_amount: int) -> CombatantState:
        """
        治疗战斗单位
        """
        return apply_health_change(combatant, healing_amount)

    def damage_combatant(self, combatant: CombatantState, damage_amount: int) -> CombatantState:
        """
        对战斗单位造成伤害
        """
        return apply_health_change(combatant, -damage_amount)

    # ======================
    # 工具方法
    # ======================

    def parse_dice_notation(self, notation: str) -> Dict[str, int]:
        """解析骰子表达式（通过 d20 库执行后提取结构）"""
        import d20
        result = d20.roll(notation)
        return {
            "expression": notation,
            "total": result.total,
        }

    def quick_roll(self, dice_type: int = 20, modifier: int = 0) -> Dict[str, Any]:
        """
        快速投掷单个骰子
        """
        dice_notation = f"1d{dice_type}"
        if modifier != 0:
            dice_notation += f"{'+' if modifier > 0 else ''}{modifier}"

        result = roll_with_notation(dice_notation)
        return {
            "dice": dice_notation,
            "result": result.total,
            "raw": result.raw,
            "modifier": result.modifier
        }