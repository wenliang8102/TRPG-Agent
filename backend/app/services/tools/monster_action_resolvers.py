"""结构化怪物动作通用 resolver。"""

from __future__ import annotations

import re

import d20

from app.conditions._base import build_condition_extra, create_condition, upsert_condition
from app.graph.state import Point2D
from app.monsters.actions import action_to_attack_info
from app.monsters.models import DamagePart, EffectSpec, MonsterAction, TargetSpec
from app.services.tools._helpers import (
    apply_damage_to_target,
    available_attack_names,
    build_attack_roll_event_payload,
    remove_action_breaking_conditions,
    roll_actor_save,
    roll_attack_hit,
    validate_attack_distance,
)
from app.space.geometry import build_space_state, cone_area, units_in_geometry, units_in_radius


def get_monster_action(actor: dict, action_id: str | None) -> MonsterAction | None:
    """按动作 ID 或名称选择结构化动作；未指定时取第一个 action 类型动作。"""
    actions = [MonsterAction.model_validate(action) for action in actor.get("actions", [])]
    if action_id:
        requested = action_id.lower()
        return next((a for a in actions if a.id.lower() == requested or a.name.lower() == requested), None)
    return next((a for a in actions if a.action_type == "action"), actions[0] if actions else None)


def available_action_labels(actor: dict) -> list[str]:
    """列出模型可调用动作，错误提示与战斗 brief 共用同一种展示信息。"""
    actions = [MonsterAction.model_validate(action) for action in actor.get("actions", [])]
    return [f"{action.name}({action.id})" for action in actions] or available_attack_names(actor)


def legacy_attack_to_action(actor: dict, attack_name: str | None) -> MonsterAction | None:
    """没有结构化动作时，把旧 AttackInfo 包成 attack 动作继续执行。"""
    attacks = actor.get("attacks", [])
    chosen = None
    if attack_name:
        requested = attack_name.lower()
        chosen = next(
            (
                attack for attack in attacks
                if attack.get("name", "").lower() == requested or str(attack.get("id", "")).lower() == requested
            ),
            None,
        )
    elif attacks:
        chosen = attacks[0]

    if chosen is None:
        return None

    return MonsterAction(
        id=str(chosen.get("id") or _slugify(chosen["name"])),
        name=chosen["name"],
        kind="attack",
        attack_bonus=chosen.get("attack_bonus", 0),
        damage=[DamagePart(dice=chosen.get("damage_dice", "1d4"), damage_type=chosen.get("damage_type", "bludgeoning"))],
        reach_feet=chosen.get("reach_feet"),
        normal_range_feet=chosen.get("normal_range_feet"),
        long_range_feet=chosen.get("long_range_feet"),
    )


def validate_action_resource(actor: dict, action: MonsterAction) -> str | None:
    """校验动作经济和 recharge，资源消耗由外层工具统一提交。"""
    resource_key = {
        "action": "action_available",
        "bonus_action": "bonus_action_available",
        "reaction": "reaction_available",
    }[action.action_type]
    if not actor.get(resource_key, True):
        label = {"action": "动作", "bonus_action": "附赠动作", "reaction": "反应"}[action.action_type]
        return f"{actor.get('name', '?')} 本回合的{label}已用尽。"
    if action.recharge and not actor.setdefault("action_recharges", {}).get(action.id, True):
        return f"{action.name} 尚未 recharge，当前不可用。"
    return None


def consume_action_resource(actor: dict, action: MonsterAction) -> None:
    """只在动作真正进入 resolver 后消耗资源，多重攻击也只消耗一次。"""
    resource_key = {
        "action": "action_available",
        "bonus_action": "bonus_action_available",
        "reaction": "reaction_available",
    }[action.action_type]
    actor[resource_key] = False
    if action.recharge:
        actor.setdefault("action_recharges", {})[action.id] = False


