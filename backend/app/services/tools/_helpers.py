"""统一状态变更辅助 + 战斗解算纯函数

核心设计：战斗期间玩家数据不再复制到 combat.participants，
而是直接在 player_dict 上叠加战斗字段（id, attacks, action_available 等）。
NPC/怪物仍然使用 CombatantState 存储在 combat.participants 中。
所有工具通过 get_combatant() 统一访问参战者，从根本上消除双写同步问题。
"""

from __future__ import annotations

import re

import d20

from app.conditions import get_combat_effects, get_condition_module, tick_conditions
from app.graph.state import AttackInfo


# ── 战斗覆盖字段 ────────────────────────────────────────────────
# 战斗期间叠加到 player_dict 上的字段，战斗结束后清除
COMBAT_OVERLAY_KEYS = frozenset({
    "id", "side", "initiative", "proficiency_bonus", "attacks",
    "action_available", "bonus_action_available", "reaction_available",
    "speed", "movement_left",
})


def apply_hp_change(target: dict, delta: int) -> dict:
    """对目标施加 HP 变化（正数=治疗, 负数=伤害），返回 hp_change 记录。原地修改 target['hp']。"""
    old_hp = target.get("hp", 0)
    max_hp = target.get("max_hp", old_hp)
    new_hp = max(0, min(old_hp + delta, max_hp))
    target["hp"] = new_hp
    return {
        "id": target.get("id", ""),
        "name": target.get("name", "?"),
        "old_hp": old_hp,
        "new_hp": new_hp,
        "max_hp": max_hp,
    }


def apply_damage_to_target(target: dict, damage: int) -> tuple[int, dict, list[str]]:
    """统一处理受伤后的结界吸收、HP 结算与专注检定。"""
    damage = max(0, damage)
    damage, effect_lines = _absorb_damage(target, damage)

    hp_change = apply_hp_change(target, -damage)
    name = target.get("name", "?")
    lines = list(effect_lines)
    lines.append(f"{name} HP: {hp_change['old_hp']} → {hp_change['new_hp']}")
    if hp_change["new_hp"] == 0 and hp_change["old_hp"] > 0:
        lines.append(f"{name} 倒下了！")
    if damage > 0:
        lines.extend(check_concentration(target, damage))

    return damage, hp_change, lines


def prepare_player_for_combat(player_dict: dict) -> dict:
    """在 player_dict 上直接叠加战斗字段（原地修改），替代旧的 build_player_combatant。
    不再创建独立的 CombatantState 副本，从而消除数据双写问题。"""
    modifiers = player_dict.get("modifiers", {})
    prof = 2  # 1 级角色标准熟练加值

    attacks: list[dict] = []
    for w in player_dict.get("weapons", []):
        props = w.get("properties", [])
        if "finesse" in props:
            ability_mod = max(modifiers.get("str", 0), modifiers.get("dex", 0))
        elif w.get("weapon_type") == "ranged":
            ability_mod = modifiers.get("dex", 0)
        else:
            ability_mod = modifiers.get("str", 0)

        attacks.append(AttackInfo(
            name=w["name"],
            attack_bonus=prof + ability_mod,
            damage_dice=w.get("damage_dice", "1d4"),
            damage_type=w.get("damage_type", "bludgeoning"),
        ).model_dump())

    name = player_dict.get("name", "player")
    player_dict["id"] = f"player_{name}"
    player_dict["side"] = "player"
    player_dict["proficiency_bonus"] = prof
    player_dict["attacks"] = attacks
    player_dict["action_available"] = True
    player_dict["bonus_action_available"] = True
    player_dict["reaction_available"] = True
    player_dict["speed"] = 30
    player_dict["movement_left"] = 30
    return player_dict


def clear_player_combat_fields(player_dict: dict) -> dict:
    """清除战斗覆盖字段（原地修改），用于战斗结束后还原为纯 PlayerState"""
    for key in COMBAT_OVERLAY_KEYS:
        player_dict.pop(key, None)
    return player_dict


