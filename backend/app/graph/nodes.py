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
    "5. 怪物回合结算：当你看到 [系统:怪物回合结算] 标记的消息时，"
    "简要向玩家转述关键战果（谁攻击了谁、造成多少伤害），然后询问玩家的行动。\n"
)


@lru_cache(maxsize=1)
def _get_llm_service() -> LLMService:
    """使用 lru_cache 实现单例级别，获取大模型服务"""
    return LLMService()


def router_node(state: GraphState) -> GraphState:
    return {**state}


def assistant_node(state: GraphState) -> dict:
    messages = state.get("messages", [])
    
    # 动态组装附加了上下文的 System Prompt
    system_prompt = ASSISTANT_SYSTEM_PROMPT

    # 注入历史归纳大纲，保持大模型宏观长时记忆
    if summary := state.get("conversation_summary"):
        system_prompt += f"\n\n[前情提要（必须铭记的游戏大纲）]\n{summary}"

    if player := state.get("player"):
        player_context = json.dumps(player, ensure_ascii=False, indent=2)
        system_prompt += f"\n\n[当前玩家状态]\n{player_context}"
    else:
        system_prompt += "\n\n[当前玩家状态]\n玩家尚未加载或创建角色卡。"

    # 注入战斗态势，让 LLM 知道当前行动者、各单位 HP 与可用攻击
    if combat := state.get("combat"):
        combat_data = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat)
        current_id = combat_data.get("current_actor_id", "")
        participants = combat_data.get("participants", {})

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
        system_prompt += "\n\n[当前战斗状态]\n" + "\n".join(combat_lines)

    response = _get_llm_service().invoke_with_tools(
        messages=messages,
        tools=get_tools(),
        system_prompt=system_prompt,
    )

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


def monster_combat_node(state: GraphState) -> dict:
    """怪物/NPC 自动战斗节点：循环执行所有非玩家单位的攻击 + 回合推进，
    直到轮到玩家行动或战斗结束。系统级确定性逻辑，不经 LLM。"""
    from app.services.tool_service import resolve_single_attack, advance_turn

    combat = state.get("combat")
    if not combat:
        return {}

    combat_dict = combat.model_dump() if hasattr(combat, "model_dump") else dict(combat)
    participants = combat_dict.get("participants", {})
    log_lines: list[str] = []

    # 循环：只要当前行动者不是玩家方，就自动执行攻击并推进回合
    max_iterations = len(participants) * 3  # 安全阀防无限循环
    iterations = 0
    while iterations < max_iterations:
        iterations += 1
        current_id = combat_dict.get("current_actor_id", "")
        actor = participants.get(current_id)
        if not actor or actor.get("side") == "player":
            break
        if actor.get("hp", 0) <= 0:
            advance_turn(combat_dict)
            continue

        # 选择第一个存活的玩家单位作为目标
        target = None
        for uid, p in participants.items():
            if p.get("side") == "player" and p.get("hp", 0) > 0:
                target = p
                break

        if not target:
            log_lines.append("所有玩家单位已倒下！")
            break

        # 执行攻击
        atk_lines, _ = resolve_single_attack(actor, target)
        log_lines.extend(atk_lines)
        log_lines.append("")  # 空行分隔

        # 推进回合
        turn_text = advance_turn(combat_dict)
        log_lines.append(turn_text)
        log_lines.append("")

    # 包装为 HumanMessage 以便 LLM 在 ASSISTANT_NODE 中看到并叙述
    combat_report = "[系统:怪物回合结算]\n" + "\n".join(log_lines)

    return {
        "combat": combat_dict,
        "messages": [HumanMessage(content=combat_report)],
    }