def roll_action_recharges(actor: dict) -> list[str]:
    """回合开始时为带 recharge 的动作掷恢复骰。"""
    lines: list[str] = []
    actions = [MonsterAction.model_validate(action) for action in actor.get("actions", [])]
    recharge_state = actor.setdefault("action_recharges", {})
    for action in actions:
        if not action.recharge or recharge_state.get(action.id, True):
            continue
        result = d20.roll(action.recharge.die)
        if result.total >= action.recharge.min_roll:
            recharge_state[action.id] = True
            lines.append(f"  [{actor.get('name', '?')}] {action.name} recharge 成功({result})。")
        else:
            lines.append(f"  [{actor.get('name', '?')}] {action.name} recharge 失败({result})。")
    return lines


def resolve_monster_action(
    actor: dict,
    targets_by_id: dict[str, dict],
    target_ids: list[str],
    action: MonsterAction,
    state: dict,
    *,
    advantage: str = "normal",
    target_point: dict[str, float] | None = None,
) -> dict:
    """结构化动作总入口，返回战报、HP 变化和可选攻击骰载荷。"""
    if action.kind == "attack":
        return _resolve_attack_action(actor, targets_by_id[target_ids[0]], action, state, advantage=advantage)
    if action.kind == "multiattack":
        return _resolve_multiattack(actor, targets_by_id, target_ids, action, state, advantage=advantage)
    if action.kind == "area_save":
        resolved_targets = _resolve_area_targets(actor, targets_by_id, target_ids, action.target, state)
        return _resolve_area_save(actor, resolved_targets, action)
    if action.kind == "save_effect":
        return _resolve_save_action(actor, targets_by_id[target_ids[0]], action, state)
    if action.kind == "spell":
        return _resolve_spell_action(actor, targets_by_id, target_ids, action, state, target_point=target_point)
    if action.kind in {"bonus_action", "special", "reaction"}:
        if action.id == "move_flaming_sphere":
            return _resolve_move_flaming_sphere(actor, targets_by_id, state, target_point)
        if action.id == "nimble_escape":
            return _resolve_nimble_escape(actor)
        if action.id == "detach":
            actor["conditions"] = [condition for condition in actor.get("conditions", []) if condition.get("id") != "attached"]
            return {"lines": [f"{actor.get('name', '?')} 主动脱离。"], "hp_changes": [], "attack_roll": None}
        if action.id == "weird_insight" and target_ids:
            return _resolve_weird_insight(actor, targets_by_id[target_ids[0]])
        line = action.description or f"{actor.get('name', '?')} 使用 {action.name}。"
        return {"lines": [line], "hp_changes": [], "attack_roll": None}
    return {"lines": [f"{action.name} 暂未接入 resolver。"], "hp_changes": [], "attack_roll": None}


def can_use_legacy_reaction_pause(action: MonsterAction) -> bool:
    """只有等价旧普通攻击才复用现有 reaction pause，复杂动作直接由结构化 resolver 结算。"""
    return action.kind == "attack" and len(action.damage) <= 1 and not action.on_hit and action.save is None


def roll_reaction_pause_attack(actor: dict, target: dict, action: MonsterAction, state: dict, advantage: str) -> dict:
    """为护盾术等既有反应链生成旧 AttackInfo 形态的命中快照。"""
    return roll_monster_attack_hit(actor, target, action, state, advantage)


def roll_monster_attack_hit(actor: dict, target: dict, action: MonsterAction, state: dict, advantage: str) -> dict:
    """结构化怪物攻击第一阶段：只掷命中，给护盾术等反应留下改判窗口。"""
    attack_info = action_to_attack_info(action).model_dump()
    actor_attacks = actor.get("attacks", [])
    actor["attacks"] = [attack_info]
    try:
        if distance_error := validate_attack_distance(state.get("space"), actor["id"], target["id"], attack_info):
            return {"blocked": True, "block_reason": distance_error, "emit_dice_roll": False}
        return roll_attack_hit(actor, target, action.name, advantage, state)
    finally:
        actor["attacks"] = actor_attacks


