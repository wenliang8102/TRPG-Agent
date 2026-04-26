"""Graph node function implementations."""

from functools import lru_cache
from time import perf_counter

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

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

    user_choice = interrupt({
        "type": "player_death",
        "summary": _build_player_death_summary(state.get("messages", [])),
        "hp_changes": list(state.get("hp_changes", [])),
    })

    if player_dict:
        if user_choice == "revive":
            player_dict["hp"] = max(1, player_dict.get("max_hp", 1) // 2)
        else:
            player_dict["hp"] = 0

    result_state: dict = {
        "combat": None,
        "phase": "exploration",
        "messages": [HumanMessage(content="[系统] 玩家角色倒下，战斗结束。")],
        "hp_changes": [],
        "pending_reaction": None,
        "reaction_choice": None,
    }
    if player_dict:
        result_state["player"] = player_dict

    return result_state


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