def get_combatant(
    combat_dict: dict, player_dict: dict | None, combatant_id: str
) -> dict | None:
    """统一获取参战者字典。玩家从 player_dict 返回，NPC 从 combat.participants 返回。"""
    if player_dict and combatant_id == player_dict.get("id"):
        return player_dict
    return combat_dict.get("participants", {}).get(combatant_id)


def get_all_combatants(
    combat_dict: dict, player_dict: dict | None
) -> dict[str, dict]:
    """获取所有参战者字典（含玩家），用于遍历全场"""
    result = dict(combat_dict.get("participants", {}))
    if player_dict and player_dict.get("id"):
        result[player_dict["id"]] = player_dict
    return result


ACTION_LABELS = {
    "action": "动作",
    "bonus_action": "附赠动作",
    "reaction": "反应",
}

SAVE_LABELS = {
    "str": "STR",
    "dex": "DEX",
    "con": "CON",
    "int": "INT",
    "wis": "WIS",
    "cha": "CHA",
}


def _iter_condition_handlers(conditions: list[dict]):
    """统一遍历条件模块与数据驱动效果，避免主流程到处手写查询。"""
    for condition in conditions:
        condition_id = condition.get("id", "")
        yield condition, get_condition_module(condition_id), get_combat_effects(condition_id)


def _condition_label(condition: dict, condition_module) -> str:
    """优先返回中文状态名，日志里避免直接暴露内部 ID。"""
    condition_def = getattr(condition_module, "CONDITION_DEF", None)
    if condition_def and getattr(condition_def, "name_cn", ""):
        return condition_def.name_cn
    return condition.get("id", "?")


# ── AC 动态计算 ─────────────────────────────────────────────────


def compute_ac(unit: dict) -> int:
    """从 base_ac + 条件模块的 modify_ac 钩子链式计算最终 AC。
    法术（如 mage_armor / shield）不再直接修改 AC，而是通过条件注册的钩子在此动态叠加。"""
    ac = unit.get("base_ac", unit.get("ac", 10))
    for condition, condition_module, _ in _iter_condition_handlers(unit.get("conditions", [])):
        if condition_module and hasattr(condition_module, "modify_ac"):
            ac = condition_module.modify_ac(unit, ac)
    return ac


def compute_current_speed(unit: dict) -> int:
    """根据条件效果计算当前可用速度，统一消费 speed_zero/prevents_movement。"""
    speed = unit.get("speed", 30)
    for condition, condition_module, effects in _iter_condition_handlers(unit.get("conditions", [])):
        if effects and (effects.speed_zero or effects.prevents_movement):
            return 0
        if condition_module and hasattr(condition_module, "modify_speed"):
            speed = condition_module.modify_speed(condition, unit, speed)
    return max(0, speed)


def sync_movement_state(unit: dict, *, reset_to_current_speed: bool = False) -> int:
    """让 movement_left 跟随条件修正后的速度，但不在回合中途平白补回移动力。"""
    current_speed = compute_current_speed(unit)
    if reset_to_current_speed or "movement_left" not in unit:
        unit["movement_left"] = current_speed
    else:
        unit["movement_left"] = min(unit.get("movement_left", current_speed), current_speed)
    return current_speed


def can_emit_attack_roll(roll_info: dict | None) -> bool:
    """区分内部攻击快照与前端骰子动画载荷，避免 blocked 行动误触发动画。"""
    return bool(roll_info) and not roll_info.get("blocked") and roll_info.get("emit_dice_roll", True)


def build_attack_roll_event_payload(roll_info: dict) -> dict | None:
    """统一构造一次攻击命中检定的前端展示载荷。"""
    if not can_emit_attack_roll(roll_info):
        return None

    raw_roll = roll_info.get("raw_roll", roll_info.get("natural", 0))
    final_total = roll_info.get("hit_total", raw_roll)
    return {
        "raw_roll": raw_roll,
        "attack_bonus": roll_info.get("attack_bonus", 0),
        "final_total": final_total,
        "hit_total": final_total,
        "target_ac": roll_info.get("target_ac", 10),
        "attack_name": roll_info.get("atk_name_display", ""),
    }