def _resolve_attack_action(
    actor: dict,
    target: dict,
    action: MonsterAction,
    state: dict,
    *,
    advantage: str = "normal",
) -> dict:
    """普通攻击：复用旧命中 resolver，再补多段伤害、豁免和命中状态。"""
    roll_info = roll_monster_attack_hit(actor, target, action, state, advantage)
    return resolve_monster_attack_from_roll(actor, target, action, state, roll_info)


def resolve_monster_attack_from_roll(
    actor: dict,
    target: dict,
    action: MonsterAction,
    state: dict,
    roll_info: dict,
) -> dict:
    """结构化怪物攻击第二阶段：沿用已固定的命中快照继续结算伤害和命中后效果。"""

    if roll_info.get("blocked"):
        return {"lines": [roll_info["block_reason"]], "hp_changes": [], "attack_roll": None, "blocked": True}

    lines: list[str] = list(roll_info["lines"])
    hp_changes: list[dict] = []
    if roll_info["hit"]:
        damage_result = _apply_action_damage(actor, target, action.damage, roll_info["crit"], state)
        lines.extend(damage_result["lines"])
        hp_changes.extend(damage_result["hp_changes"])
        for effect in action.on_hit:
            lines.extend(_apply_effect(actor, target, effect, base_damage=damage_result["total_damage"], hp_changes=hp_changes))
        if action.save:
            lines.extend(_resolve_save_effect(actor, target, action.save, hp_changes, damage_result["total_damage"]))
    else:
        natural = roll_info["natural"]
        lines.append("未命中！" if natural != 1 else "严重失误！攻击完全落空！")

    return {
        "lines": lines,
        "hp_changes": hp_changes,
        "attack_roll": build_attack_roll_event_payload(roll_info),
        "hit": roll_info["hit"],
    }


def _resolve_multiattack(
    actor: dict,
    targets_by_id: dict[str, dict],
    target_ids: list[str],
    action: MonsterAction,
    state: dict,
    *,
    advantage: str = "normal",
) -> dict:
    """多重攻击只消耗一个 action，子攻击复用普通攻击 resolver。"""
    lines = [f"{actor.get('name', '?')} 使用 {action.name}。"]
    hp_changes: list[dict] = []
    attack_roll = None
    actions = {a.id: a for a in [MonsterAction.model_validate(raw) for raw in actor.get("actions", [])]}

    for index, child_id in enumerate(action.sequence):
        child = actions[child_id]
        target_id = target_ids[min(index, len(target_ids) - 1)]
        if child.kind == "attack":
            result = _resolve_attack_action(actor, targets_by_id[target_id], child, state, advantage=advantage)
        elif child.kind == "save_effect":
            result = _resolve_save_action(actor, targets_by_id[target_id], child, state)
        else:
            result = resolve_monster_action(actor, targets_by_id, [target_id], child, state, advantage=advantage)
        lines.extend(result["lines"])
        hp_changes.extend(result["hp_changes"])
        attack_roll = attack_roll or result.get("attack_roll")
        if action.sequence_mode == "on_previous_hit" and not result.get("hit"):
            lines.append(f"{child.name} 未命中，后续连击未触发。")
            break

    return {"lines": lines, "hp_changes": hp_changes, "attack_roll": attack_roll}


def _resolve_save_action(actor: dict, target: dict, action: MonsterAction, state: dict) -> dict:
    """单体豁免动作，用于腐烂凝视等非攻击能力。"""
    if action.normal_range_feet:
        attack_like = {
            "name": action.name,
            "reach_feet": action.reach_feet or 5,
            "normal_range_feet": action.normal_range_feet,
            "long_range_feet": action.long_range_feet,
        }
        if distance_error := validate_attack_distance(state.get("space"), actor["id"], target["id"], attack_like):
            return {"lines": [distance_error], "hp_changes": [], "attack_roll": None, "blocked": True}

    hp_changes: list[dict] = []
    lines = [f"{actor.get('name', '?')} 使用 {action.name} 指向 {target.get('name', '?')}。"]
    lines.extend(_resolve_save_effect(actor, target, action.save, hp_changes, 0))
    return {"lines": lines, "hp_changes": hp_changes, "attack_roll": None}


