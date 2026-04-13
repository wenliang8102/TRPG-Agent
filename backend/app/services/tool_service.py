"""Tool definitions — 掷骰 + 战斗动作工具链"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

import d20

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.utils.logger import logger
from app.calculation.predefined_characters import PREDEFINED_CHARACTERS
from app.calculation.bestiary import spawn_combatants
from app.calculation.abilities import ability_to_modifier
from app.graph.state import AttackInfo, CombatState, CombatantState


# ── 统一状态变更辅助 ─────────────────────────────────────────────


def _apply_hp_change(target: dict, delta: int) -> dict:
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


@tool
def weather(city: str, unit: str = "c") -> dict:
    """获取指定城市的天气信息。

    Args:
        city: 目标城市名称。
        unit: 温度单位，支持 "c" (摄氏度) 或 "f" (华氏度)。
    """
    normalized_unit = (unit or "c").strip().lower()
    if normalized_unit not in {"c", "f"}:
        normalized_unit = "c"

    city_name = (city or "").strip() or "unknown"
    temperature_c = 22
    temperature = temperature_c if normalized_unit == "c" else int(temperature_c * 9 / 5 + 32)

    return {
        "city": city_name,
        "temperature": temperature,
        "unit": normalized_unit,
        "condition": "clear",
        "source": "mock",
    }


@tool
def request_dice_roll(
    reason: str,
    state: Annotated[dict, InjectedState], 
    ability: Literal["str", "dex", "con", "int", "wis", "cha"] | None = None,
    formula: str = "1d20"
) -> dict:
    """向玩家发起掷骰请求以判断动作结果（例如：“破门力量检定”）。
    如果提供了 `ability` 参数，系统会自动获取对应角色的属性值，并计算修正附加到总分中。
    注意：你在接下来的叙事中绝对不需要（也不应该）手动二次加上修正值计算结果，因为本工具返回的 final_total 已经包含了修正值！
    
    Args:
        reason: 掷骰的叙事原因，例如 "破门力量检定"。
        ability: 【强烈推荐】动作所依赖的属性 ("str", "dex", "con", "int", "wis", "cha")。
        formula: 掷骰公式，默认为 "1d20"。
    """
    # 提取属性修正值
    modifier = 0
    if ability and state.get("player") and "modifiers" in state["player"]:
        modifier = state["player"]["modifiers"].get(ability, 0)

    # 全自动掷骰，不再中断等待前端确认
    result = d20.roll(formula)
    raw_roll = result.total
    final_total = raw_roll + modifier

    sign = '+' if modifier >= 0 else ''
    modifier_str = f"属性修正({ability}){sign}{modifier}" if ability else "无属性修正"
    
    note_str = (
        f"系统已完成严谨计算：基础骰值(raw_roll)={raw_roll}，"
        f"{modifier_str}，最终总值(final_total)={final_total}。\n"
        "【特别指令】：请向玩家如实播报这个算式（例：“基础X + 修正Y = 最终Z”），并严格仅使用 final_total 判断检定成败，不要自己重新做加法！"
    )

    return {
        "raw_roll": raw_roll,
        "modifier": modifier,
        "final_total": final_total,
        "status": "success",
        "note": note_str
    }


@tool
def load_character_profile(
    role_class: str,
    tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command | str:
    """根据给定的职业（如'战士'、'法师'、'游荡者'）读取并加载该角色的预设属性卡。
    此工具会自动把角色的血量(HP)、护甲(AC)和各项能力值/修正值写入游戏的主状态中。
    在需要与角色互动前使用此工具为玩家初始化。

    Args:
        role_class: 需要加载的角色职业名称。当前支持："战士", "法师", "游荡者"。
    """
    key = role_class.strip()
    if key not in PREDEFINED_CHARACTERS:
        return f"未找到对应职业 '{key}'。支持的预设职业为：{', '.join(PREDEFINED_CHARACTERS.keys())}。"

    profile = PREDEFINED_CHARACTERS[key]
    
    import json
    
    # 依赖 LangGraph 机制原地更新 PlayerState 节点的共享状态
    # 并且返回 ToolMessage 防止节点因为缺少工具执行确认而报错
    return Command(
        update={
            "player": profile,
            "messages": [
                ToolMessage(
                    content=f"已成功加载角色卡：{key}。\n属性如下：{json.dumps(profile, ensure_ascii=False, indent=2)}",
                    tool_call_id=tool_call_id
                )
            ]
        }
    )


@tool
def modify_character_state(
    target_id: str,
    changes: dict,
    reason: str = "",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """调整任意角色/战斗单位的状态属性。所有涉及 HP、AC、能力值等数值变化都应通过该工具执行。

    支持的 changes 键包括：hp_delta(增减HP)、set_hp(直接设置HP)、ac、speed、
    abilities(dict)、conditions(list)、add_condition(str)、remove_condition(str) 等。
    对于 HP 变化，优先使用 hp_delta（正=治疗, 负=伤害）以确保边界安全。

    Args:
        target_id: 目标单位 ID（如 "player_预设-战士"、"goblin_1"）或 "player" 表示当前玩家。
        changes: 要修改的属性字典，如 {"hp_delta": -5} 或 {"ac": 18, "add_condition": "prone"}。
        reason: 修改原因的简短描述，用于日志。
    """
    update: dict = {}
    lines: list[str] = [f"[状态变更] {reason}" if reason else "[状态变更]"]
    hp_changes: list[dict] = []

    # 定位目标：可能是玩家本体、场景单位或战斗参与者
    is_player = target_id == "player"
    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

    if is_player and player_dict:
        target_id = f"player_{player_dict.get('name', 'player')}"

    # 在战斗参与者中查找
    combat_raw = state.get("combat")
    combat_dict = None
    combat_target = None
    if combat_raw:
        combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
        combat_target = combat_dict.get("participants", {}).get(target_id)

    # 在场景单位中查找
    scene_units: dict = state.get("scene_units") or {}
    scene_raw = scene_units
    if hasattr(scene_units, "model_dump"):
        scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()}
    elif isinstance(scene_units, dict):
        scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()}
    scene_target = scene_raw.get(target_id)

    # 确定实际操作对象
    target = combat_target or scene_target
    if not target and player_dict and target_id == f"player_{player_dict.get('name', 'player')}":
        # 不在战斗也不在场景，操作玩家本体
        target = player_dict
    if not target:
        return Command(update={"messages": [
            ToolMessage(content=f"找不到目标 '{target_id}'。", tool_call_id=tool_call_id)
        ]})

    target_name = target.get("name", target_id)

    # 应用各项变更
    if "hp_delta" in changes:
        hc = _apply_hp_change(target, changes["hp_delta"])
        hp_changes.append(hc)
        lines.append(f"  {target_name} HP: {hc['old_hp']} → {hc['new_hp']}")
    if "set_hp" in changes:
        old_hp = target.get("hp", 0)
        max_hp = target.get("max_hp", old_hp)
        new_hp = max(0, min(int(changes["set_hp"]), max_hp))
        target["hp"] = new_hp
        hp_changes.append({"id": target.get("id", target_id), "name": target_name, "old_hp": old_hp, "new_hp": new_hp, "max_hp": max_hp})
        lines.append(f"  {target_name} HP 设为 {new_hp}")
    if "ac" in changes:
        target["ac"] = int(changes["ac"])
        lines.append(f"  {target_name} AC → {target['ac']}")
    if "speed" in changes:
        target["speed"] = int(changes["speed"])
        lines.append(f"  {target_name} 速度 → {target['speed']}")
    if "abilities" in changes:
        for k, v in changes["abilities"].items():
            target.setdefault("abilities", {})[k] = int(v)
            target.setdefault("modifiers", {})[k] = ability_to_modifier(int(v))
        lines.append(f"  {target_name} 能力值已更新")
    if "add_condition" in changes:
        conds = target.setdefault("conditions", [])
        c = changes["add_condition"]
        if c not in conds:
            conds.append(c)
        lines.append(f"  {target_name} +状态: {c}")
    if "remove_condition" in changes:
        conds = target.get("conditions", [])
        c = changes["remove_condition"]
        if c in conds:
            conds.remove(c)
        lines.append(f"  {target_name} -状态: {c}")

    # 回写变更
    if combat_target and combat_dict:
        combat_dict["participants"][target_id] = target
        update["combat"] = combat_dict
    if scene_target:
        scene_raw[target_id] = target
        update["scene_units"] = scene_raw

    # 同步玩家本体
    if player_dict and target_id == f"player_{player_dict.get('name', 'player')}":
        for key in ("hp", "ac", "abilities", "modifiers", "conditions"):
            if key in target:
                player_dict[key] = target[key]
        update["player"] = player_dict

    if hp_changes:
        update["hp_changes"] = hp_changes

    update["messages"] = [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)]
    return Command(update=update)



# ── 玩家 → 战斗单位转换 ─────────────────────────────────────────


def _build_player_combatant(player: dict) -> dict:
    """从 PlayerState 字典 + 已装备武器生成 CombatantState 字典，
    将武器属性自动计算为 AttackInfo（含 attack_bonus）。"""
    modifiers = player.get("modifiers", {})
    prof = 2  # 1 级角色标准熟练加值

    attacks: list[dict] = []
    for w in player.get("weapons", []):
        props = w.get("properties", [])
        # finesse 取 STR/DEX 较高者；ranged 用 DEX；melee 用 STR
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

    name = player.get("name", "player")
    return CombatantState(
        id=f"player_{name}",
        name=name,
        side="player",
        hp=player.get("hp", 1),
        max_hp=player.get("max_hp", 1),
        ac=player.get("ac", 10),
        speed=30,
        abilities=player.get("abilities", {}),
        modifiers=modifiers,
        proficiency_bonus=prof,
        attacks=[AttackInfo(**a) for a in attacks],
    ).model_dump()


@tool
def spawn_monsters(
    monster_index: str,
    count: int = 1,
    faction: str = "enemy",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None
) -> Command:
    """根据怪物图鉴生成战斗单位实例并加入当前场景。
    怪物数据来自 Open5e SRD（使用英文 slug，如 "goblin", "owlbear", "adult-red-dragon"）。
    生成后的单位进入场景单位池（scene_units），需要通过 start_combat 指定参战。

    Args:
        monster_index: 怪物的 Open5e slug（如 "goblin"）。必须输入其英文代号。
        count: 生成该单位的数量。默认为 1。
        faction: 阵营，通常为 "enemy", "ally" 或 "neutral"。默认 "enemy"。
    """
    try:
        new_combatants = spawn_combatants(monster_index, count, faction)
    except Exception as e:
        return f"生成战斗单位失败: {str(e)}"

    # 写入 scene_units 而非 combat.participants
    scene_units: dict = state.get("scene_units") or {}
    if hasattr(scene_units, "items"):
        scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()}
    else:
        scene_raw = {}

    for c in new_combatants:
        scene_raw[c.id] = c.model_dump()

    names = [f"{c.name} [ID: {c.id}]" for c in new_combatants]

    return Command(
        update={
            "scene_units": scene_raw,
            "messages": [
                ToolMessage(
                    content=f"成功在场景中生成了 {count} 只 {monster_index}: {', '.join(names)}。\n可用 start_combat 指定哪些单位参加战斗。",
                    tool_call_id=tool_call_id
                )
            ]
        }
    )


# ── 战斗工具链 ──────────────────────────────────────────────────


def _get_natural_d20(result: d20.RollResult) -> int:
    """从 d20.RollResult 的 AST 中递归提取天然 d20 点数"""
    def _extract(node) -> int | None:
        # Dice 节点（如 1d20）包含 Die 子节点
        if isinstance(node, d20.Dice):
            for die in node.values:
                if isinstance(die, d20.Die) and die.size == 20:
                    return die.values[0].number
        # 递归遍历复合节点（BinOp 有 left/right，其他有 values）
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


def resolve_single_attack(
    attacker: dict,
    target: dict,
    attack_name: str | None = None,
    advantage: str = "normal",
) -> tuple[list[str], int, dict | None, dict]:
    """执行一次单体攻击的纯计算逻辑，返回 (日志行列表, 实际伤害值, hp_change 信息, extra_info 字典)。
    会原地修改 target["hp"] 与 attacker["action_available"]。"""
    import re

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

    attacker["action_available"] = False
    return lines, damage_dealt, hp_change, extra_info


def advance_turn(combat_dict: dict) -> str:
    """推进回合到下一个存活单位，返回描述文本。原地修改 combat_dict。"""
    order = combat_dict.get("initiative_order", [])
    participants = combat_dict.get("participants", {})
    current_id = combat_dict.get("current_actor_id", "")

    if not order:
        return "先攻顺序为空。"

    current_idx = order.index(current_id) if current_id in order else -1
    total = len(order)
    checked = 0
    next_idx = (current_idx + 1) % total
    while checked < total:
        candidate_id = order[next_idx]
        p = participants.get(candidate_id, {})
        if p.get("hp", 0) > 0:
            break
        next_idx = (next_idx + 1) % total
        checked += 1
    else:
        return "所有参战者均已倒下，战斗结束。"

    if next_idx <= current_idx or current_idx == -1:
        combat_dict["round"] = combat_dict.get("round", 1) + 1

    next_actor_id = order[next_idx]
    combat_dict["current_actor_id"] = next_actor_id

    actor = participants.get(next_actor_id, {})
    actor["action_available"] = True
    actor["bonus_action_available"] = True
    actor["reaction_available"] = True
    actor["movement_left"] = actor.get("speed", 30)

    current_round = combat_dict.get("round", 1)
    actor_name = actor.get("name", next_actor_id)
    return f"第 {current_round} 回合 — 当前行动者：{actor_name} [ID: {next_actor_id}] (HP: {actor.get('hp', '?')}/{actor.get('max_hp', '?')})"


@tool
def start_combat(
    combatant_ids: list[str],
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """开始战斗：从场景单位池中选取指定 ID 的单位作为参战者，投先攻骰并排定行动顺序。
    前置条件：必须先用 spawn_monsters 生成单位到场景中。
    玩家角色会自动加入，无需在 combatant_ids 中指定。

    Args:
        combatant_ids: 从场景中参加本次战斗的单位 ID 列表（如 ["goblin_1", "goblin_2"]）。
    """
    scene_units: dict = state.get("scene_units") or {}
    if hasattr(scene_units, "items"):
        scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()}
    else:
        scene_raw = {}

    if not combatant_ids and not scene_raw:
        return "场景中没有任何单位。请先使用 spawn_monsters 生成怪物。"

    participants: dict[str, dict] = {}
    missing: list[str] = []
    for uid in combatant_ids:
        unit = scene_raw.get(uid)
        if unit:
            participants[uid] = unit
        else:
            missing.append(uid)

    if missing:
        available = ", ".join(scene_raw.keys()) or "无"
        return f"找不到以下单位: {', '.join(missing)}。场景中可用单位: {available}"

    # 玩家自动入场
    player_raw = state.get("player")
    if player_raw:
        player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw)
        player_id = f"player_{player_dict.get('name', 'player')}"
        if player_id not in participants:
            participants[player_id] = _build_player_combatant(player_dict)

    if not participants:
        return "没有参战者，请先生成怪物或加载角色卡。"

    # 为每个参战单位投先攻
    initiative_list: list[tuple[str, int]] = []
    for uid, p in participants.items():
        dex_mod = p.get("modifiers", {}).get("dex", 0)
        init_roll = d20.roll(f"1d20+{dex_mod}")
        p["initiative"] = init_roll.total
        initiative_list.append((uid, init_roll.total))

    initiative_list.sort(key=lambda x: x[1], reverse=True)
    order = [uid for uid, _ in initiative_list]

    combat_dict = {
        "round": 1,
        "participants": participants,
        "initiative_order": order,
        "current_actor_id": order[0],
    }

    order_desc = "\n".join(
        f"  {i+1}. {participants[uid].get('name', uid)} [ID: {uid}] (先攻 {init})"
        for i, (uid, init) in enumerate(initiative_list)
    )

    return Command(
        update={
            "combat": combat_dict,
            "phase": "combat",
            "messages": [
                ToolMessage(
                    content=f"战斗开始！第 1 回合。\n先攻顺序：\n{order_desc}\n\n当前行动者：{participants[order[0]].get('name', order[0])} [ID: {order[0]}]",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


@tool
def attack_action(
    attacker_id: str,
    target_id: str,
    attack_name: str | None = None,
    advantage: Literal["normal", "advantage", "disadvantage"] = "normal",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """执行一次攻击动作：命中判定 → 暴击检测 → 伤害结算 → 扣血。
    玩家攻击结束后如果没有其他额外动作，可以询问玩家或代表玩家调用 `next_turn`。
    
    Args:
        attacker_id: 攻击者的 ID。
        target_id: 目标的 ID。
        attack_name: 使用的攻击名称（可选，默认使用攻击者的第一个攻击方式）。
        advantage: 攻击优劣势，"normal" / "advantage" / "disadvantage"。
    """
    combat_raw = state.get("combat")
    if not combat_raw:
        return "当前不在战斗中。"

    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
    participants = combat_dict.get("participants", {})

    attacker = participants.get(attacker_id)
    target = participants.get(target_id)

    # 前置校验 — 错误统一走 Command+ToolMessage 确保 LLM 不会忽略
    def _reject(msg: str) -> Command:
        return Command(update={"messages": [
            ToolMessage(content=f"[攻击失败] {msg}", tool_call_id=tool_call_id)
        ]})

    if not attacker:
        return _reject(f"找不到攻击者 '{attacker_id}'。")
    if not target:
        return _reject(f"找不到目标 '{target_id}'。")
    if combat_dict.get("current_actor_id") != attacker_id:
        return _reject(f"现在不是 {attacker.get('name', attacker_id)} 的回合，当前行动者为 {combat_dict.get('current_actor_id')}。")
    if target.get("hp", 0) <= 0:
        return _reject(f"目标 {target.get('name', target_id)} 已经倒下，无法攻击。")
    if not attacker.get("action_available", True):
        return _reject(f"{attacker.get('name', attacker_id)} 本回合的动作已用尽。")

    # 全自动掷骰，玩家攻击不再中断等待确认
    # 委托核心计算函数
    lines, _, hp_change, extra_info = resolve_single_attack(attacker, target, attack_name, advantage)

    tool_msg = ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)
    tool_msg.artifact = {"raw_roll": extra_info.get("raw_roll")}

    update: dict = {
        "combat": combat_dict,
        "messages": [tool_msg],
    }

    if hp_change:
        update["hp_changes"] = [hp_change]
        # 同步玩家本体 HP
        if target.get("side") == "player" and state.get("player"):
            player_dict = state.get("player").model_dump() if hasattr(state.get("player"), "model_dump") else dict(state.get("player"))
            player_dict["hp"] = hp_change["new_hp"]
            update["player"] = player_dict

    return Command(update=update)


@tool
def next_turn(
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """推进到下一个行动者的回合。如果所有人都行动过，则进入新的回合。"""
    combat_raw = state.get("combat")
    if not combat_raw:
        return "当前不在战斗中。"

    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)

    if not combat_dict.get("initiative_order"):
        return "先攻顺序为空，请先调用 start_combat。"

    result_text = advance_turn(combat_dict)

    return Command(
        update={
            "combat": combat_dict,
            "messages": [
                ToolMessage(content=result_text, tool_call_id=tool_call_id)
            ],
        }
    )


@tool
def end_combat(
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """结束当前战斗。存活的非玩家单位回归场景，死亡单位归入死亡档案（可搜尸等）。"""
    combat_raw = state.get("combat")
    summary = "战斗结束。"
    update: dict = {"combat": None, "phase": "exploration"}

    if combat_raw:
        combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
        rounds = combat_dict.get("round", 0)
        participants = combat_dict.get("participants", {})

        alive_names: list[str] = []
        fallen_names: list[str] = []

        # 拿到现有场景单位和死亡单位快照
        scene_units: dict = state.get("scene_units") or {}
        scene_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()} if hasattr(scene_units, "items") else {}
        dead_units: dict = state.get("dead_units") or {}
        dead_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in dead_units.items()} if hasattr(dead_units, "items") else {}

        # 同步玩家 HP 回本体
        player_raw = state.get("player")
        player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None

        for uid, p in participants.items():
            name = p.get("name", uid)
            if p.get("side") == "player":
                # 玩家不进 scene_units/dead_units，只同步 HP
                if p.get("hp", 0) > 0:
                    alive_names.append(name)
                else:
                    fallen_names.append(name)
                if player_dict and uid == f"player_{player_dict.get('name', 'player')}":
                    player_dict["hp"] = p.get("hp", 0)
                continue

            if p.get("hp", 0) > 0:
                alive_names.append(name)
                scene_raw[uid] = p  # 存活单位回归场景
            else:
                fallen_names.append(name)
                dead_raw[uid] = p  # 死亡单位归档
                scene_raw.pop(uid, None)

        parts = [f"共进行了 {rounds} 回合。"]
        if alive_names:
            parts.append(f"存活: {', '.join(alive_names)}")
        if fallen_names:
            parts.append(f"倒下: {', '.join(fallen_names)}")
        summary = " ".join(parts)

        update["scene_units"] = scene_raw
        update["dead_units"] = dead_raw
        if player_dict:
            update["player"] = player_dict

    update["messages"] = [ToolMessage(content=summary, tool_call_id=tool_call_id)]
    return Command(update=update)


@tool
def clear_dead_units(
    unit_ids: list[str] | None = None,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """清除死亡单位档案。可指定 ID 列表部分清除，或不传参数清除全部。
    适用于剧情上玩家已完成搜刮尸体、处理遗骸等环节后的清理。

    Args:
        unit_ids: 要清除的死亡单位 ID 列表。为空则清除全部。
    """
    dead_units: dict = state.get("dead_units") or {}
    dead_raw = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in dead_units.items()} if hasattr(dead_units, "items") else {}

    if not dead_raw:
        return Command(update={"messages": [
            ToolMessage(content="当前没有死亡单位。", tool_call_id=tool_call_id)
        ]})

    if unit_ids:
        removed = [uid for uid in unit_ids if uid in dead_raw]
        for uid in removed:
            del dead_raw[uid]
        msg = f"已清除死亡单位: {', '.join(removed)}" if removed else "指定的 ID 不在死亡单位列表中。"
    else:
        count = len(dead_raw)
        dead_raw.clear()
        msg = f"已清除全部 {count} 个死亡单位。"

    return Command(update={
        "dead_units": dead_raw,
        "messages": [ToolMessage(content=msg, tool_call_id=tool_call_id)],
    })


@lru_cache(maxsize=1)
def get_tools() -> list[BaseTool]:
    return [
        weather,
        request_dice_roll,
        load_character_profile,
        modify_character_state,
        spawn_monsters,
        start_combat,
        attack_action,
        next_turn,
        end_combat,
        clear_dead_units,
    ]