# ── 攻击解算 ────────────────────────────────────────────────────


def _get_natural_d20(result: d20.RollResult) -> int:
    """从 d20.RollResult 的 AST 中递归提取天然 d20 点数"""
    def _extract(node) -> int | None:
        if isinstance(node, d20.Dice):
            for die in node.values:
                if isinstance(die, d20.Die) and die.size == 20:
                    return die.values[0].number
        if hasattr(node, "left"):
            v = _extract(node.left)
            if v is not None:
                return v
            return _extract(node.right)
        if hasattr(node, "values"):
            for child in node.values:
                v = _extract(child)
                if v is not None:
                    return v
        return None

    return _extract(result.expr.roll) or result.total


def _determine_advantage_from_conditions(
    attacker_conditions: list[dict],
    defender_conditions: list[dict],
) -> str:
    """根据攻防双方的状态效果，计算综合优劣势"""
    adv_count = 0
    dis_count = 0

    for c in attacker_conditions:
        eff = get_combat_effects(c.get("id", ""))
        if not eff:
            continue
        if eff.attack_advantage == "advantage":
            adv_count += 1
        elif eff.attack_advantage == "disadvantage":
            dis_count += 1

    for c in defender_conditions:
        eff = get_combat_effects(c.get("id", ""))
        if not eff:
            continue
        if eff.defend_advantage == "advantage":
            adv_count += 1
        elif eff.defend_advantage == "disadvantage":
            dis_count += 1

    if adv_count > 0 and dis_count > 0:
        return "normal"
    if adv_count > 0:
        return "advantage"
    if dis_count > 0:
        return "disadvantage"
    return "normal"


def roll_actor_save(actor: dict, ability: str) -> tuple[d20.RollResult | None, str | None, bool]:
    """统一处理条件对豁免的影响：自动失败优先，其次才是劣势。"""
    disadvantage = False

    for condition, condition_module, effects in _iter_condition_handlers(actor.get("conditions", [])):
        if condition_module and hasattr(condition_module, "auto_fail_save"):
            fail_reason = condition_module.auto_fail_save(condition, actor, ability)
            if fail_reason:
                return None, fail_reason, False

        if effects and ability in effects.save_disadvantage:
            disadvantage = True

    save_mod = actor.get("modifiers", {}).get(ability, 0)
    if disadvantage:
        save_expr = f"2d20kl1{save_mod:+d}"
    else:
        save_expr = f"1d20{save_mod:+d}"

    return d20.roll(save_expr), None, disadvantage


def get_condition_action_block_reason(actor: dict, action_type: str = "action") -> str | None:
    """统一查询条件是否阻止动作/附赠动作/反应。"""
    actor_name = actor.get("name", "?")
    action_label = ACTION_LABELS.get(action_type, "行动")

    for condition, condition_module, effects in _iter_condition_handlers(actor.get("conditions", [])):
        hook_name = {
            "action": "on_action_eligibility",
            "bonus_action": "on_bonus_action_eligibility",
            "reaction": "on_reaction_eligibility",
        }.get(action_type, "")
        if hook_name and condition_module and hasattr(condition_module, hook_name):
            reason = getattr(condition_module, hook_name)(condition, actor)
            if reason:
                return reason

        if action_type == "reaction":
            prevented = bool(effects and effects.prevents_reactions)
        else:
            prevented = bool(effects and effects.prevents_actions)

        if prevented:
            condition_label = _condition_label(condition, condition_module)
            return f"{actor_name} 处于{condition_label}状态，无法执行{action_label}。"

    return None


def _determine_attack_block_reason(attacker: dict, target: dict) -> str | None:
    """攻击资格先走目标相关钩子，再回退到通用 prevents_actions。"""
    attacker_name = attacker.get("name", "?")

    for condition, condition_module, effects in _iter_condition_handlers(attacker.get("conditions", [])):
        if condition_module and hasattr(condition_module, "on_attack_eligibility"):
            reason = condition_module.on_attack_eligibility(condition, attacker, target)
            if reason:
                return reason

        if effects and effects.prevents_actions:
            condition_label = _condition_label(condition, condition_module)
            return f"{attacker_name} 处于{condition_label}状态，无法攻击。"

    return None


