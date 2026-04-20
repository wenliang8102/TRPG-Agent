"""Graph node function implementations."""

import json
from functools import lru_cache

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, RemoveMessage

from app.graph.state import GraphState
from app.services.llm_service import LLMService
from app.services.tool_service import get_tools

ASSISTANT_SYSTEM_PROMPT = (
    "你是一个专业的 TRPG 游戏核心主持人（DM/GM）。"
    "你的职责是推动剧情发展、回应玩家的探索与交互。"
    "在需要判定、对抗、查询角色属性等处理外部客观事实时，请务必使用工具（Tools）；如果玩家只是闲聊或剧情对话，请直接回复。\n\n"

    "【行动准则】\n"
    "1. 工具优先：当你决定执行一个动作时（攻击、掷骰、生成怪物等），立即调用对应工具。"
    "绝对不要先输出「我将使用…」之类的预告文本再调用工具，这会造成冗余延迟。\n"
    "2. 回合意识：在调用 attack_action 之前，必须核对下方注入的战斗状态中的 current_actor_id 字段，"
    "确认当前行动者确实是你要操作的单位。不要盲目发起攻击。\n"
    "3. 武器真实性：攻击时只能使用战斗状态中该单位 attacks 列表里实际存在的武器名称，"
    "不要编造或猜测武器名。\n"
    "4. 战斗简洁模式：在战斗阶段（phase=combat），使用简洁的播报风格，1-2 句话概括工具返回的结果即可。"
    "不要使用表情符号，不要输出大段剧情描写。\n"
    "5. 怪物回合结算：当你看到 [系统:怪物行动] 或 [系统:怪物回合结算] 标记的消息时，"
    "简要向玩家转述关键战果（谁攻击了谁、造成多少伤害），然后询问玩家的行动。\n"
    "6. 工具结果权威性：当你调用任何战斗工具（attack_action, next_turn 等）后，"
    "工具返回的结果是唯一的事实来源。如果工具返回错误信息（如'不是你的回合'、'动作已用尽'），"
    "你必须如实向玩家转达该错误，绝对不能忽略错误而自行编造攻击效果。\n"
    "7. 禁止虚构战斗结果：在战斗阶段，你绝不可以在没有成功调用 attack_action 工具的情况下"
    "描述任何攻击命中、伤害或 HP 变化。所有战斗数值必须来自工具返回。\n"
    "8. 状态变更规范：所有涉及角色 HP、AC、能力值、状态效果等变化，必须通过 modify_character_state 工具执行，"
    "不要自行编造数值后果。\n"
    "9. 场景单位管理：spawn_monsters 生成的单位进入场景单位池。开战前你需要获取可用单位 ID 列表，"
    "并通过 start_combat 的 combatant_ids 参数指定参战者。未参战单位仍保留在场景中。\n"
    "10. 死亡单位：战斗结束后，死亡单位会归入死亡档案。若玩家希望搜刮尸体等，可描述剧情后使用 clear_dead_units 清理。\n"
    "11. 法术施放：使用 cast_spell 工具施放法术，系统自动处理法术位消耗、命中/豁免判定和伤害/治疗计算。"
    "施法前确认角色已知该法术且有足够法术位。反应法术（如护盾术）可在任意单位回合施放。\n"
    "12. 单位查询：使用 inspect_unit 查看任意单位完整属性（HP、AC、攻击列表、法术位等）。"
    "在需要了解目标详情时使用此工具，而非编造数据。\n"
    "13. 资源管理：法术位等资源通过 cast_spell 自动消耗。如需手动调整（如长休恢复法术位），"
    "使用 modify_character_state 的 resource_delta 或 set_resource 键。\n"
)


@lru_cache(maxsize=1)
def _get_llm_service() -> LLMService:
    """使用 lru_cache 实现单例级别，获取大模型服务"""
    return LLMService()


def router_node(state: GraphState) -> dict:
    # Do not return the entire state to avoid duplicate updates in stream_mode="updates"
    return {}