def _resolve_spell_action(
    actor: dict,
    targets_by_id: dict[str, dict],
    target_ids: list[str],
    action: MonsterAction,
    state: dict,
    *,
    target_point: dict[str, float] | None = None,
) -> dict:
    """怪物施法复用法术注册表，怪物动作只提供 spell_id 和施法环阶。"""
    from app.services.tools.spell_tools import (
        _break_concentration,
        _cantrip_dice_count,
        _move_caster_by_spell,
        _resolve_area_target_ids,
    )
    from app.spells import get_spell_def, get_spell_module
    from app.spells._base import get_spell_range_feet
    from app.space.geometry import validate_point_distance, validate_unit_distance

    spell_mod = get_spell_module(action.spell_id)
    spell_def = get_spell_def(action.spell_id)
    if not spell_mod or not spell_def:
        return {"lines": [f"未知怪物法术: {action.spell_id}。"], "hp_changes": [], "attack_roll": None, "blocked": True}

    point = Point2D(**target_point) if target_point else None
    slot_level = action.slot_level or spell_def["level"]
    area_def = spell_def.get("area")
    explicit_target_ids = list(target_ids)
    spell_range = get_spell_range_feet(spell_def)

    if area_def and area_def.get("origin", "point") == "point" and point:
        if spell_range is not None:
            distance_error, space_state = validate_point_distance(
                state.get("space"),
                actor["id"],
                point,
                spell_range,
                action_label=spell_def["name_cn"],
            )
            if distance_error:
                return {"lines": [distance_error], "hp_changes": [], "attack_roll": None, "blocked": True}
        else:
            _, space_state = validate_point_distance(state.get("space"), actor["id"], point, 0, action_label=spell_def["name_cn"])
        if not space_state or not space_state.maps:
            return {"lines": [f"{spell_def['name_cn']} 需要已启用的平面空间来解析目标点范围。"], "hp_changes": [], "attack_roll": None, "blocked": True}

    if area_def:
        try:
            auto_target_ids = _resolve_area_target_ids(area_def, state, actor["id"], target_ids, point)
        except KeyError as exc:
            return {"lines": [f"范围法术缺少空间落点：{exc.args[0]}。"], "hp_changes": [], "attack_roll": None, "blocked": True}
        if auto_target_ids is not None:
            target_ids = list(dict.fromkeys([*target_ids, *auto_target_ids]))

    if spell_range is not None and not point:
        range_target_ids = list(target_ids)
        if area_def and area_def.get("origin") == "self":
            range_target_ids = []
        elif area_def and area_def.get("origin") == "target":
            range_target_ids = explicit_target_ids[:1]
        for target_id in range_target_ids:
            if target_id == actor["id"]:
                continue
            if distance_error := validate_unit_distance(
                state.get("space"),
                actor["id"],
                target_id,
                spell_range,
                action_label=spell_def["name_cn"],
            ):
                return {"lines": [distance_error], "hp_changes": [], "attack_roll": None, "blocked": True}

    targets = _spell_targets(actor, targets_by_id, target_ids, spell_def)
    kwargs: dict = {}
    if spell_def["level"] == 0:
        kwargs["cantrip_scale"] = _cantrip_dice_count(actor.get("level", 1))
    if action.spell_id == "misty_step":
        if not point:
            return {"lines": ["迷踪步需要 target_point 指定瞬移落点。"], "hp_changes": [], "attack_roll": None, "blocked": True}
        distance_error, _ = validate_point_distance(state.get("space"), actor["id"], point, spell_range or 30, action_label=spell_def["name_cn"])
        if distance_error:
            return {"lines": [distance_error], "hp_changes": [], "attack_roll": None, "blocked": True}
        space_error, space_update, move_line = _move_caster_by_spell(state, actor["id"], point, spell_def["name_cn"])
        if space_error:
            return {"lines": [space_error], "hp_changes": [], "attack_roll": None, "blocked": True}
        kwargs["space_update"] = space_update
        kwargs["move_line"] = move_line
    if action.spell_id == "flaming_sphere" and point:
        kwargs["target_point"] = point.model_dump()

    extra_lines: list[str] = []
    if spell_def.get("concentration", False):
        combat_raw = state.get("combat")
        combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw or {})
        _break_concentration(actor, extra_lines, combat_dict=combat_dict)

    extra_lines.extend(remove_action_breaking_conditions(actor, event="spell"))
    result = spell_mod.execute(caster=actor, targets=targets, slot_level=slot_level, **kwargs)
    if spell_def.get("concentration", False):
        actor["concentrating_on"] = action.spell_id

    lines = [f"{actor.get('name', '?')} 使用 {action.name}。", *extra_lines, *result.get("lines", [])]
    return {
        "lines": lines,
        "hp_changes": result.get("hp_changes", []),
        "attack_roll": None,
        **({"space": result["space"]} if result.get("space") else {}),
    }