def _build_blocked_attack_result(reason: str) -> dict:
    """为被条件阻止的攻击返回统一结果结构。"""
    return {
        "blocked": True,
        "block_reason": reason,
        "emit_dice_roll": False,
    }


def _apply_attack_resolution_hooks(attacker: dict, target: dict, roll_info: dict) -> list[str]:
    """在基础命中判定后交给目标条件改判，避免主流程特判状态 ID。"""
    lines: list[str] = []

    for condition, condition_module, _ in _iter_condition_handlers(target.get("conditions", [])):
        if not condition_module or not hasattr(condition_module, "on_attack_resolved"):
            continue

        updates = condition_module.on_attack_resolved(condition, attacker, target, roll_info)
        if not updates:
            continue

        hook_lines = list(updates.get("lines", []))
        if hook_lines:
            lines.extend(hook_lines)

        stop_processing = bool(updates.get("stop_processing"))
        for key, value in updates.items():
            if key in {"lines", "stop_processing"}:
                continue
            roll_info[key] = value

        if stop_processing:
            break

    return lines


def remove_consume_on_attacked_conditions(target: dict) -> None:
    """统一移除受击即消耗的状态，攻击工具与法术解算共用同一入口。"""
    target_conditions = target.get("conditions", [])
    if not target_conditions:
        return

    surviving_conditions = []
    for condition, _, effects in _iter_condition_handlers(target_conditions):
        if condition.get("extra", {}).get("consume_on_attacked") or (effects and effects.consume_on_attacked):
            continue
        surviving_conditions.append(condition)

    if len(surviving_conditions) != len(target_conditions):
        target["conditions"] = surviving_conditions


def roll_attack_hit(
    attacker: dict,
    target: dict,
    attack_name: str | None = None,
    advantage: str = "normal",
) -> dict:
    """第一阶段：仅做命中骰 + AC 比较，不造成伤害、不修改任何状态。
    返回 roll_info dict，供 apply_attack_damage() 或反应中断流程使用。"""

    # 状态系统自动叠加优劣势
    cond_advantage = _determine_advantage_from_conditions(
        attacker.get("conditions", []),
        target.get("conditions", []),
    )
    if advantage == "normal":
        advantage = cond_advantage
    elif cond_advantage != "normal" and cond_advantage != advantage:
        advantage = "normal"

    block_reason = _determine_attack_block_reason(attacker, target)
    if block_reason:
        return _build_blocked_attack_result(block_reason)

    attacks = attacker.get("attacks", [])
    if attack_name:
        chosen = next((a for a in attacks if a["name"].lower() == attack_name.lower()), None)
    else:
        chosen = attacks[0] if attacks else None

    atk_bonus = chosen["attack_bonus"] if chosen else 0
    dmg_dice = chosen["damage_dice"] if chosen else "1d4"
    dmg_type = chosen.get("damage_type", "bludgeoning") if chosen else "bludgeoning"
    atk_name_display = chosen["name"] if chosen else "徒手攻击"

    if advantage == "advantage":
        hit_expr = f"2d20kh1+{atk_bonus}"
    elif advantage == "disadvantage":
        hit_expr = f"2d20kl1+{atk_bonus}"
    else:
        hit_expr = f"1d20+{atk_bonus}"

    hit_result = d20.roll(hit_expr)
    natural = _get_natural_d20(hit_result)
    target_ac = compute_ac(target)

    if natural == 1:
        hit, crit = False, False
    elif natural == 20:
        hit, crit = True, True
    else:
        hit = hit_result.total >= target_ac
        crit = False

    roll_info = {
        "blocked": False,
        "emit_dice_roll": True,
        "hit": hit,
        "crit": crit,
        "natural": natural,
        "raw_roll": natural,
        "attack_bonus": atk_bonus,
        "hit_total": hit_result.total,
        "target_ac": target_ac,
        "dmg_dice": dmg_dice,
        "dmg_type": dmg_type,
        "atk_name_display": atk_name_display,
        "advantage_used": advantage,
        "deflected": False,
    }

    hook_lines = _apply_attack_resolution_hooks(attacker, target, roll_info)

    lines: list[str] = []
    atk_name_src = attacker.get("name", "?")
    tgt_name = target.get("name", "?")
    lines.append(f"{atk_name_src} 使用 [{atk_name_display}] 攻击 {tgt_name}!")

    if natural == 1:
        lines.append(f"命中骰: {hit_result} (天然 1 - 严重失误!) vs AC {target_ac}")
    elif natural == 20:
        lines.append(f"命中骰: {hit_result} (天然 20 - 暴击!) vs AC {target_ac}")
    else:
        lines.append(f"命中骰: {hit_result} vs AC {target_ac}")

    lines.extend(hook_lines)
    roll_info["lines"] = lines
    return roll_info


