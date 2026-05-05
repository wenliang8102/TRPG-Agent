"""Graph node function implementations."""

from functools import lru_cache
from time import perf_counter

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage

from app.graph.constants import COMBAT_AGENT_MODE, NARRATIVE_AGENT_MODE
from app.graph.state import GraphState
from app.memory.context_assembler import (
    ContextAssembler,
    message_content_to_text as _message_content_to_text,
    trim_model_messages as _trim_projected_messages,
    state_value_to_dict as _state_value_to_dict,
)
from app.prompts import get_assistant_system_prompt
from app.services.llm_service import LLMService
from app.services.tools import get_tool_profile
from app.utils.agent_trace import fail_llm_trace, finish_llm_trace, start_llm_trace


@lru_cache(maxsize=1)
def _get_llm_service() -> LLMService:
    """使用 lru_cache 实现单例级别，获取大模型服务"""
    return LLMService()


@lru_cache(maxsize=1)
def _get_context_assembler() -> ContextAssembler:
    """统一缓存上下文装配器，避免在每轮调用里重复构造。"""
    return ContextAssembler()


def _message_count(state: GraphState) -> int:
    return len(state.get("messages", []))


def _combat_archives_from_state(state: GraphState) -> list[dict]:
    archives: list[dict] = []
    for archive in state.get("combat_archives", []) or []:
        if hasattr(archive, "model_dump"):
            archives.append(archive.model_dump())
        elif hasattr(archive, "items"):
            archives.append(dict(archive))
    return archives


def _build_combat_archive(summary: str, start_index: int, end_index: int) -> dict:
    safe_start = max(start_index, 0)
    safe_end = max(end_index, safe_start)
    return {
        "summary": summary.strip(),
        "start_index": safe_start,
        "end_index": safe_end,
    }


def router_node(state: GraphState) -> dict:
    # Do not return the entire state to avoid duplicate updates in stream_mode="updates"
    return {}


# 探索阶段入口。
def assistant_node(state: GraphState) -> dict:
    return _invoke_assistant(state, mode=NARRATIVE_AGENT_MODE)


# 战斗阶段入口，玩家与怪物回合都走同一 combat assistant。
def combat_assistant_node(state: GraphState) -> dict:
    return _invoke_assistant(state, mode=COMBAT_AGENT_MODE)


def _invoke_assistant(state: GraphState, mode: str) -> dict:
    from app.utils.logger import logger

    assembler = _get_context_assembler()
    base_system_prompt = get_assistant_system_prompt(mode)
    assembled_context = assembler.assemble(state, mode, base_system_prompt=base_system_prompt)
    tools = get_tool_profile(mode)
    session_id = str(state.get("session_id") or "detached")
    phase = state.get("phase")

    logger.info(
        "Assistant invocation mode={} session={} messages={} tools={}",
        mode,
        session_id,
        len(assembled_context.model_input_messages),
        len(tools),
    )

    invocation_id, started_at = start_llm_trace(
        session_id,
        mode=mode,
        phase=phase,
        system_prompt=assembled_context.system_prompt,
        hud_text=assembled_context.hud_text,
        messages=assembled_context.model_input_messages,
        tools=tools,
    )
    started_perf = perf_counter()

    try:
        response = _get_llm_service().invoke_with_tools(
            messages=assembled_context.model_input_messages,
            tools=tools,
            system_prompt=assembled_context.system_prompt,
            mode=mode,
        )
    except Exception as exc:
        fail_llm_trace(
            session_id,
            invocation_id=invocation_id,
            started_at=started_at,
            duration_ms=(perf_counter() - started_perf) * 1000,
            mode=mode,
            phase=phase,
            error=exc,
        )
        raise

    finish_llm_trace(
        session_id,
        invocation_id=invocation_id,
        started_at=started_at,
        duration_ms=(perf_counter() - started_perf) * 1000,
        mode=mode,
        phase=phase,
        response=response,
    )

    response = _keep_first_tool_call(response)

    if hasattr(response, "tool_calls") and response.tool_calls:
        logger.info("LLM called tools session={} tools={}", session_id, response.tool_calls)
    else:
        info_resp = str(getattr(response, "content", ""))[:100]
        logger.info("LLM responded session={} text={}...", session_id, info_resp)

    output = response.content if isinstance(response.content, str) and not response.tool_calls else ""
    return {
        "messages": [response],
        "output": output,
    }


def _keep_first_tool_call(response: BaseMessage) -> BaseMessage:
    """有状态工具必须按轮次吃到最新状态，兼容模型若返回并行工具调用则只执行第一条。"""
    tool_calls = getattr(response, "tool_calls", None)
    if not tool_calls or len(tool_calls) <= 1 or not hasattr(response, "model_copy"):
        return response
    return response.model_copy(update={"tool_calls": [tool_calls[0]]})


