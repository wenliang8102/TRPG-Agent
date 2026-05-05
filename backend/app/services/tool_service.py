"""向后兼容垫片 — 真正的实现已拆分到 app.services.tools 子包中。
所有旧的 ``from app.services.tool_service import X`` 写法依然可用。"""

from app.services.tools import (  # noqa: F401
    get_tools,
    get_tool_profile,
    resolve_single_attack,
    advance_turn,
    prepare_player_for_combat,
    build_player_combatant,
)
from app.services.tools._helpers import apply_hp_change as _apply_hp_change  # noqa: F401
from app.services.tools.combat_tools import (  # noqa: F401
    attack_action,
    clear_dead_units,
    end_combat,
    next_turn,
    spawn_monsters,
    start_combat,
)
from app.services.tools.character_tools import (  # noqa: F401
    choose_arcane_tradition,
    grant_xp,
    inspect_unit,
    level_up,
    load_character_profile,
    modify_character_state,
)
from app.services.tools.monster_action_tools import use_monster_action  # noqa: F401
from app.services.tools.dice_tools import request_dice_roll  # noqa: F401
from app.services.tools.spell_tools import cast_spell  # noqa: F401
from app.services.tools.condition_tools import apply_condition, remove_condition  # noqa: F401
from app.services.tools.rag_tools import consult_rules_handbook  # noqa: F401
from app.services.tools.skill_tools import load_skill  # noqa: F401
from app.services.tools.space_tools import (  # noqa: F401
    create_plane_map,
    manage_space,
    measure_distance,
    move_unit,
    remove_unit,
    place_unit,
    query_units_in_radius,
    switch_plane_map,
)

# 旧名称兼容
_build_player_combatant = prepare_player_for_combat