def apply_attack_damage(
    attacker: dict,
    target: dict,
    roll_info: dict,
) -> tuple[list[str], int, dict | None, dict]:
    """第二阶段：根据 roll_info 结算伤害并修改状态。
    返回格式与旧 resolve_single_attack 一致: (lines, damage, hp_change, extra_info)。"""

    if roll_info.get("blocked"):
        attacker["action_available"] = False
        return [roll_info["block_reason"]], 0, None, {"hit": False, "crit": False}

    lines: list[str] = list(roll_info["lines"])
    hit = roll_info["hit"]
    crit = roll_info["crit"]
    natural = roll_info["natural"]
    dmg_dice = roll_info["dmg_dice"]
    dmg_type = roll_info["dmg_type"]
    tgt_name = target.get("name", "?")

    damage_dealt = 0
    hp_change: dict | None = None
    extra_info: dict = {
        "raw_roll": roll_info.get("raw_roll", natural),
        "attack_bonus": roll_info.get("attack_bonus", 0),
        "final_total": roll_info.get("hit_total", natural),
        "hit": hit,
        "crit": crit,
    }

    if hit:
        if crit:
            crit_dice = re.sub(r"(\d+)d(\d+)", lambda m: f"{int(m.group(1))*2}d{m.group(2)}", dmg_dice)
            lines.append("暴击！骰子数翻倍！")
        else:
            crit_dice = dmg_dice

        dmg_result = d20.roll(crit_dice)
        damage_dealt = max(1, dmg_result.total)
        lines.append(f"伤害骰: {dmg_result} → {damage_dealt} 点 {dmg_type} 伤害")

        damage_dealt, hp_change, damage_lines = apply_damage_to_target(target, damage_dealt)
        lines.extend(damage_lines)
    else:
        lines.append("未命中！" if natural != 1 else "严重失误！攻击完全落空！")

    remove_consume_on_attacked_conditions(target)

    attacker["action_available"] = False
    return lines, damage_dealt, hp_change, extra_info


def resolve_single_attack(
    attacker: dict,
    target: dict,
    attack_name: str | None = None,
    advantage: str = "normal",
) -> tuple[list[str], int, dict | None, dict]:
    """向后兼容包装器：一步完成命中+伤害解算"""
    roll_info = roll_attack_hit(attacker, target, attack_name, advantage)
    return apply_attack_damage(attacker, target, roll_info)


# ── 专注检定 ────────────────────────────────────────────────────


def check_concentration(target: dict, damage: int) -> list[str]:
    """受伤后自动 CON 豁免维持专注。DC = max(10, damage // 2)。
    失败时移除专注法术对应的条件并清除 concentrating_on。"""
    spell_id = target.get("concentrating_on")
    if not spell_id:
        return []

    dc = max(10, damage // 2)
    name = target.get("name", "?")
    save_roll, auto_fail_reason, disadvantaged = roll_actor_save(target, "con")

    if auto_fail_reason:
        return [f"  [{name} 专注检定] DC {dc} — 自动失败（{auto_fail_reason}）！失去对 {spell_id} 的专注"]

    roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)

    if save_roll.total >= dc:
        return [f"  [{name} 专注检定] DC {dc} — {roll_text} 成功，维持 {spell_id} 专注"]

    # 失败：移除专注法术挂载的条件
    target["concentrating_on"] = None
    conditions = target.get("conditions", [])
    target["conditions"] = [c for c in conditions if c.get("source_id") != f"concentration:{spell_id}"]
    return [f"  [{name} 专注检定] DC {dc} — {roll_text} 失败！失去对 {spell_id} 的专注"]


