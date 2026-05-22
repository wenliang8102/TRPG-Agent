"""通用施法字段写入助手。"""

from __future__ import annotations


def _append_unique(target: list[str], values: list[str] | tuple[str, ...]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def grant_spellcasting(
    player_dict: dict,
    *,
    ability: str,
    cantrips: list[str] | tuple[str, ...] = (),
    spells: list[str] | tuple[str, ...] = (),
    spell_slots: dict[str, int] | None = None,
    proficiency_bonus: int = 2,
) -> None:
    """把通用施法字段一次性写回角色卡。"""
    player_dict["spellcasting_ability"] = ability

    known_cantrips = player_dict.setdefault("known_cantrips", [])
    known_spells = player_dict.setdefault("known_spells", [])
    _append_unique(known_cantrips, cantrips)
    _append_unique(known_spells, spells)

    if spell_slots:
        resources = player_dict.setdefault("resources", {})
        resource_caps = player_dict.setdefault("resource_caps", {})
        for slot_key, count in spell_slots.items():
            resources[slot_key] = count
            resource_caps[slot_key] = count

    ability_mod = player_dict.get("modifiers", {}).get(ability, 0)
    prof = int(player_dict.get("proficiency_bonus", proficiency_bonus))
    player_dict["spell_save_dc"] = 8 + prof + ability_mod
    player_dict["spell_attack_bonus"] = prof + ability_mod


def sync_spellcasting_fields(character: dict, *, proficiency_bonus: int = 2) -> None:
    """基于角色已有施法字段刷新派生值，避免模板和升级路径各算一遍。"""
    ability = str(character.get("spellcasting_ability") or "").strip()
    if not ability:
        return

    grant_spellcasting(
        character,
        ability=ability,
        cantrips=tuple(character.get("known_cantrips", ())),
        spells=tuple(character.get("known_spells", ())),
        spell_slots=None,
        proficiency_bonus=proficiency_bonus,
    )