def _spell_targets(actor: dict, targets_by_id: dict[str, dict], target_ids: list[str], spell_def: dict) -> list[dict]:
    """按法术元数据补齐怪物施法目标；self 法术默认作用于施法者。"""
    if spell_def.get("range", "").lower().startswith("self"):
        return [actor]
    return [targets_by_id[target_id] for target_id in target_ids]


def _resolve_area_save(actor: dict, targets: list[dict], action: MonsterAction) -> dict:
    """范围豁免：一次伤害骰，逐目标做豁免，成功半伤。"""
    save = action.save
    damage = action.damage[0]
    dmg_roll = d20.roll(damage.dice)
    full_damage = max(1, dmg_roll.total)
    half_damage = full_damage // 2
    label = save.ability.upper()
    lines = [
        f"{actor.get('name', '?')} 使用 {action.name} — DC {save.dc} {label} 豁免。",
        f"伤害骰: {dmg_roll} = {full_damage} {damage.damage_type}伤害。",
    ]
    hp_changes: list[dict] = []

    for target in targets:
        save_roll, auto_fail_reason, disadvantaged = roll_actor_save(target, save.ability)
        if auto_fail_reason:
            saved = False
            roll_text = f"自动失败（{auto_fail_reason}）"
        else:
            saved = save_roll.total >= save.dc
            roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)
        actual_damage = half_damage if saved else full_damage
        result_text = "成功" if saved else "失败"
        lines.append(f"  → {target.get('name', '?')}: {label} 豁免{result_text}({roll_text})，承受 {actual_damage} {damage.damage_type}伤害。")
        _, hp_change, damage_lines = apply_damage_to_target(target, actual_damage, damage_type=damage.damage_type)
        hp_changes.append(hp_change)
        lines.extend(f"  {line}" for line in damage_lines)

    return {"lines": lines, "hp_changes": hp_changes, "attack_roll": None}


def _resolve_save_effect(actor: dict, target: dict, save, hp_changes: list[dict], base_damage: int) -> list[str]:
    """命中后的追加豁免，失败/成功效果完全来自动作数据。"""
    save_roll, auto_fail_reason, disadvantaged = roll_actor_save(target, save.ability)
    label = save.ability.upper()
    if auto_fail_reason:
        saved = False
        roll_text = f"自动失败（{auto_fail_reason}）"
    else:
        saved = save_roll.total >= save.dc
        roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)

    lines = [f"{target.get('name', '?')} 进行 {label} 豁免 DC {save.dc}: {roll_text}，{'成功' if saved else '失败'}。"]
    effects = save.success if saved else save.failure
    for effect in effects:
        lines.extend(_apply_effect(actor, target, effect, base_damage=base_damage, hp_changes=hp_changes))
    return lines


