# 怪物生成模块 — 基于 Open5e API
import uuid

import d20

from app.graph.state import (
    AbilityBlock, ModifierBlock, CombatantState,
)
from app.calculation.abilities import ability_to_modifier
from app.services.open5e_client import get_monster_template, MonsterTemplate


def spawn_combatants(slug: str, count: int = 1, side: str = "enemy") -> list[CombatantState]:
    """
    通过 Open5e slug（如 "goblin"）生成 count 个战斗单元。
    HP 使用 d20 库根据 hit_dice 投掷。
    """
    template: MonsterTemplate = get_monster_template(slug)

    combatants: list[CombatantState] = []
    for i in range(1, count + 1):
        name_suffix = f" {i}" if count > 1 else ""

        # 用 d20 投掷 HP
        try:
            max_hp = d20.roll(template.hit_dice).total
            max_hp = max(1, max_hp)
        except Exception:
            max_hp = template.hit_points

        abilities = AbilityBlock(
            str=template.strength, dex=template.dexterity, con=template.constitution,
            int=template.intelligence, wis=template.wisdom, cha=template.charisma,
        )
        modifiers = ModifierBlock(
            str=ability_to_modifier(template.strength),
            dex=ability_to_modifier(template.dexterity),
            con=ability_to_modifier(template.constitution),
            int=ability_to_modifier(template.intelligence),
            wis=ability_to_modifier(template.wisdom),
            cha=ability_to_modifier(template.charisma),
        )

        combatant = CombatantState(
            id=f"{slug}_{uuid.uuid4().hex[:6]}",
            name=f"{template.name}{name_suffix}",
            side=side,
            hp=max_hp,
            max_hp=max_hp,
            base_ac=template.armor_class,
            ac=template.armor_class,
            initiative=0,
            speed=template.speed_walk,
            conditions=[],
            abilities=abilities,
            modifiers=modifiers,
            proficiency_bonus=template.proficiency_bonus,
            attacks=template.attacks,
            action_available=True,
            bonus_action_available=True,
            reaction_available=True,
            movement_left=template.speed_walk,
        )
        combatants.append(combatant)

    return combatants
