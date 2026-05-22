"""战斗大师战技定义与校验。"""

from __future__ import annotations

import d20

from app.services.class_actions.types import ClassActionContext, ClassActionResult

BATTLE_MASTER_ACTION_IDS: tuple[str, ...] = (
    "choose_maneuvers",
    "trip_attack",
    "precision_attack",
    "menacing_attack",
    "pushing_attack",
    "riposte",
    "rally",
)

BATTLE_MASTER_MANEUVERS: dict[str, str] = {
    "trip_attack": "摔绊攻击",
    "precision_attack": "精准攻击",
    "menacing_attack": "恐吓攻击",
    "pushing_attack": "推撞攻击",
    "riposte": "反击",
    "rally": "鼓舞",
}


def valid_maneuver_ids() -> tuple[str, ...]:
    """集中暴露可选战技 ID，避免工具层散落硬编码列表。"""
    return tuple(BATTLE_MASTER_MANEUVERS)


def validate_maneuver_selection(actor: dict, maneuvers: list[str]) -> list[str]:
    """校验战斗大师战技选择，返回错误文本列表。"""
    errors: list[str] = []
    known_limit = int(actor.get("maneuvers_known_count", 0) or 0)
    valid_ids = set(valid_maneuver_ids())

    if actor.get("fighter_archetype") != "battle_master":
        errors.append("只有战斗大师范型才能选择战技。")
    if "combat_superiority" not in actor.get("class_features", []):
        errors.append("只有拥有卓越战技的战斗大师才能选择战技。")
    if known_limit <= 0:
        errors.append("当前角色没有可用的战技已知数量。")
    if not maneuvers:
        errors.append("至少需要选择一个战技。")
    if len(maneuvers) > known_limit:
        errors.append(f"最多只能选择 {known_limit} 个战技。")
    if len(set(maneuvers)) != len(maneuvers):
        errors.append("战技不能重复选择。")

    invalid = sorted(set(maneuvers) - valid_ids)
    if invalid:
        errors.append(f"未知战技: {', '.join(invalid)}。")

    return errors


def choose_maneuvers(context: ClassActionContext) -> ClassActionResult:
    """只记录战斗大师已知战技，战技效果留给后续战斗触发点实现。"""
    actor = context.actor
    actor_name = actor.get("name", "角色")
    maneuvers = list((context.payload or {})["maneuvers"])
    errors = validate_maneuver_selection(actor, maneuvers)
    if errors:
        return ClassActionResult(lines=[f"{actor_name} 不能选择这些战技：", *errors], update={})

    actor["maneuvers"] = maneuvers
    maneuver_names = [BATTLE_MASTER_MANEUVERS[maneuver_id] for maneuver_id in maneuvers]
    return ClassActionResult(
        lines=[
            f"{actor_name} 已记录战斗大师战技: {', '.join(maneuvers)}。",
            f"战技名称: {'、'.join(maneuver_names)}。",
        ],
        update={},
    )


