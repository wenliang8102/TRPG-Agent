"""《凡戴尔的失落矿坑》第一批结构化怪物动作。"""

from __future__ import annotations

from app.monsters.actions import recharge_5_6
from app.monsters.models import DamagePart, EffectSpec, MonsterAction, SaveSpec, TargetSpec


def _damage(dice: str, damage_type: str) -> DamagePart:
    """让动作表只呈现规则数据，减少样板字段噪音。"""
    return DamagePart(dice=dice, damage_type=damage_type)


def _condition(
    condition_id: str,
    *,
    duration: int | None = None,
    save_ends: dict | None = None,
    extra: dict | None = None,
    apply_to: str = "target",
) -> EffectSpec:
    """统一声明命中后附加状态，生命周期交给状态系统处理。"""
    return EffectSpec(kind="condition", apply_to=apply_to, condition_id=condition_id, duration=duration, save_ends=save_ends, extra=extra or {})


def _damage_effect(dice: str, damage_type: str, *, half: bool = False) -> EffectSpec:
    """统一声明豁免后的伤害效果，避免 resolver 依赖怪物名称。"""
    return EffectSpec(
        kind="damage",
        damage=_damage(dice, damage_type),
        damage_multiplier="half" if half else "full",
    )


def _reduce_max_hp_by_damage() -> EffectSpec:
    """吸取生命这类效果要依赖本次实际造成伤害，单独用效果类型表达。"""
    return EffectSpec(kind="reduce_max_hp_by_damage")


def _spell(spell_id: str, *, slot_level: int = 0, action_type: str = "action") -> MonsterAction:
    """怪物施法动作只引用 spell registry，不在动作表复制法术规则。"""
    return MonsterAction(
        id=spell_id,
        name=spell_id.replace("_", " ").title(),
        kind="spell",
        action_type=action_type,
        spell_id=spell_id,
        slot_level=slot_level,
    )