def _resolve_nimble_escape(actor: dict) -> dict:
    """地精机敏逃脱：用官方动作语义直接挂 Hide/Disengage 的战斗收益。"""
    upsert_condition(actor, create_condition("disengaged", source_id=actor.get("id", "")), replace_existing=True)
    upsert_condition(actor, create_condition("hidden", source_id=actor.get("id", "")), replace_existing=True)
    lines = [f"{actor.get('name', '?')} 使用 Nimble Escape：获得撤离与隐藏状态。"]
    return {"lines": lines, "hp_changes": [], "attack_roll": None}


def _resolve_weird_insight(actor: dict, target: dict) -> dict:
    """怪异洞悉：执行 CHA(Deception) vs WIS(Insight) 对抗，并产出秘密信息占位。"""
    actor_mod = actor.get("modifiers", {}).get("wis", 0)
    target_mod = target.get("modifiers", {}).get("cha", 0)
    insight_roll = d20.roll(f"1d20{actor_mod:+d}")
    deception_roll = d20.roll(f"1d20{target_mod:+d}")
    success = insight_roll.total >= deception_roll.total
    lines = [
        f"{actor.get('name', '?')} 使用 Weird Insight 观察 {target.get('name', '?')}。",
        f"  → WIS(Insight) {insight_roll} vs CHA(Deception) {deception_roll}：{'成功' if success else '失败'}。",
    ]
    if success:
        secret = target.get("secret") or target.get("backstory_secret") or "叙事层应揭示该目标一个事实、秘密或弱点。"
        actor.setdefault("known_secrets", {})[target.get("id", "")] = secret
        lines.append(f"  → 洞悉结果：{secret}")
    return {"lines": lines, "hp_changes": [], "attack_roll": None}


def _resolve_move_flaming_sphere(
    actor: dict,
    targets_by_id: dict[str, dict],
    state: dict,
    target_point: dict[str, float] | None,
) -> dict:
    """焰球后续回合用附赠动作移动并撞击目标，结束贴近伤害由回合 hook 处理。"""
    from app.conditions import find_condition
    from app.spells.flaming_sphere import damage_targets_near_sphere

    if not target_point:
        return {"lines": ["移动焰球需要 target_point 指定新位置。"], "hp_changes": [], "attack_roll": None, "blocked": True}

    condition = find_condition(actor.get("conditions", []), "flaming_sphere")
    if not condition:
        return {"lines": [f"{actor.get('name', '?')} 当前没有维持焰球。"], "hp_changes": [], "attack_roll": None, "blocked": True}

    old_position = condition.get("extra", {}).get("position", {})
    new_position = Point2D(**target_point)
    if old_position:
        old_point = Point2D(**old_position)
        from app.space.geometry import distance_to_point
        from app.graph.state import UnitPlacementState

        old_placement = UnitPlacementState(unit_id="flaming_sphere", map_id="", position=old_point)
        if distance_to_point(old_placement, new_position) > 30:
            return {"lines": ["焰球每次附赠动作最多移动 30 尺。"], "hp_changes": [], "attack_roll": None, "blocked": True}

    condition.setdefault("extra", {})["position"] = new_position.model_dump()
    lines = [f"{actor.get('name', '?')} 移动焰球至 ({new_position.x:g}, {new_position.y:g})。"]
    damage_result = damage_targets_near_sphere(actor, targets_by_id, condition, state, trigger="撞击")
    lines.extend(damage_result["lines"])
    return {"lines": lines, "hp_changes": damage_result["hp_changes"], "attack_roll": None}