def assistant_node(state: GraphState) -> dict:
    messages = state.get("messages", [])
    
    # 动态组装附加了上下文的 System Prompt
    system_prompt = ASSISTANT_SYSTEM_PROMPT

    # 注入历史归纳大纲，保持大模型宏观长时记忆
    if summary := state.get("conversation_summary"):
        system_prompt += f"\n\n[前情提要（必须铭记的游戏大纲）]\n{summary}"

    hud_text = ""
    if player := state.get("player"):
        player_context = json.dumps(player, ensure_ascii=False, indent=2)
        hud_text += f"\n\n[当前玩家状态]\n{player_context}"
    else:
        hud_text += "\n\n[当前玩家状态]\n玩家尚未加载或创建角色卡。"

    # 注入战斗态势，让 LLM 知道当前行动者、各单位 HP 与可用攻击
    if combat := state.get("combat"):
        combat_data = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat)
        current_id = combat_data.get("current_actor_id", "")
        participants = dict(combat_data.get("participants", {}))

        # 将玩家纳入 HUD 显示（玩家不在 participants 中，但是参战者）
        if player := state.get("player"):
            pd = player.model_dump() if hasattr(player, "model_dump") else dict(player)
            if pd.get("id"):
                participants[pd["id"]] = pd

        combat_lines = [
            f"第 {combat_data.get('round', '?')} 回合 | 当前行动者: {current_id}",
            f"先攻顺序: {combat_data.get('initiative_order', [])}",
        ]
        for uid, p in participants.items():
            attacks_desc = ", ".join(a.get("name", "?") for a in p.get("attacks", []))
            marker = " ← 当前行动" if uid == current_id else ""
            combat_lines.append(
                f"  {p.get('name', uid)} [ID:{uid}] side={p.get('side')} "
                f"HP:{p.get('hp')}/{p.get('max_hp')} AC:{p.get('ac')} "
                f"attacks=[{attacks_desc}]{marker}"
            )
        hud_text += "\n\n[当前战斗状态]\n" + "\n".join(combat_lines)

    # 注入场景单位池，让 LLM 知道可用战斗单位
    if scene_units := state.get("scene_units"):
        scene_data = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in scene_units.items()} if hasattr(scene_units, "items") else scene_units
        if scene_data:
            scene_lines = [f"  {uid}: {p.get('name', uid)} (side={p.get('side')}, HP:{p.get('hp')}/{p.get('max_hp')})" for uid, p in scene_data.items()]
            hud_text += "\n\n[场景单位池（可用 start_combat 指定参战）]\n" + "\n".join(scene_lines)

    # 注入死亡单位档案
    if dead_units := state.get("dead_units"):
        dead_data = {k: v.model_dump() if hasattr(v, "model_dump") else dict(v) for k, v in dead_units.items()} if hasattr(dead_units, "items") else dead_units
        if dead_data:
            dead_lines = [f"  {uid}: {p.get('name', uid)}" for uid, p in dead_data.items()]
            hud_text += "\n\n[死亡单位档案]\n" + "\n".join(dead_lines)

    hud_text = "\n\n=== 实时系统监控窗(HUD) ===\n" + hud_text.strip() + "\n===========================\n"

    invoke_messages = list(messages)
    if invoke_messages:
        import copy
        last_msg = invoke_messages[-1]
        modified_msg = copy.copy(last_msg)
        
        if isinstance(modified_msg.content, str):
            modified_msg.content = modified_msg.content + hud_text
        elif isinstance(modified_msg.content, list):
            modified_msg.content.append({"type": "text", "text": hud_text})
            
        invoke_messages[-1] = modified_msg

    from app.utils.logger import logger
    logger.info("=== [Assistant Node Invocation] ===")
    logger.debug(f"HUD Info [injected into latest message]:\n{hud_text}")
    
    # Elegant formatting for Dialogue History / Tool Returns
    logger.debug("--- [Message Dialogue & Context History] ---")
    for i, msg in enumerate(invoke_messages):
        msg_type = msg.__class__.__name__
        content_preview = str(msg.content)[:200] + "..." if len(str(msg.content)) > 200 else msg.content
        logger.debug(f"Msg {i} [{msg_type}]: {content_preview}")

    response = _get_llm_service().invoke_with_tools(
        messages=invoke_messages,
        tools=get_tools(),
        system_prompt=system_prompt,
    )
    
    if hasattr(response, "tool_calls") and response.tool_calls:
        logger.info(f"LLM Called Tools -> {response.tool_calls}")
    else:
        info_resp = str(getattr(response, 'content', ''))[:100]
        logger.info(f"LLM Responsed [Text] -> {info_resp}...")

    output = response.content if isinstance(response.content, str) and not response.tool_calls else ""
    return {
        "messages": [response],
        "output": output,
    }


