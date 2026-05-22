"""职业特性框架入口。"""

from app.services.class_features.champion import CHAMPION_FEATURE_IDS, get_critical_threshold, improved_critical_threshold
from app.services.class_features.eldritch_knight import (
    ELDRITCH_KNIGHT_DEFAULT_CANTRIPS,
    ELDRITCH_KNIGHT_DEFAULT_SPELLS,
    ELDRITCH_KNIGHT_FEATURE_IDS,
    ELDRITCH_KNIGHT_SPELLCASTING_BY_LEVEL,
    sync_eldritch_knight_spellcasting,
)
from app.services.class_features.battle_master import (
    BATTLE_MASTER_FEATURE_IDS,
    BATTLE_MASTER_MANEUVER_SAVE_ABILITY,
    BATTLE_MASTER_MANEUVER_SAVE_DC_BONUS,
    BATTLE_MASTER_SUPERIORITY_DIE,
    BATTLE_MASTER_SUPERIORITY_DICE,
)
from app.services.class_features.spellcasting import grant_spellcasting, sync_spellcasting_fields

__all__ = [
    "BATTLE_MASTER_FEATURE_IDS",
    "BATTLE_MASTER_MANEUVER_SAVE_ABILITY",
    "BATTLE_MASTER_MANEUVER_SAVE_DC_BONUS",
    "BATTLE_MASTER_SUPERIORITY_DIE",
    "BATTLE_MASTER_SUPERIORITY_DICE",
    "CHAMPION_FEATURE_IDS",
    "ELDRITCH_KNIGHT_DEFAULT_CANTRIPS",
    "ELDRITCH_KNIGHT_DEFAULT_SPELLS",
    "ELDRITCH_KNIGHT_FEATURE_IDS",
    "ELDRITCH_KNIGHT_SPELLCASTING_BY_LEVEL",
    "get_critical_threshold",
    "grant_spellcasting",
    "improved_critical_threshold",
    "sync_spellcasting_fields",
    "sync_eldritch_knight_spellcasting",
]