# ── 奥术结界伤害吸收 ────────────────────────────────────────────


def _absorb_damage(target: dict, damage: int) -> tuple[int, list[str]]:
    """检查 arcane_ward 条件，优先用结界 HP 吸收伤害。返回 (剩余伤害, 日志行)。"""
    for condition, condition_module, _ in _iter_condition_handlers(target.get("conditions", [])):
        if condition_module and hasattr(condition_module, "absorb_damage"):
            damage, lines = condition_module.absorb_damage(condition, target, damage)
            # 结界 HP 归零时移除条件
            if condition.get("extra", {}).get("ward_hp", 1) <= 0:
                target["conditions"] = [x for x in target["conditions"] if x is not condition]
            return damage, lines
    return damage, []


# ── 法术位消耗 ─────────────────────────────────────────────────


def consume_spell_slot(resources: dict, slot_level: int) -> str | None:
    """查找并标记待消耗的法术位。优先使用普通法术位，再用秘契法术位。
    返回消耗的 slot_key，或 None 若无可用位。"""
    slot_key = f"spell_slot_lv{slot_level}"
    pact_key = f"pact_magic_lv{slot_level}"
    if resources.get(slot_key, 0) > 0:
        return slot_key
    elif resources.get(pact_key, 0) > 0:
        return pact_key
    return None


# ── CR → XP 映射 ───────────────────────────────────────────────

CR_XP_TABLE: dict[str, int] = {
    "0": 10, "1/8": 25, "1/4": 50, "1/2": 100,
    "1": 200, "2": 450, "3": 700, "4": 1100, "5": 1800,
    "6": 2300, "7": 2900, "8": 3900, "9": 5000, "10": 5900,
}

# 升级经验值阈值
XP_THRESHOLDS: dict[int, int] = {2: 300, 3: 900, 4: 2700, 5: 6500}


def xp_from_cr(cr_value) -> int:
    """从怪物 CR 值查询对应 XP 奖励"""
    return CR_XP_TABLE.get(str(cr_value), 0)


# ── 回合推进 ────────────────────────────────────────────────────


def _process_save_ends(actor: dict) -> list[str]:
    """回合末对带 save_ends 的条件进行豁免（如 Hold Person），成功则移除。"""
    lines: list[str] = []
    conditions = actor.get("conditions", [])
    to_remove_ids: set[int] = set()
    name = actor.get("name", "?")

    for c in conditions:
        save_info = c.get("extra", {}).get("save_ends")
        if not save_info:
            continue
        ability = save_info.get("ability", "wis")
        dc = save_info.get("dc", 10)
        save_roll, auto_fail_reason, disadvantaged = roll_actor_save(actor, ability)
        label = SAVE_LABELS.get(ability, ability)

        if auto_fail_reason:
            lines.append(f"  [{name} 回合末豁免] {label} DC {dc} — 自动失败（{auto_fail_reason}），{c['id']} 持续")
            continue

        roll_text = f"{save_roll}（劣势）" if disadvantaged else str(save_roll)

        if save_roll.total >= dc:
            to_remove_ids.add(id(c))
            lines.append(f"  [{name} 回合末豁免] {label} DC {dc} — {roll_text} 成功！{c['id']} 状态解除")
        else:
            lines.append(f"  [{name} 回合末豁免] {label} DC {dc} — {roll_text} 失败，{c['id']} 持续")

    if to_remove_ids:
        actor["conditions"] = [condition for condition in conditions if id(condition) not in to_remove_ids]
        # 同时清除施法者的专注标记（如 hold_person 被豁免解除）
        # 不在此处理——施法者侧由自己的 check_concentration 或手动管理

    return lines