def summarize_conversation_node(state: GraphState) -> dict:
    """清理冗长对话记录：归纳极其老旧的消息，并发送指令将其丢弃卸载，释放窗口 token"""
    messages = state.get("messages", [])
    
    # 我们期望在截断时，至少在本地视窗中安全地留下最新的对局。此处放大为 20 条记录。
    keep_count = 20
    
    if len(messages) <= keep_count:
        return {}

    # 从右往左追溯：防范拦腰斩断 ToolMessage 导致 API 校验报 400 Bad Request 错误！
    while keep_count < len(messages):
        first_kept_msg = messages[-keep_count]
        if isinstance(first_kept_msg, ToolMessage):
            # 将指针向左扩大 1 格，强行将它的父级发起者（含 tool_calls 的 AIMessage）纳入保留区。
            keep_count += 1
            continue
        break
        
    msgs_to_summarize = messages[:-keep_count]
    if not msgs_to_summarize:
        return {}

    # 将该段早期的历史聊天交给 LLM 获取压缩大纲
    current_summary = state.get("conversation_summary", "")
    summary_prompt = (
        "这是一段 TRPG 游戏过往的部分对话。请将其客观浓缩，提炼出关键行为、物资增减及主干剧情。\n"
        "保持精简，拒绝任何修饰语。这段记录将被遗忘，此总结将作为继承给未来的核心大纲：\n\n"
    )
    
    if current_summary:
        summary_prompt = f"之前累积的大纲如下：\n{current_summary}\n\n" + summary_prompt
        
    summary_prompt += "【新发生的即将被遗弃的历史对话】\n"
    for m in msgs_to_summarize:
        role = m.__class__.__name__.replace("Message", "")
        # 处理 list 等复合 content
        content = m.content
        if isinstance(content, list):
            content = " ".join(str(x) for x in content if isinstance(x, str) or (isinstance(x, dict) and x.get("text")))
        summary_prompt += f"[{role}]: {content}\n"
        
    response = _get_llm_service().invoke_with_tools(
        messages=[HumanMessage(content=summary_prompt)],
        tools=[],  # 纯提炼节点，关闭 tool 防发散
        system_prompt="你是一个极度严谨的记忆整理员。请生成一段客观、简短且剥离所有情感修饰的核心内容摘要，帮助系统长效保存历史进程。",
    )
    
    new_summary = str(response.content).strip()
    
    # 利用原生的 RemoveMessage 发送销毁指令给 StateGraph Checkpointer。必须要确保 m.id 存在才能销毁（langchain > 0.2 原生全带 ID）
    delete_msgs = [RemoveMessage(id=m.id) for m in msgs_to_summarize if getattr(m, "id", None)]
    
    return {
        "conversation_summary": new_summary,
        "messages": delete_msgs
    }


def _state_value_to_dict(value):
    """将图状态中的 Pydantic/映射对象统一转成 dict。"""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


def _build_pending_reaction(actor: dict, target: dict, roll_info: dict, available: list[dict]) -> dict:
    """把一次待决反应所需的全部事实快照固化进状态。"""
    return {
        "type": "reaction_prompt",
        "trigger": "on_hit",
        "attacker_id": actor.get("id", ""),
        "attacker_name": actor.get("name", ""),
        "target_id": target.get("id", ""),
        "target_name": target.get("name", ""),
        "attack_roll": dict(roll_info),
        "available_reactions": list(available),
    }


def _all_players_down(combat_dict: dict, player_dict: dict | None) -> bool:
    """检查战场上是否已不存在存活的玩家单位。"""
    from app.services.tools._helpers import get_all_combatants

    all_combatants = get_all_combatants(combat_dict, player_dict)
    player_units = [unit for unit in all_combatants.values() if unit.get("side") == "player"]
    if not player_units:
        return False
    return all(unit.get("hp", 0) <= 0 for unit in player_units)