def use_trip_attack(context: ClassActionContext) -> ClassActionResult:
    """摔绊攻击先作为命中后手动结算，避免过早侵入主攻击流程。"""
    actor = context.actor
    target = context.target
    payload = context.payload or {}
    actor_name = actor.get("name", "角色")
    target_name = target.get("name", "目标") if target else "目标"
    resources = actor.setdefault("resources", {})
    uses = int(resources.get("superiority_dice", 0) or 0)

    if actor.get("fighter_archetype") != "battle_master":
        return ClassActionResult(lines=[f"{actor_name} 不是战斗大师，不能使用摔绊攻击。"], update={})
    if "trip_attack" not in actor.get("maneuvers", []):
        return ClassActionResult(lines=[f"{actor_name} 尚未选择摔绊攻击。"], update={})
    if not payload.get("attack_hit"):
        return ClassActionResult(lines=["摔绊攻击必须在武器攻击命中后使用。"], update={})
    if not target:
        return ClassActionResult(lines=["摔绊攻击需要指定一个目标。"], update={})
    if uses <= 0:
        return ClassActionResult(lines=[f"{actor_name} 的卓越骰已用尽。"], update={})

    superiority_die = str(actor.get("superiority_die") or "1d8")
    roll = d20.roll(superiority_die)
    old_hp = int(target.get("hp", 0) or 0)
    max_hp = int(target.get("max_hp", old_hp) or old_hp)
    new_hp = max(0, old_hp - roll.total)
    resources["superiority_dice"] = uses - 1
    target["hp"] = new_hp

    damage_type = str(payload.get("damage_type") or "weapon")
    save_dc = int(actor.get("maneuver_save_dc", 0) or 0)
    lines = [
        f"{actor_name} 使用摔绊攻击，消耗 1 枚卓越骰。",
        f"{target_name} 受到额外 {roll.total} 点 {damage_type} 伤害（{roll}）。",
        f"HP: {old_hp} → {new_hp} / {max_hp}",
    ]
    if save_dc:
        lines.append(f"{target_name} 需要进行 DC {save_dc} 的力量豁免；失败则倒地。")
    else:
        lines.append(f"{target_name} 需要进行力量豁免；失败则倒地。")
    lines.append(f"superiority_dice: {uses} → {uses - 1}")

    return ClassActionResult(
        lines=lines,
        update={"hp_changes": [{
            "id": target.get("id", target_name),
            "name": target_name,
            "old_hp": old_hp,
            "new_hp": new_hp,
            "max_hp": max_hp,
        }]},
    )


def use_rally(context: ClassActionContext) -> ClassActionResult:
    """鼓舞是独立附赠动作：消耗卓越骰，为能听见或看见你的盟友提供临时 HP。"""
    from app.services.tools._helpers import consume_action_resource, has_action_resource

    actor = context.actor
    target = context.target
    actor_name = actor.get("name", "角色")
    target_name = target.get("name", "目标") if target else "目标"
    state = context.state or {}
    combat = state.get("combat")
    resources = actor.setdefault("resources", {})
    uses = int(resources.get("superiority_dice", 0) or 0)

    if actor.get("fighter_archetype") != "battle_master":
        return ClassActionResult(lines=[f"{actor_name} 不是战斗大师，不能使用鼓舞。"], update={})
    if "rally" not in actor.get("maneuvers", []):
        return ClassActionResult(lines=[f"{actor_name} 尚未选择鼓舞。"], update={})
    if not target:
        return ClassActionResult(lines=["鼓舞需要指定一个盟友目标。"], update={})
    if combat and not has_action_resource(actor, "bonus_action"):
        return ClassActionResult(lines=[f"{actor_name} 本回合的附赠动作已用尽，不能使用鼓舞。"], update={})
    if uses <= 0:
        return ClassActionResult(lines=[f"{actor_name} 的卓越骰已用尽。"], update={})

    superiority_die = str(actor.get("superiority_die") or "1d8")
    roll = d20.roll(superiority_die)
    cha_mod = int(actor.get("modifiers", {}).get("cha", 0) or 0)
    temp_hp_gain = max(0, roll.total + cha_mod)
    old_temp_hp = int(target.get("temp_hp", 0) or 0)
    new_temp_hp = max(old_temp_hp, temp_hp_gain)

    resources["superiority_dice"] = uses - 1
    target["temp_hp"] = new_temp_hp
    if combat:
        consume_action_resource(actor, "bonus_action")

    return ClassActionResult(
        lines=[
            f"{actor_name} 使用鼓舞，消耗 1 枚卓越骰。",
            f"{target_name} 获得临时 HP: {old_temp_hp} → {new_temp_hp}（{roll} + CHA {cha_mod:+d}）。",
            f"superiority_dice: {uses} → {uses - 1}",
        ],
        update={},
    )