def _apply_action_damage(actor: dict, target: dict, damage_parts: list[DamagePart], crit: bool, state: dict) -> dict:
    """同一次命中可有多段伤害，每段独立翻倍暴击骰并统一进 HP 管线。"""
    lines: list[str] = []
    hp_changes: list[dict] = []
    total_damage = 0
    if crit:
        lines.append("暴击！骰子数翻倍！")

    augmented_parts = [*damage_parts, *_trait_bonus_damage(actor, target, state)]
    for part in augmented_parts:
        dice = _crit_dice(part.dice) if crit else part.dice
        dmg_result = d20.roll(dice)
        damage = max(1, dmg_result.total)
        lines.append(f"伤害骰: {dmg_result} → {damage} 点 {part.damage_type} 伤害")
        dealt, hp_change, damage_lines = apply_damage_to_target(target, damage, damage_type=part.damage_type, crit=crit)
        total_damage += dealt
        hp_changes.append(hp_change)
        lines.extend(damage_lines)

    return {"lines": lines, "hp_changes": hp_changes, "total_damage": total_damage}


def _apply_effect(actor: dict, target: dict, effect: EffectSpec, *, base_damage: int, hp_changes: list[dict]) -> list[str]:
    """解释第一阶段支持的效果：状态、伤害和纯描述。"""
    effect_target = actor if effect.apply_to == "self" else target
    if effect.kind == "description":
        return [effect.text] if effect.text else []

    if effect.kind == "condition":
        raw_extra = _substitute_effect_extra(effect.extra, actor, target)
        extra = build_condition_extra(save_ends=effect.save_ends, **raw_extra) if effect.save_ends else raw_extra
        _, applied = upsert_condition(
            effect_target,
            create_condition(effect.condition_id, source_id=actor.get("id", ""), duration=effect.duration, extra=extra),
            replace_existing=True,
        )
        verb = "获得" if applied else "刷新"
        return [f"{effect_target.get('name', '?')} {verb}状态：{effect.condition_id}。"]

    if effect.kind == "damage" and effect.damage:
        dmg_roll = d20.roll(effect.damage.dice)
        damage = max(1, dmg_roll.total)
        if effect.damage_multiplier == "half":
            damage //= 2
        lines = [f"追加伤害骰: {dmg_roll} → {damage} 点 {effect.damage.damage_type} 伤害"]
        _, hp_change, damage_lines = apply_damage_to_target(target, damage, damage_type=effect.damage.damage_type)
        hp_changes.append(hp_change)
        lines.extend(damage_lines)
        return lines

    if effect.kind == "reduce_max_hp_by_damage":
        old_max = target.get("max_hp", target.get("hp", 0))
        new_max = max(0, old_max - base_damage)
        target["max_hp"] = new_max
        target["hp"] = min(target.get("hp", 0), new_max)
        return [f"{target.get('name', '?')} 的 HP 上限降低 {base_damage} 点：{old_max} → {new_max}。"]

    return []


def _trait_bonus_damage(actor: dict, target: dict, state: dict) -> list[DamagePart]:
    """处理少量数据难以表达的命中附伤特质，并用轮次记录避免一回合多次触发。"""
    bonus: list[DamagePart] = []
    traits = set(actor.get("traits", []))

    if "martial_advantage" in traits and _can_use_once_per_turn(actor, state, "martial_advantage") and _has_ally_near_target(actor, target, state):
        bonus.append(DamagePart(dice="2d6", damage_type="martial_advantage"))
        _mark_once_per_turn(actor, state, "martial_advantage")

    if "surprise_attack" in traits and _can_use_once_per_turn(actor, state, "surprise_attack") and _combat_round(state) == 1:
        bonus.append(DamagePart(dice="2d6", damage_type="surprise_attack"))
        _mark_once_per_turn(actor, state, "surprise_attack")

    return bonus


def _can_use_once_per_turn(actor: dict, state: dict, trait_id: str) -> bool:
    """用 combat round 做最小粒度的特质使用记录。"""
    round_no = _combat_round(state)
    return actor.setdefault("trait_uses", {}).get(trait_id) != round_no