def _finalize_monster_combat_state(
    combat_dict: dict,
    player_dict: dict | None,
    log_lines: list[str],
    hp_changes: list[dict],
    attack_roll: dict | None = None,
) -> dict:
    """统一封装怪物战斗结算输出，兼顾玩家倒地中断与命中骰展示。"""
    from langgraph.types import interrupt
    from app.services.tools._helpers import build_attack_roll_event_payload

    if _all_players_down(combat_dict, player_dict):
        combat_report = "[系统:怪物行动]\n" + "\n".join(log_lines)
        user_choice = interrupt({
            "type": "player_death",
            "summary": combat_report,
            "hp_changes": hp_changes,
        })

        if player_dict:
            if user_choice == "revive":
                player_dict["hp"] = max(1, player_dict.get("max_hp", 1) // 2)
            else:
                player_dict["hp"] = 0

        return {
            "combat": None,
            "player": player_dict,
            "phase": "exploration",
            "messages": [HumanMessage(content="[系统] 玩家角色倒下，战斗结束。")],
            "hp_changes": [],
            "pending_reaction": None,
            "reaction_choice": None,
        }

    combat_report = "[系统:怪物行动]\n" + "\n".join(log_lines)
    message_kwargs = {}
    if attack_roll is not None:
        attack_roll_payload = build_attack_roll_event_payload(attack_roll)
        if attack_roll_payload:
            message_kwargs["additional_kwargs"] = {
                "attack_roll": attack_roll_payload,
            }
    msg = HumanMessage(content=combat_report, **message_kwargs)

    result_state: dict = {
        "combat": combat_dict,
        "messages": [msg],
        "hp_changes": hp_changes,
        "pending_reaction": None,
        "reaction_choice": None,
    }
    if player_dict:
        result_state["player"] = player_dict

    return result_state


def monster_combat_node(state: GraphState) -> dict:
    """怪物/NPC 自动战斗节点：只处理当前一个非玩家单位的攻击 + 推进回合。
    命中玩家后若存在可用反应，则只写入待决反应状态，由后续节点解析。
    graph 条件边控制循环：下一个仍是怪物则再次进入本节点。"""
    from app.services.tools._helpers import (
        advance_turn, get_all_combatants, get_combatant,
        roll_attack_hit, apply_attack_damage,
    )
    from app.services.tools.reactions import get_available_reactions

    combat = state.get("combat")
    if not combat:
        return {}

    combat_dict = _state_value_to_dict(combat)
    participants = combat_dict.get("participants", {})

    player_dict = _state_value_to_dict(state.get("player"))

    current_id = combat_dict.get("current_actor_id", "")
    actor = participants.get(current_id)

    if not actor or actor.get("side") == "player":
        result = {"combat": combat_dict, "pending_reaction": None, "reaction_choice": None}
        if player_dict:
            result["player"] = player_dict
        return result

    log_lines: list[str] = []
    hp_changes: list[dict] = []
    attack_roll: dict | None = None

    if actor.get("hp", 0) <= 0:
        turn_text = advance_turn(combat_dict, player_dict)
        log_lines.append(turn_text)
    else:
        # 选择第一个存活的玩家单位作为目标
        target = None
        all_combatants = get_all_combatants(combat_dict, player_dict)
        for uid, p in all_combatants.items():
            if p.get("side") == "player" and p.get("hp", 0) > 0:
                target = p
                break

        if not target:
            log_lines.append("所有玩家单位已倒下！")
        else:
            roll_info = roll_attack_hit(actor, target)
            attack_roll = dict(roll_info)

            if roll_info.get("hit") and not roll_info.get("blocked") and target is player_dict:
                reaction_context = {
                    "attacker": actor.get("name", current_id),
                    "attack_roll": {
                        "raw_roll": roll_info.get("raw_roll", roll_info.get("natural", 0)),
                        "attack_bonus": roll_info.get("attack_bonus", 0),
                        "final_total": roll_info["hit_total"],
                        "hit_total": roll_info["hit_total"],
                        "target_ac": roll_info["target_ac"],
                    },
                }
                available = get_available_reactions(player_dict, "on_hit", reaction_context)
                if available:
                    result_state: dict = {
                        "combat": combat_dict,
                        "pending_reaction": _build_pending_reaction(actor, target, roll_info, available),
                        "reaction_choice": None,
                    }
                    if player_dict:
                        result_state["player"] = player_dict
                    return result_state

            atk_lines, _, hp_change, _ = apply_attack_damage(actor, target, roll_info)
            log_lines.extend(atk_lines)
            if hp_change:
                hp_changes.append(hp_change)

            turn_text = advance_turn(combat_dict, player_dict)
            log_lines.append(turn_text)

    return _finalize_monster_combat_state(combat_dict, player_dict, log_lines, hp_changes, attack_roll=attack_roll)


def resolve_reaction_node(state: GraphState) -> dict:
    """继续结算一条已暂停的怪物攻击，并应用玩家的反应选择。"""
    from app.services.tools._helpers import advance_turn, get_combatant, apply_attack_damage, compute_ac
    from app.services.tools.reactions import execute_player_reaction

    combat = state.get("combat")
    pending_reaction = state.get("pending_reaction")
    if not combat or not pending_reaction:
        return {"pending_reaction": None, "reaction_choice": None}

    combat_dict = _state_value_to_dict(combat)
    player_dict = _state_value_to_dict(state.get("player"))
    pending_dict = _state_value_to_dict(pending_reaction)
    reaction_choice = _state_value_to_dict(state.get("reaction_choice")) or {"spell_id": None}

    attacker_id = pending_dict.get("attacker_id", "")
    target_id = pending_dict.get("target_id", "")
    actor = get_combatant(combat_dict, player_dict, attacker_id)
    target = get_combatant(combat_dict, player_dict, target_id)
    if not actor or not target:
        result = {
            "combat": combat_dict,
            "pending_reaction": None,
            "reaction_choice": None,
        }
        if player_dict:
            result["player"] = player_dict
        return result

    roll_info = dict(pending_dict.get("attack_roll", {}))
    log_lines: list[str] = []
    hp_changes: list[dict] = []

    reaction_context = {
        "attacker": pending_dict.get("attacker_name", actor.get("name", attacker_id)),
        "attack_roll": {
            "raw_roll": roll_info.get("raw_roll", roll_info.get("natural", 0)),
            "attack_bonus": roll_info.get("attack_bonus", 0),
            "final_total": roll_info.get("hit_total", 0),
            "hit_total": roll_info.get("hit_total", 0),
            "target_ac": roll_info.get("target_ac", 10),
        },
    }

    chosen_spell_id = reaction_choice.get("spell_id")
    if chosen_spell_id and player_dict:
        reaction_result = execute_player_reaction(player_dict, reaction_choice, reaction_context)
        log_lines.extend(reaction_result.lines)

        if reaction_result.modifies_ac:
            new_ac = compute_ac(player_dict)
            roll_info["target_ac"] = new_ac
            if roll_info.get("natural") != 20 and roll_info.get("hit_total", 0) < new_ac:
                roll_info["hit"] = False
                roll_info["crit"] = False
                if roll_info.get("lines"):
                    roll_info["lines"][-1] = f"命中骰总值: {roll_info['hit_total']} vs AC {new_ac}（反应法术生效，未命中！）"
            elif roll_info.get("lines"):
                if roll_info.get("natural") == 20:
                    detail = "天然 20，反应法术无法改判！"
                else:
                    detail = "反应法术生效，但仍然命中！"
                roll_info["lines"][-1] = f"命中骰总值: {roll_info['hit_total']} vs AC {new_ac}（{detail}）"
    else:
        log_lines.append("你放弃了反应。")

    atk_lines, _, hp_change, _ = apply_attack_damage(actor, target, roll_info)
    log_lines.extend(atk_lines)
    if hp_change:
        hp_changes.append(hp_change)

    turn_text = advance_turn(combat_dict, player_dict)
    log_lines.append(turn_text)

    return _finalize_monster_combat_state(combat_dict, player_dict, log_lines, hp_changes, attack_roll=roll_info)