def _all_players_down(combat_dict: dict, player_dict: dict | None) -> bool:
    """检查战场上是否已不存在存活的玩家单位。"""
    from app.services.tools._helpers import get_all_combatants

    all_combatants = get_all_combatants(combat_dict, player_dict)
    player_units = [unit for unit in all_combatants.values() if unit.get("side") == "player"]
    if not player_units:
        return False
    return all(unit.get("hp", 0) <= 0 for unit in player_units)


def _build_combat_system_message(log_lines: list[str], attack_roll: dict | None = None) -> HumanMessage:
    """把节点内的怪物/反应结算统一投影成系统战报消息。"""
    from app.services.tools._helpers import build_attack_roll_event_payload

    combat_report = "[系统:怪物行动]\n" + "\n".join(log_lines)
    message_kwargs = {}
    if attack_roll is not None:
        attack_roll_payload = build_attack_roll_event_payload(attack_roll)
        if attack_roll_payload:
            message_kwargs["additional_kwargs"] = {
                "attack_roll": attack_roll_payload,
            }
    return HumanMessage(content=combat_report, **message_kwargs)


def _resolve_counterspell_prompt(
    combat_dict: dict,
    player_dict: dict | None,
    pending_dict: dict,
    reaction_choice: dict,
) -> dict:
    """继续结算一次被玩家法术反制暂停的怪物施法。"""
    from app.monsters.models import MonsterAction
    from app.services.tools._helpers import get_all_combatants, get_combatant
    from app.services.tools.monster_action_resolvers import consume_action_resource, resolve_monster_action
    from app.services.tools.reactions import execute_player_reaction

    actor_id = pending_dict.get("attacker_id", "")
    actor = get_combatant(combat_dict, player_dict, actor_id)
    if not actor:
        result = {"combat": combat_dict, "pending_reaction": None, "reaction_choice": None}
        if player_dict:
            result["player"] = player_dict
        return result

    action = MonsterAction.model_validate(pending_dict["spell_action"])
    from app.spells import get_spell_def

    spell_def = get_spell_def(action.spell_id)
    if not spell_def:
        return {
            "combat": combat_dict,
            "messages": [_build_combat_system_message([f"未知怪物法术: {action.spell_id}。"])],
            "hp_changes": [],
            "pending_reaction": None,
            "reaction_choice": None,
            **({"player": player_dict} if player_dict else {}),
        }
    trigger_spell_level = max(action.slot_level, spell_def["level"])
    target_ids = list(pending_dict.get("target_ids", []))
    all_combatants = get_all_combatants(combat_dict, player_dict)
    # 点选范围法术会在恢复结算时重新按空间展开目标，因此这里必须提供全场索引。
    targets_by_id = all_combatants
    log_lines: list[str] = []
    hp_changes: list[dict] = []

    chosen_spell_id = reaction_choice.get("spell_id")
    if chosen_spell_id and player_dict:
        reaction_context = {
            "trigger_caster_id": actor.get("id", actor_id),
            "trigger_caster_name": actor.get("name", actor_id),
            "trigger_spell_name_cn": spell_def["name_cn"],
            "trigger_spell_level": trigger_spell_level,
            "targets": [actor],
        }
        reaction_result = execute_player_reaction(player_dict, reaction_choice, reaction_context)
        log_lines.extend(reaction_result.lines)
        if reaction_result.blocked_action:
            consume_action_resource(actor, action)
            log_lines.append(f"{actor.get('name', actor_id)} 的 {action.name} 被打断，没有产生效果。")
            result_state: dict = {
                "combat": combat_dict,
                "messages": [_build_combat_system_message(log_lines)],
                "hp_changes": [],
                "pending_reaction": None,
                "reaction_choice": None,
            }
            if player_dict:
                result_state["player"] = player_dict
            return result_state
    else:
        log_lines.append("你放弃了反应。")

    result = resolve_monster_action(
        actor,
        targets_by_id,
        target_ids,
        action,
        {
            "combat": combat_dict,
            **({"player": player_dict} if player_dict else {}),
            **({"space": pending_dict.get("space")} if pending_dict.get("space") else {}),
        },
        target_point=pending_dict.get("target_point"),
    )
    consume_action_resource(actor, action)
    log_lines.extend(result["lines"])
    hp_changes.extend(result.get("hp_changes", []))

    result_state = {
        "combat": combat_dict,
        "messages": [_build_combat_system_message(log_lines)],
        "hp_changes": hp_changes,
        "pending_reaction": None,
        "reaction_choice": None,
    }
    if result.get("space"):
        result_state["space"] = result["space"]
    if player_dict:
        result_state["player"] = player_dict
    return result_state