def _mark_once_per_turn(actor: dict, state: dict, trait_id: str) -> None:
    """记录本轮已触发，避免多重攻击里重复吃同一个特质。"""
    actor.setdefault("trait_uses", {})[trait_id] = _combat_round(state)


def _combat_round(state: dict) -> int:
    """兼容 dict/Pydantic combat 状态读取当前轮次。"""
    combat = state.get("combat") or {}
    if hasattr(combat, "model_dump"):
        combat = combat.model_dump()
    return combat.get("round", 1)


def _has_ally_near_target(actor: dict, target: dict, state: dict) -> bool:
    """Martial Advantage 只要求目标 5 尺内有行动者盟友，空间未启用时不触发。"""
    space_raw = state.get("space")
    if not space_raw:
        return False
    space = build_space_state(space_raw)
    if not space.maps or target.get("id") not in space.placements:
        return False
    target_placement = space.placements[target["id"]]
    near_ids = [
        unit_id for unit_id, _ in units_in_radius(
            space.placements,
            map_id=target_placement.map_id,
            origin=target_placement.position,
            radius=5,
        )
    ]
    combat = state.get("combat")
    combat_dict = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat or {})
    participants = combat_dict.get("participants", {})
    for unit_id in near_ids:
        if unit_id == actor.get("id"):
            continue
        unit = participants.get(unit_id)
        if unit and unit.get("side") == actor.get("side"):
            return True
    return False


def _substitute_effect_extra(extra: dict, actor: dict, target: dict) -> dict:
    """让本地动作数据能引用当前目标 ID，不引入模板语言。"""
    result = {}
    for key, value in extra.items():
        if value == "$target_id":
            result[key] = target.get("id", "")
        else:
            result[key] = value
    return result


def _resolve_area_targets(
    actor: dict,
    targets_by_id: dict[str, dict],
    target_ids: list[str],
    target_spec: TargetSpec,
    state: dict,
) -> list[dict]:
    """优先用空间系统展开范围目标；无空间时使用调用方传入目标列表。"""
    space_raw = state.get("space")
    if not space_raw:
        return [targets_by_id[tid] for tid in target_ids]

    space = build_space_state(space_raw)
    if not space.maps:
        return [targets_by_id[tid] for tid in target_ids]

    actor_placement = space.placements[actor["id"]]
    if target_spec.kind == "cone":
        area = cone_area(
            actor_placement.position,
            actor_placement.facing_deg,
            target_spec.length_feet or 0,
            target_spec.angle_deg,
        )
        ids = [
            unit_id for unit_id, _ in units_in_geometry(
                space.placements,
                map_id=actor_placement.map_id,
                area=area,
                origin=actor_placement.position,
            )
            if target_spec.include_self or unit_id != actor["id"]
        ]
        return [targets_by_id[unit_id] for unit_id in ids if unit_id in targets_by_id]

    if target_spec.kind == "radius":
        origin = actor_placement.position
        ids = [
            unit_id for unit_id, _ in units_in_radius(
                space.placements,
                map_id=actor_placement.map_id,
                origin=Point2D(x=origin.x, y=origin.y),
                radius=target_spec.radius_feet or 0,
            )
            if target_spec.include_self or unit_id != actor["id"]
        ]
        return [targets_by_id[unit_id] for unit_id in ids if unit_id in targets_by_id]

    return [targets_by_id[tid] for tid in target_ids]


def _crit_dice(dice: str) -> str:
    """暴击只翻倍骰子数量，固定加值保持不变。"""
    return re.sub(r"(\d+)d(\d+)", lambda m: f"{int(m.group(1)) * 2}d{m.group(2)}", dice)


def _slugify(value: str) -> str:
    """把旧攻击名转为稳定 ID，避免模型只能靠显示名调用。"""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
