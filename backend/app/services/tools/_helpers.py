"""统一状态变更辅助 + 战斗解算纯函数

核心设计：战斗期间玩家数据不再复制到 combat.participants，
而是直接在 player_dict 上叠加战斗字段（id, attacks, action_available 等）。
NPC/怪物仍然使用 CombatantState 存储在 combat.participants 中。
所有工具通过 get_combatant() 统一访问参战者，从根本上消除双写同步问题。
"""

from __future__ import annotations

import re

import d20

from app.conditions import get_combat_effects, has_condition
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


def resolve_single_attack(
    attacker: dict,
    target: dict,
    attack_name: str | None = None,
    advantage: str = "normal",
) -> tuple[list[str], int, dict | None, dict]:
    """执行一次单体攻击的纯计算逻辑，返回 (日志行列表, 实际伤害值, hp_change 信息, extra_info 字典)。
    会原地修改 target["hp"] 与 attacker["action_available"]。
    自动叠加状态效果产生的优劣势。"""

    # 状态系统自动叠加优劣势
    cond_advantage = _determine_advantage_from_conditions(
        attacker.get("conditions", []),
        target.get("conditions", []),
    )
    # 合并手动指定与状态产生的优劣势
    if advantage == "normal":
        advantage = cond_advantage
    elif cond_advantage != "normal" and cond_advantage != advantage:
        advantage = "normal"  # 优势+劣势互相抵消

    # 失能检查：失能单位无法执行动作
    if has_condition(attacker.get("conditions", []), "incapacitated"):
        attacker["action_available"] = False
        return [f"{attacker.get('name', '?')} 处于失能状态，无法行动！"], 0, None, {"hit": False, "crit": False}

    # 魅惑检查：不能攻击魅惑者
    for c in attacker.get("conditions", []):
        if c.get("id") == "charmed" and c.get("source_id") == target.get("id", ""):
            return [f"{attacker.get('name', '?')} 被魅惑，无法攻击 {target.get('name', '?')}！"], 0, None, {"hit": False, "crit": False}

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
    target_ac = target.get("ac", 10)

    if natural == 1:
        hit, crit = False, False
    elif natural == 20:
        hit, crit = True, True
    else:
        hit = hit_result.total >= target_ac
        crit = False

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

    damage_dealt = 0
    hp_change: dict | None = None
    extra_info: dict = {"raw_roll": natural, "hit": hit, "crit": crit}
    if hit:
        if crit:
            crit_dice = re.sub(r"(\d+)d(\d+)", lambda m: f"{int(m.group(1))*2}d{m.group(2)}", dmg_dice)
            lines.append("暴击！骰子数翻倍！")
        else:
            crit_dice = dmg_dice

        dmg_result = d20.roll(crit_dice)
        damage_dealt = max(1, dmg_result.total)
        lines.append(f"伤害骰: {dmg_result} → {damage_dealt} 点 {dmg_type} 伤害")

        old_hp = target.get("hp", 0)
        new_hp = max(0, old_hp - damage_dealt)
        target["hp"] = new_hp
        lines.append(f"{tgt_name} HP: {old_hp} → {new_hp}")

        hp_change = {
            "id": target.get("id", ""),
            "name": tgt_name,
            "old_hp": old_hp,
            "new_hp": new_hp,
            "max_hp": target.get("max_hp", old_hp),
        }
        if new_hp == 0:
            lines.append(f"{tgt_name} 倒下了！")
    else:
        lines.append("未命中！" if natural != 1 else "严重失误！攻击完全落空！")

    # 移除被标记为受击即消耗的状态
    target_conditions = target.get("conditions", [])
    if target_conditions:
        surviving_conditions = [c for c in target_conditions if not c.get("extra", {}).get("consume_on_attacked")]
        if len(surviving_conditions) != len(target_conditions):
            target["conditions"] = surviving_conditions

    attacker["action_available"] = False
    return lines, damage_dealt, hp_change, extra_info


# ── 回合推进 ────────────────────────────────────────────────────


def advance_turn(combat_dict: dict, player_dict: dict | None = None) -> str:
    """推进回合到下一个存活单位，返回描述文本。原地修改 combat_dict 和 player_dict（如有）。"""
    order = combat_dict.get("initiative_order", [])
    current_id = combat_dict.get("current_actor_id", "")

    if not order:
        return "先攻顺序为空。"

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
    actor["action_available"] = True
    actor["bonus_action_available"] = True
    actor["reaction_available"] = True
    actor["movement_left"] = actor.get("speed", 30)

    current_round = combat_dict.get("round", 1)
    actor_name = actor.get("name", next_actor_id)
    return f"第 {current_round} 回合 — 当前行动者：{actor_name} [ID: {next_actor_id}] (HP: {actor.get('hp', '?')}/{actor.get('max_hp', '?')})"