def _expire_start_of_turn_conditions(all_combatants: dict[str, dict], actor: dict) -> list[str]:
    """支持跨单位的 start-of-turn 过期条件，例如 Ray of Frost 的减速。"""
    actor_id = actor.get("id")
    actor_name = actor.get("name", actor_id or "?")
    if not actor_id:
        return []

    lines: list[str] = []
    for owner in all_combatants.values():
        remaining: list[dict] = []
        expired: list[str] = []
        for condition in owner.get("conditions", []):
            expire_on = condition.get("extra", {}).get("expire_on_turn_start_of")
            if expire_on == actor_id:
                expired.append(condition.get("id", "?"))
                continue
            remaining.append(condition)

        if not expired:
            continue

        owner["conditions"] = remaining
        lines.append(f"（{owner.get('name', '?')} 的状态在 {actor_name} 的回合开始时过期：{', '.join(expired)}）")

    return lines


def _process_turn_end_conditions(actor: dict) -> list[str]:
    """统一处理回合末生命周期：先递减 duration，再执行 save_ends。"""
    lines: list[str] = []
    conditions = actor.get("conditions", [])
    if not conditions:
        return lines

    remaining, expired = tick_conditions(conditions)
    actor["conditions"] = remaining
    if expired:
        lines.append(f"（{actor.get('name', '?')} 的状态已过期：{', '.join(expired)}）")

    lines.extend(_process_save_ends(actor))
    return lines


def _process_turn_start_conditions(actor: dict, all_combatants: dict[str, dict]) -> list[str]:
    """统一处理回合开始生命周期与动作资源重置。"""
    lines = _expire_start_of_turn_conditions(all_combatants, actor)

    actor["action_available"] = True
    actor["bonus_action_available"] = True
    actor["reaction_available"] = True
    sync_movement_state(actor, reset_to_current_speed=True)
    return lines


def advance_turn(combat_dict: dict, player_dict: dict | None = None) -> str:
    """推进回合到下一个存活单位，返回描述文本。原地修改 combat_dict 和 player_dict（如有）。
    在切换前对当前行动者执行 tick_conditions（递减持续时间、移除过期状态）。"""
    order = combat_dict.get("initiative_order", [])
    current_id = combat_dict.get("current_actor_id", "")

    if not order:
        return "先攻顺序为空。"

    lifecycle_lines: list[str] = []
    if current_id:
        actor_leaving = get_combatant(combat_dict, player_dict, current_id)
        if actor_leaving:
            lifecycle_lines.extend(_process_turn_end_conditions(actor_leaving))

    current_idx = order.index(current_id) if current_id in order else -1
    total = len(order)
    checked = 0
    next_idx = (current_idx + 1) % total
    while checked < total:
        candidate_id = order[next_idx]
        p = get_combatant(combat_dict, player_dict, candidate_id)
        if p and p.get("hp", 0) > 0:
            break
        next_idx = (next_idx + 1) % total
        checked += 1
    else:
        return "所有参战者均已倒下，战斗结束。"

    if next_idx <= current_idx or current_idx == -1:
        combat_dict["round"] = combat_dict.get("round", 1) + 1

    next_actor_id = order[next_idx]
    combat_dict["current_actor_id"] = next_actor_id

    actor = get_combatant(combat_dict, player_dict, next_actor_id)
    lifecycle_lines.extend(_process_turn_start_conditions(actor, get_all_combatants(combat_dict, player_dict)))

    current_round = combat_dict.get("round", 1)
    actor_name = actor.get("name", next_actor_id)
    lifecycle_text = ""
    if lifecycle_lines:
        lifecycle_text = "\n".join(lifecycle_lines) + "\n"
    return f"{lifecycle_text}第 {current_round} 回合 — 当前行动者：{actor_name} [ID: {next_actor_id}] (HP: {actor.get('hp', '?')}/{actor.get('max_hp', '?')})"