LOST_MINE_ACTIONS: dict[str, list[MonsterAction]] = {
    "goblin": [
        MonsterAction(
            id="scimitar",
            name="Scimitar",
            kind="attack",
            attack_bonus=4,
            damage=[_damage("1d6+2", "slashing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="shortbow",
            name="Shortbow",
            kind="attack",
            attack_bonus=4,
            damage=[_damage("1d6+2", "piercing")],
            normal_range_feet=80,
            long_range_feet=320,
        ),
        MonsterAction(
            id="nimble_escape",
            name="Nimble Escape",
            kind="bonus_action",
            action_type="bonus_action",
            description="Disengage or Hide as a bonus action.",
        ),
    ],
    "wolf": [
        MonsterAction(
            id="bite",
            name="Bite",
            kind="attack",
            attack_bonus=4,
            damage=[_damage("2d4+2", "piercing")],
            reach_feet=5,
            on_hit=[
                EffectSpec(
                    kind="description",
                    text="If the target is a creature, it must succeed on a DC 11 STR save or fall prone.",
                )
            ],
            save=SaveSpec(
                ability="str",
                dc=11,
                failure=[_condition("prone")],
            ),
        )
    ],
    "ghoul": [
        MonsterAction(
            id="bite",
            name="Bite",
            kind="attack",
            attack_bonus=2,
            damage=[_damage("2d6+2", "piercing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="claws",
            name="Claws",
            kind="attack",
            attack_bonus=4,
            damage=[_damage("2d4+2", "slashing")],
            reach_feet=5,
            save=SaveSpec(
                ability="con",
                dc=10,
                failure=[_condition("paralyzed", save_ends={"ability": "con", "dc": 10})],
            ),
        ),
    ],
    "giant-spider": [
        MonsterAction(
            id="bite",
            name="Bite",
            kind="attack",
            attack_bonus=5,
            damage=[_damage("1d8+3", "piercing")],
            reach_feet=5,
            save=SaveSpec(
                ability="con",
                dc=11,
                success=[_damage_effect("2d8", "poison", half=True)],
                failure=[_damage_effect("2d8", "poison")],
            ),
        ),
        MonsterAction(
            id="web",
            name="Web",
            kind="attack",
            attack_bonus=5,
            damage=[],
            normal_range_feet=30,
            long_range_feet=60,
            recharge=recharge_5_6(),
            on_hit=[_condition("restrained")],
        ),
    ],
    "redbrand-ruffian": [
        MonsterAction(
            id="shortsword",
            name="Shortsword",
            kind="attack",
            attack_bonus=4,
            damage=[_damage("1d6+2", "piercing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="multiattack",
            name="Multiattack",
            kind="multiattack",
            sequence=["shortsword", "shortsword"],
        ),
    ],
    "owlbear": [
        MonsterAction(
            id="beak",
            name="Beak",
            kind="attack",
            attack_bonus=7,
            damage=[_damage("1d10+5", "piercing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="claws",
            name="Claws",
            kind="attack",
            attack_bonus=7,
            damage=[_damage("2d8+5", "slashing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="multiattack",
            name="Multiattack",
            kind="multiattack",
            sequence=["beak", "claws"],
        ),
    ],
    "zombie": [
        MonsterAction(
            id="slam",
            name="Slam",
            kind="attack",
            attack_bonus=3,
            damage=[_damage("1d6+1", "bludgeoning")],
            reach_feet=5,
        )
    ],
    "young-green-dragon": [
        MonsterAction(
            id="bite",
            name="Bite",
            kind="attack",
            attack_bonus=7,
            damage=[_damage("2d10+4", "piercing"), _damage("2d6", "poison")],
            reach_feet=10,
        ),
        MonsterAction(
            id="claw",
            name="Claw",
            kind="attack",
            attack_bonus=7,
            damage=[_damage("2d6+4", "slashing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="poison_breath",
            name="Poison Breath",
            kind="area_save",
            damage=[_damage("12d6", "poison")],
            target=TargetSpec(kind="cone", length_feet=30, angle_deg=53.13),
            save=SaveSpec(ability="con", dc=14),
            recharge=recharge_5_6(),
        ),
        MonsterAction(
            id="multiattack",
            name="Multiattack",
            kind="multiattack",
            sequence=["bite", "claw", "claw"],
        ),
    ],
    "bugbear": [
        MonsterAction(
            id="morningstar",
            name="Morningstar",
            kind="attack",
            attack_bonus=4,
            damage=[_damage("2d8+2", "piercing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="javelin",
            name="Javelin",
            kind="attack",
            attack_bonus=4,
            damage=[_damage("2d6+2", "piercing")],
            reach_feet=5,
            normal_range_feet=30,
            long_range_feet=120,
        ),
    ],
    "hobgoblin": [
        MonsterAction(
            id="longsword",
            name="Longsword",
            kind="attack",
            attack_bonus=3,
            damage=[_damage("1d8+1", "slashing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="longbow",
            name="Longbow",
            kind="attack",
            attack_bonus=3,
            damage=[_damage("1d8+1", "piercing")],
            normal_range_feet=150,
            long_range_feet=600,
        ),
    ],
    "grick": [
        MonsterAction(
            id="tentacles",
            name="Tentacles",
            kind="attack",
            attack_bonus=4,
            damage=[_damage("2d6+2", "slashing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="beak",
            name="Beak",
            kind="attack",
            attack_bonus=4,
            damage=[_damage("1d6+2", "piercing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="multiattack",
            name="Multiattack",
            kind="multiattack",
            sequence=["tentacles", "beak"],
            sequence_mode="on_previous_hit",
        ),
    ],
    "nothic": [
        MonsterAction(
            id="claws",
            name="Claws",
            kind="attack",
            attack_bonus=4,
            damage=[_damage("1d6+3", "slashing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="rotting_gaze",
            name="Rotting Gaze",
            kind="save_effect",
            normal_range_feet=30,
            save=SaveSpec(
                ability="con",
                dc=12,
                failure=[_damage_effect("3d6", "necrotic")],
            ),
        ),
        MonsterAction(
            id="weird_insight",
            name="Weird Insight",
            kind="special",
            target=TargetSpec(kind="single"),
            description="Target contests CHA (Deception) against the nothic's WIS (Insight); on success the nothic learns one fact or secret.",
        ),
    ],
    "stirge": [
        MonsterAction(
            id="blood_drain",
            name="Blood Drain",
            kind="attack",
            attack_bonus=5,
            damage=[_damage("1d4+3", "piercing")],
            reach_feet=5,
            on_hit=[
                _condition(
                    "attached",
                    apply_to="self",
                    extra={"target_id": "$target_id", "damage": {"dice": "1d4+3", "damage_type": "piercing"}},
                )
            ],
        ),
        MonsterAction(
            id="detach",
            name="Detach",
            kind="special",
            description="The stirge detaches from its current target.",
        ),
    ],
    "wraith": [
        MonsterAction(
            id="life_drain",
            name="Life Drain",
            kind="attack",
            attack_bonus=6,
            damage=[_damage("4d8+3", "necrotic")],
            reach_feet=5,
            save=SaveSpec(
                ability="con",
                dc=14,
                failure=[_reduce_max_hp_by_damage()],
            ),
        ),
    ],
    "evil-mage": [
        _spell("shocking_grasp"),
        _spell("magic_missile", slot_level=1),
        _spell("charm_person", slot_level=1),
        _spell("hold_person", slot_level=2),
        _spell("misty_step", slot_level=2, action_type="bonus_action"),
        MonsterAction(
            id="quarterstaff",
            name="Quarterstaff",
            kind="attack",
            attack_bonus=1,
            damage=[_damage("1d8-1", "bludgeoning")],
            reach_feet=5,
        ),
    ],
    "flameskull": [
        _spell("magic_missile", slot_level=1),
        _spell("shield", slot_level=1, action_type="reaction"),
        _spell("blur", slot_level=2),
        _spell("flaming_sphere", slot_level=2),
        MonsterAction(
            id="move_flaming_sphere",
            name="Move Flaming Sphere",
            kind="bonus_action",
            action_type="bonus_action",
            target=TargetSpec(kind="radius"),
            description="Move the flaming sphere up to 30 feet and ram nearby creatures.",
        ),
        _spell("fireball", slot_level=3),
        MonsterAction(
            id="fire_ray",
            name="Fire Ray",
            kind="attack",
            attack_bonus=5,
            damage=[_damage("3d6", "fire")],
            normal_range_feet=30,
        ),
        MonsterAction(
            id="multiattack",
            name="Multiattack",
            kind="multiattack",
            sequence=["fire_ray", "fire_ray"],
        ),
    ],
    "nezznar": [
        _spell("ray_of_frost"),
        _spell("shocking_grasp"),
        _spell("mage_armor", slot_level=1),
        _spell("magic_missile", slot_level=1),
        _spell("shield", slot_level=1, action_type="reaction"),
        _spell("darkness", slot_level=2),
        _spell("faerie_fire", slot_level=1),
        _spell("invisibility", slot_level=2),
        _spell("suggestion", slot_level=2),
        MonsterAction(
            id="spider_staff",
            name="Spider Staff",
            kind="attack",
            attack_bonus=1,
            damage=[_damage("1d6-1", "bludgeoning"), _damage("1d6", "poison")],
            reach_feet=5,
        ),
    ],
    "spectator": [
        MonsterAction(
            id="bite",
            name="Bite",
            kind="attack",
            attack_bonus=1,
            damage=[_damage("1d6-1", "piercing")],
            reach_feet=5,
        ),
        MonsterAction(
            id="confusion_ray",
            name="Confusion Ray",
            kind="save_effect",
            normal_range_feet=90,
            save=SaveSpec(
                ability="wis",
                dc=13,
                failure=[_condition("confused", duration=1)],
            ),
        ),
        MonsterAction(
            id="paralyzing_ray",
            name="Paralyzing Ray",
            kind="save_effect",
            normal_range_feet=90,
            save=SaveSpec(
                ability="con",
                dc=13,
                failure=[_condition("paralyzed", save_ends={"ability": "con", "dc": 13})],
            ),
        ),
        MonsterAction(
            id="fear_ray",
            name="Fear Ray",
            kind="save_effect",
            normal_range_feet=90,
            save=SaveSpec(
                ability="wis",
                dc=13,
                failure=[_condition("frightened", save_ends={"ability": "wis", "dc": 13})],
            ),
        ),
        MonsterAction(
            id="wounding_ray",
            name="Wounding Ray",
            kind="save_effect",
            normal_range_feet=90,
            save=SaveSpec(
                ability="con",
                dc=13,
                success=[_damage_effect("3d10", "necrotic", half=True)],
                failure=[_damage_effect("3d10", "necrotic")],
            ),
        ),
        MonsterAction(
            id="eye_rays",
            name="Eye Rays",
            kind="multiattack",
            sequence=["confusion_ray", "wounding_ray"],
        ),
    ],
}


LOST_MINE_TRAITS: dict[str, list[str]] = {
    "zombie": ["undead_fortitude"],
    "bugbear": ["brute", "surprise_attack"],
    "hobgoblin": ["martial_advantage"],
    "spectator": ["spell_reflection"],
}


def get_lost_mine_actions(slug: str) -> list[MonsterAction]:
    """按 Open5e slug 返回本地结构化动作，未知怪物保持空增量。"""
    return [action.model_copy(deep=True) for action in LOST_MINE_ACTIONS.get(slug, [])]


def get_lost_mine_traits(slug: str) -> list[str]:
    """本地怪物特质表只放需要 resolver hook 的能力。"""
    return list(LOST_MINE_TRAITS.get(slug, []))
