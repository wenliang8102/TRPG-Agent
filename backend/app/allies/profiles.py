"""友方角色模板；友方走角色型单位规则，由 Agent 控制行动。"""

from __future__ import annotations

from copy import deepcopy

from app.calculation.abilities import ability_to_modifier
from app.equipment.items import default_potion_inventory
from app.services.class_features import sync_spellcasting_fields


ALLY_PROFILES: dict[str, dict] = {
    "fighter_companion": {
        "id": "fighter_companion",
        "name": "格林",
        "role_class": "fighter",
        "level": 1,
        "hit_die": "1d10",
        "hit_dice_total": 1,
        "hit_dice_remaining": 1,
        "hp": 12,
        "max_hp": 12,
        "base_ac": 16,
        "ac": 16,
        "speed": 30,
        "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 10},
        "proficiency_bonus": 2,
        "skill_proficiencies": ["medicine", "athletics"],
        "weapons": [
            {"name": "Longsword"},
            {"name": "Light Crossbow"},
        ],
        "resources": {"second_wind_uses": 1},
        "resource_caps": {"second_wind_uses": 1},
        "known_spells": [],
        "known_cantrips": [],
        "class_features": ["fighting_style", "second_wind"],
        "fighting_style": "",
        "fighter_archetype": "",
        "behavior_profile": "格林是可靠沉稳的开局战士同伴，不是剧情 NPC 修达；保护玩家，优先贴近威胁者，盟友倒地时会尝试急救稳定。",
    },
    "sildar": {
        "id": "sildar",
        "name": "Sildar Hallwinter",
        "role_class": "fighter",
        "level": 3,
        "hp": 27,
        "max_hp": 27,
        "base_ac": 16,
        "ac": 16,
        "speed": 30,
        "abilities": {"str": 13, "dex": 10, "con": 12, "int": 10, "wis": 11, "cha": 10},
        "proficiency_bonus": 2,
        "skill_proficiencies": ["medicine", "athletics"],
        "weapons": [
            {"name": "Longsword"},
            {"name": "Heavy Crossbow"},
        ],
        "resources": {},
        "known_spells": [],
        "known_cantrips": [],
        "class_features": ["multiattack"],
        "behavior_profile": "保护玩家，优先攻击威胁玩家的敌人；重伤时后撤并请求治疗。",
    },
    "apprentice_wizard": {
        "id": "apprentice_wizard",
        "name": "Apprentice Wizard",
        "role_class": "wizard",
        "level": 2,
        "hp": 12,
        "max_hp": 12,
        "base_ac": 12,
        "ac": 12,
        "speed": 30,
        "abilities": {"str": 8, "dex": 14, "con": 12, "int": 16, "wis": 10, "cha": 10},
        "proficiency_bonus": 2,
        "skill_proficiencies": ["arcana", "medicine"],
        "weapons": [{"name": "Dagger"}],
        "resources": {"spell_slot_lv1": 3},
        "resource_caps": {"spell_slot_lv1": 3},
        "known_spells": ["magic_missile", "mage_armor", "shield"],
        "known_cantrips": ["fire_bolt", "ray_of_frost"],
        "spellcasting_ability": "int",
        "class_features": [],
        "behavior_profile": "保持距离；优先用戏法，关键时用魔法飞弹集火，受到命中时可用防护反应。",
    },
    "acolyte_healer": {
        "id": "acolyte_healer",
        "name": "Acolyte Healer",
        "role_class": "cleric",
        "level": 2,
        "hp": 13,
        "max_hp": 13,
        "base_ac": 13,
        "ac": 13,
        "speed": 30,
        "abilities": {"str": 10, "dex": 12, "con": 12, "int": 10, "wis": 15, "cha": 13},
        "proficiency_bonus": 2,
        "skill_proficiencies": ["medicine", "religion"],
        "weapons": [{"name": "Mace"}],
        "resources": {"spell_slot_lv1": 3},
        "resource_caps": {"spell_slot_lv1": 3},
        "known_spells": ["cure_wounds"],
        "known_cantrips": ["toll_the_dead"],
        "spellcasting_ability": "wis",
        "class_features": [],
        "behavior_profile": "保护濒危盟友；优先治疗倒地或重伤角色，其余时间保持安全距离。",
    },
}


def get_ally_profile(profile_id: str) -> dict:
    """按模板 ID 生成独立友方角色，避免场景单位共享同一份可变状态。"""
    key = str(profile_id).strip().lower()
    if key not in ALLY_PROFILES:
        available = ", ".join(sorted(ALLY_PROFILES))
        raise ValueError(f"未知友方模板 '{profile_id}'。可用模板: {available}")
    profile = deepcopy(ALLY_PROFILES[key])
    profile["modifiers"] = {
        ability: ability_to_modifier(score)
        for ability, score in profile.get("abilities", {}).items()
    }
    sync_spellcasting_fields(profile)
    profile.setdefault("conditions", [])
    profile.setdefault("inventory", default_potion_inventory())
    profile.setdefault("side", "ally")
    profile.setdefault("death_save_successes", 0)
    profile.setdefault("death_save_failures", 0)
    profile.setdefault("is_stable", False)
    profile.setdefault("is_dead", False)
    return profile