def _build_player_death_summary(messages: list[BaseMessage]) -> str:
    """玩家团灭时优先复用最近一次真实战报，而不是再造一份占位文本。"""
    for message in reversed(messages):
        content = _message_content_to_text(getattr(message, "content", "")).strip()
        if content:
            return content
    return "[系统:怪物行动]\n所有玩家单位已倒下！"


def combat_resolution_node(state: GraphState) -> dict:
    """战斗后置收束节点：统一处理玩家团灭 interrupt，不再依赖旧 monster 节点。"""
    from langgraph.types import interrupt

    combat_dict = _state_value_to_dict(state.get("combat"))
    if not combat_dict:
        return {}

    player_dict = _state_value_to_dict(state.get("player"))
    if not _all_players_down(combat_dict, player_dict):
        return {}

    death_summary = _build_player_death_summary(state.get("messages", []))
    user_choice = interrupt({
        "type": "player_death",
        "summary": death_summary,
        "hp_changes": list(state.get("hp_changes", [])),
    })

    if player_dict:
        if user_choice == "revive":
            player_dict["hp"] = max(1, player_dict.get("max_hp", 1) // 2)
        else:
            player_dict["hp"] = 0

    archive_summary = "战斗以玩家角色倒下告终。"
    compact_death_summary = death_summary.replace("\n", " | ").strip()
    if compact_death_summary and compact_death_summary != "[系统:怪物行动] | 所有玩家单位已倒下！":
        archive_summary += f" 最后关键战报：{compact_death_summary}"

    combat_archives = _combat_archives_from_state(state)
    active_start = state.get("active_combat_message_start")
    if isinstance(active_start, int):
        combat_archives.append(_build_combat_archive(archive_summary, active_start, _message_count(state)))

    result_state: dict = {
        "combat": None,
        "phase": "exploration",
        "messages": [HumanMessage(content="[系统] 玩家角色倒下，战斗结束。")],
        "hp_changes": [],
        "pending_reaction": None,
        "reaction_choice": None,
        "active_combat_message_start": None,
    }
    if player_dict:
        result_state["player"] = player_dict
    if combat_archives:
        result_state["combat_archives"] = combat_archives

    return result_state


def resolve_reaction_node(state: GraphState) -> dict:
    """继续结算一条已暂停的攻击；只处理反应和伤害，不隐式结束行动者回合。"""
    from app.services.tools._helpers import get_combatant, apply_attack_damage, compute_ac
    from app.monsters.models import MonsterAction
    from app.services.tools.monster_action_resolvers import consume_action_resource, resolve_monster_attack_from_roll
    from app.services.tools.reactions import execute_player_reaction

    combat = state.get("combat")
    pending_reaction = state.get("pending_reaction")
    if not combat or not pending_reaction:
        return {"pending_reaction": None, "reaction_choice": None}

    combat_dict = _state_value_to_dict(combat)
    player_dict = _state_value_to_dict(state.get("player"))
    pending_dict = _state_value_to_dict(pending_reaction)
    reaction_choice = _state_value_to_dict(state.get("reaction_choice")) or {"spell_id": None}

    if pending_dict.get("trigger") == "on_enemy_cast":
        return _resolve_counterspell_prompt(combat_dict, player_dict, pending_dict, reaction_choice)

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

    if pending_dict.get("monster_attack_action"):
        action = MonsterAction.model_validate(pending_dict["monster_attack_action"])
        attack_result = resolve_monster_attack_from_roll(
            actor,
            target,
            action,
            {
                "combat": combat_dict,
                **({"player": player_dict} if player_dict else {}),
                **({"space": state.get("space")} if state.get("space") else {}),
            },
            roll_info,
        )
        consume_action_resource(actor, action)
        log_lines.extend(attack_result["lines"])
        hp_changes.extend(attack_result.get("hp_changes", []))
    else:
        atk_lines, _, hp_change, _ = apply_attack_damage(actor, target, roll_info)
        log_lines.extend(atk_lines)
        if hp_change:
            hp_changes.append(hp_change)

    result_state: dict = {
        "combat": combat_dict,
        "messages": [_build_combat_system_message(log_lines, attack_roll=roll_info)],
        "hp_changes": hp_changes,
        "pending_reaction": None,
        "reaction_choice": None,
    }
    if player_dict:
        result_state["player"] = player_dict
    return result_state
