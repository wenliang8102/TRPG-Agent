"""职业动作框架入口。"""

from app.services.class_actions.battle_master import (
    BATTLE_MASTER_ACTION_IDS,
    BATTLE_MASTER_MANEUVERS,
    choose_maneuvers,
    use_rally,
    use_trip_attack,
    valid_maneuver_ids,
    validate_maneuver_selection,
)
from app.services.class_actions.fighter import FIGHTER_ACTION_IDS, use_action_surge, use_second_wind
from app.services.class_actions.wizard import WIZARD_ACTION_IDS, use_arcane_recovery
from app.services.class_actions.registry import (
    available_class_actions,
    has_class_action,
    register_class_action,
    run_class_action,
)
from app.services.class_actions.types import ClassActionContext, ClassActionResult

register_class_action("second_wind", use_second_wind, required_features=("second_wind",))
register_class_action("action_surge", use_action_surge, required_features=("action_surge",))
register_class_action("choose_maneuvers", choose_maneuvers, required_features=("combat_superiority",))
register_class_action("trip_attack", use_trip_attack, required_features=("combat_superiority",))
register_class_action("rally", use_rally, required_features=("combat_superiority",))
register_class_action("arcane_recovery", use_arcane_recovery, required_features=("arcane_recovery",))

__all__ = [
    "BATTLE_MASTER_ACTION_IDS",
    "BATTLE_MASTER_MANEUVERS",
    "ClassActionContext",
    "ClassActionResult",
    "FIGHTER_ACTION_IDS",
    "WIZARD_ACTION_IDS",
    "available_class_actions",
    "choose_maneuvers",
    "has_class_action",
    "register_class_action",
    "run_class_action",
    "use_action_surge",
    "use_arcane_recovery",
    "use_rally",
    "use_second_wind",
    "use_trip_attack",
    "valid_maneuver_ids",
    "validate_maneuver_selection",
]
