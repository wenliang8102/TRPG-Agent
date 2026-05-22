"""奥法骑士职业特性。"""

from __future__ import annotations

from app.services.class_features.spellcasting import grant_spellcasting


ELDRITCH_KNIGHT_FEATURE_IDS: tuple[str, ...] = ("eldritch_knight_spellcasting", "weapon_bond")
ELDRITCH_KNIGHT_DEFAULT_CANTRIPS: tuple[str, ...] = ("fire_bolt", "ray_of_frost")
ELDRITCH_KNIGHT_DEFAULT_SPELLS: tuple[str, ...] = ("shield", "magic_missile", "burning_hands")
ELDRITCH_KNIGHT_LEVEL_4_DEFAULT_SPELLS: tuple[str, ...] = (
    "shield",
    "magic_missile",
    "burning_hands",
    "thunderwave",
)

ELDRITCH_KNIGHT_SPELLCASTING_BY_LEVEL: dict[int, dict] = {
    3: {
        "cantrips_known": 2,
        "spells_known": 3,
        "cantrips": ELDRITCH_KNIGHT_DEFAULT_CANTRIPS,
        "spells": ELDRITCH_KNIGHT_DEFAULT_SPELLS,
        "spell_slots": {"spell_slot_lv1": 2},
    },
    4: {
        "cantrips_known": 2,
        "spells_known": 4,
        "cantrips": ELDRITCH_KNIGHT_DEFAULT_CANTRIPS,
        "spells": ELDRITCH_KNIGHT_LEVEL_4_DEFAULT_SPELLS,
        "spell_slots": {"spell_slot_lv1": 3},
    },
    5: {
        "cantrips_known": 2,
        "spells_known": 4,
        "cantrips": ELDRITCH_KNIGHT_DEFAULT_CANTRIPS,
        "spells": ELDRITCH_KNIGHT_LEVEL_4_DEFAULT_SPELLS,
        "spell_slots": {"spell_slot_lv1": 3},
    },
}


def sync_eldritch_knight_spellcasting(target: dict) -> list[str]:
    """按奥法骑士 3-5 级表同步施法字段，避免升级路径和选范型路径分叉。"""
    if target.get("fighter_archetype") != "eldritch_knight":
        return []

    level = int(target.get("level", 1))
    table = ELDRITCH_KNIGHT_SPELLCASTING_BY_LEVEL.get(level)
    if not table:
        return []

    grant_spellcasting(
        target,
        ability="int",
        cantrips=table["cantrips"],
        spells=table["spells"],
        spell_slots=table["spell_slots"],
    )
    target["eldritch_knight_cantrips_known"] = table["cantrips_known"]
    target["eldritch_knight_spells_known"] = table["spells_known"]
    target.setdefault("bonded_weapons", [])
    target["bonded_weapon_limit"] = 2

    return [
        "  奥法骑士施法表同步: "
        f"戏法 {table['cantrips_known']}，已知法术 {table['spells_known']}，"
        f"1 环法术位 {table['spell_slots']['spell_slot_lv1']}"
    ]
