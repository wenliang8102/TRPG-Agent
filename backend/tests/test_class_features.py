"""职业特性框架测试。"""

from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.class_features import (
    get_critical_threshold,
    grant_spellcasting,
    sync_spellcasting_fields,
    sync_eldritch_knight_spellcasting,
)


def test_get_critical_threshold_respects_champion_only():
    """重击阈值只应被勇士职业特性下调到 19。"""
    assert get_critical_threshold({"class_features": []}) == 20
    assert get_critical_threshold({"class_features": ["improved_critical"]}) == 19
    assert get_critical_threshold({"class_features": ["combat_superiority", "student_of_war"]}) == 20


def test_grant_spellcasting_writes_shared_spell_fields():
    """通用施法 helper 应把法术字段一次性写回角色卡。"""
    player = {"modifiers": {"int": 3}, "resources": {}, "resource_caps": {}}

    grant_spellcasting(
        player,
        ability="int",
        cantrips=["fire_bolt", "ray_of_frost"],
        spells=["shield", "magic_missile", "burning_hands"],
        spell_slots={"spell_slot_lv1": 2},
    )

    assert player["spellcasting_ability"] == "int"
    assert player["resources"]["spell_slot_lv1"] == 2
    assert player["resource_caps"]["spell_slot_lv1"] == 2
    assert player["spell_save_dc"] == 13
    assert player["spell_attack_bonus"] == 5
    assert "fire_bolt" in player["known_cantrips"]
    assert "shield" in player["known_spells"]


def test_sync_spellcasting_fields_refreshes_template_derived_values():
    """模板角色只声明施法能力和已知法术，派生 DC/攻击值由 helper 统一刷新。"""
    player = {
        "spellcasting_ability": "wis",
        "known_cantrips": ["toll_the_dead"],
        "known_spells": ["cure_wounds"],
        "modifiers": {"wis": 2},
        "proficiency_bonus": 2,
    }

    sync_spellcasting_fields(player)

    assert player["spell_save_dc"] == 12
    assert player["spell_attack_bonus"] == 4
    assert player["known_cantrips"] == ["toll_the_dead"]
    assert player["known_spells"] == ["cure_wounds"]


def test_sync_eldritch_knight_spellcasting_uses_level_3_to_5_table():
    """奥法骑士施法同步应按 3-5 级职业表刷新法术位和已知法术数量。"""
    player = {
        "level": 4,
        "fighter_archetype": "eldritch_knight",
        "modifiers": {"int": 1},
        "proficiency_bonus": 2,
        "resources": {},
        "resource_caps": {},
    }

    lines = sync_eldritch_knight_spellcasting(player)

    assert player["eldritch_knight_cantrips_known"] == 2
    assert player["eldritch_knight_spells_known"] == 4
    assert player["resources"]["spell_slot_lv1"] == 3
    assert player["resource_caps"]["spell_slot_lv1"] == 3
    assert player["spell_save_dc"] == 11
    assert player["spell_attack_bonus"] == 3
    assert "thunderwave" in player["known_spells"]
    assert "奥法骑士施法表同步" in lines[0]



