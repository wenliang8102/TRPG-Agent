"""Tool definitions — 掷骰 + 战斗动作工具链"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

import d20

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, interrupt

from app.calculation.predefined_characters import PREDEFINED_CHARACTERS
from app.calculation.bestiary import spawn_combatants
from app.calculation.abilities import ability_to_modifier
from app.graph.state import AttackInfo, CombatState, CombatantState


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
        
    # 挂起 Graph 并将请求下发前端呈现按钮
    user_response = interrupt({
        "type": "dice_roll",
        "reason": reason,
        "ability": ability,
        "formula": formula,
    })
    
    if user_response == "confirmed":
        # 使用 d20 库解析并执行掷骰
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
    
    return {"status": "failed", "note": "玩家拒绝了掷骰或动作未知。"}


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
    """根据怪物图鉴生成战斗单位实例并加入当前战场环境。
    怪物数据来自 Open5e SRD（使用英文 slug，如 "goblin", "owlbear", "adult-red-dragon"）。

    Args:
        monster_index: 怪物的 Open5e slug（如 "goblin"）。必须输入其英文代号。
        count: 生成该单位的数量。默认为 1。
        faction: 阵营，通常为 "enemy", "ally" 或 "neutral"。默认 "enemy"。
    """
    try:
        new_combatants = spawn_combatants(monster_index, count, faction)
    except Exception as e:
        return f"生成战斗单位失败: {str(e)}"

    existing = state.get("combat")
    if existing and hasattr(existing, "model_dump"):
        combat_dict = existing.model_dump()
    elif existing:
        combat_dict = dict(existing)
    else:
        combat_dict = {"round": 0, "participants": {}, "initiative_order": [], "current_actor_id": ""}

    for c in new_combatants:
        combat_dict["participants"][c.id] = c.model_dump()

    names = [f"{c.name} [ID: {c.id}]" for c in new_combatants]

    return Command(
        update={
            "combat": combat_dict,
            "messages": [
                ToolMessage(
                    content=f"成功在战场中生成了 {count} 只 {monster_index}，ID/名字分别为: {', '.join(names)}",
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
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """开始战斗：为所有已在战场中的参与者投先攻骰并排定行动顺序。
    前置条件：必须先用 spawn_monsters 生成至少一个战斗单位。
    """
    combat_raw = state.get("combat")
    if not combat_raw:
        return "当前没有任何战斗单位。请先使用 spawn_monsters 工具生成怪物。"

    combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
    participants = combat_dict.get("participants", {})
    if not participants:
        return "战场上没有参与者，请先生成怪物。"

    # 玩家自动入场：将已加载的角色卡转为战斗单位
    player_raw = state.get("player")
    if player_raw:
        player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw)
        player_id = f"player_{player_dict.get('name', 'player')}"
        if player_id not in participants:
            participants[player_id] = _build_player_combatant(player_dict)

    # 为每个参战单位投先攻
    initiative_list: list[tuple[str, int]] = []
    for uid, p in participants.items():
        dex_mod = p.get("modifiers", {}).get("dex", 0) if isinstance(p, dict) else getattr(p, "modifiers", {}).get("dex", 0)
        init_roll = d20.roll(f"1d20+{dex_mod}")
        p["initiative"] = init_roll.total
        initiative_list.append((uid, init_roll.total))

    # 按先攻降序排列
    initiative_list.sort(key=lambda x: x[1], reverse=True)
    order = [uid for uid, _ in initiative_list]

    combat_dict["round"] = 1
    combat_dict["initiative_order"] = order
    combat_dict["current_actor_id"] = order[0]

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

    # 玩家攻击需要前端确认掷骰
    if attacker.get("side") == "player":
        # 先确定武器名用于弹窗展示
        attacks = attacker.get("attacks", [])
        if attack_name:
            chosen = next((a for a in attacks if a["name"].lower() == attack_name.lower()), None)
        else:
            chosen = attacks[0] if attacks else None
        display_name = chosen["name"] if chosen else "徒手攻击"
        atk_bonus = chosen["attack_bonus"] if chosen else 0

        user_response = interrupt({
            "type": "dice_roll",
            "reason": f"{attacker.get('name', attacker_id)} 使用 [{display_name}] 攻击 {target.get('name', target_id)}",
            "formula": f"1d20+{atk_bonus}",
        })
        if user_response != "confirmed":
            return Command(update={"messages": [
                ToolMessage(content="玩家取消了攻击。", tool_call_id=tool_call_id)
            ]})

    # 委托核心计算函数
    lines, _, hp_change, extra_info = resolve_single_attack(attacker, target, attack_name, advantage)

    update: dict = {
        "combat": combat_dict,
        "messages": [
            ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)
        ],
    }
    
    # 额外附加攻击判定的原始骰值数据以供前端 3D 骰子捕获
    if "raw_roll" in extra_info:
        # 我们把骰子总数据加到 messages 的 additional_kwargs 或者通过把 content 变 dict 来传给前端
        # 但 LangChain 推荐 ToolMessage 就是 string。那我们在额外返回一条隐藏信息的 ToolMessage 专门装 DiceResult 吗？
        # 更简洁做法：我们把这个 ToolMessage 的附加参数带上，然后在 chat_session_service 拦截？
        pass
        
    # 为了方便对接前一回合的 JSON 检测，我们修改 `chat_session_service.py` 时可以通过读取 ToolMessage.artifact 拿信息（需要 langchain 0.2）
    # 或者就附带一段隐藏 JSON。我们使用 artifact：
    tool_msg = ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)
    tool_msg.artifact = {"raw_roll": extra_info.get("raw_roll")}
    
    update["messages"] = [tool_msg]

    if hp_change:
        update["hp_changes"] = [hp_change]
        # 【修复BUG】同步更新角色的实际状态对象，防止由于状态脱节导致虚空回血
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
    """结束当前战斗，清理战斗状态并返回总结。"""
    combat_raw = state.get("combat")
    summary = "战斗结束。"
    if combat_raw:
        combat_dict = combat_raw.model_dump() if hasattr(combat_raw, "model_dump") else dict(combat_raw)
        rounds = combat_dict.get("round", 0)
        participants = combat_dict.get("participants", {})
        alive = [p.get("name", uid) for uid, p in participants.items() if p.get("hp", 0) > 0]
        fallen = [p.get("name", uid) for uid, p in participants.items() if p.get("hp", 0) <= 0]
        parts = [f"共进行了 {rounds} 回合。"]
        if alive:
            parts.append(f"存活: {', '.join(alive)}")
        if fallen:
            parts.append(f"倒下: {', '.join(fallen)}")
        summary = " ".join(parts)

    return Command(
        update={
            "combat": None,
            "phase": "exploration",
            "messages": [
                ToolMessage(content=summary, tool_call_id=tool_call_id)
            ],
        }
    )


@lru_cache(maxsize=1)
def get_tools() -> list[BaseTool]:
    return [
        weather,
        request_dice_roll,
        load_character_profile,
        spawn_monsters,
        start_combat,
        attack_action,
        next_turn,
        end_combat,
    ]
