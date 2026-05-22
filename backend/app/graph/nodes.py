"""Graph node function implementations."""

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
import json
from functools import lru_cache
from time import perf_counter
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from app.config.settings import settings
from app.graph.constants import COMBAT_AGENT_MODE, NARRATIVE_AGENT_MODE, STATE_DEATH_SAVE_PAUSE_TURN_KEY
from app.graph.state import GraphState
from app.memory.context_assembler import (
    ContextAssembler,
    build_runtime_state_message,
    is_internal_system_human_message,
    is_runtime_state_message,
    _latest_external_human_text,
    message_content_to_text as _message_content_to_text,
    state_value_to_dict as _state_value_to_dict,
)
from app.prompts import get_assistant_system_prompt
from app.rag.auto_rule_evidence import AutoRuleEvidenceEvaluation, evaluate_auto_rule_evidence, mark_auto_rule_rag_timed_out
from app.services.llm_service import LLMService
from app.services.tools import get_tool_profile
from app.utils.agent_trace import (
    fail_llm_trace,
    finish_llm_trace,
    start_auto_rule_rag_trace,
    start_llm_trace,
    timeout_auto_rule_rag_trace,
)


_OPTIONAL_RAG_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="auto-rule-rag")


@dataclass(frozen=True, slots=True)
class OptionalRuleRagTask:
    """保存机会型 RAG 后台任务和 trace 元数据，超时后仍可对齐同一 invocation。"""

    future: Future
    invocation_id: str
    started_at: str
    started_perf: float
    query: str
    top_k: int
    timeout_ms: int


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


def combat_start_node(state: GraphState) -> dict:
    """执行 LLM 提交的开战计划；地图、落点、先攻与突袭统一由 workflow 收束。"""
    from langchain_core.messages import ToolMessage

    from app.services.tools.combat_tools import _execute_start_combat

    pending = _state_value_to_dict(state.get("pending_combat_start"))
    if not pending:
        return {}

    workflow_state = dict(state)
    layout_result = _apply_combat_start_layout(
        pending,
        workflow_state,
        tool_call_id="workflow-combat-start",
    )
    if layout_result:
        layout_result["pending_combat_start"] = None
        return layout_result

    result = _execute_start_combat(
        [str(unit_id) for unit_id in pending.get("combatant_ids", [])],
        [str(unit_id) for unit_id in pending.get("surprised_ids", [])],
        state=workflow_state,
        tool_call_id="workflow-combat-start",
    )

    if isinstance(result, Command):
        update = dict(result.update or {})
    elif isinstance(result, str):
        update = {"messages": [ToolMessage(content=result, tool_call_id="workflow-combat-start")]}
    else:
        update = {}

    update["pending_combat_start"] = None
    if pending.get("map_plan") or pending.get("placements"):
        update["space"] = workflow_state.get("space")
    if pending.get("reason") and update.get("messages"):
        message = update["messages"][0]
        if isinstance(message, ToolMessage) and not str(message.content).startswith("无法开始战斗"):
            update["messages"][0] = ToolMessage(
                content=f"开战裁定：{pending['reason']}\n{message.content}",
                tool_call_id=message.tool_call_id,
                name=message.name,
                additional_kwargs=message.additional_kwargs,
            )
    return update


def combat_end_node(state: GraphState) -> dict:
    """执行战斗收束计划；单位结局分类必须先通过 workflow 校验。"""
    from app.services.tools.combat_tools import end_combat

    pending = _state_value_to_dict(state.get("pending_combat_end"))
    if not pending:
        return {}

    plan = _build_combat_end_plan(pending, state)
    if "messages" in plan:
        plan["pending_combat_end"] = None
        return plan

    result = end_combat.invoke({
        "name": end_combat.name,
        "args": {
            "departed_unit_ids": plan["departed_unit_ids"],
            "defeated_unit_ids": plan["defeated_unit_ids"],
            "state": dict(state),
        },
        "id": "workflow-combat-end",
        "type": "tool_call",
    })
    update = dict(result.update or {}) if isinstance(result, Command) else {}
    update["pending_combat_end"] = None
    if pending.get("reason") and update.get("messages"):
        message = update["messages"][0]
        if isinstance(message, ToolMessage):
            update["messages"][0] = ToolMessage(
                content=f"战斗收束裁定：{pending['reason']}\n{message.content}",
                tool_call_id=message.tool_call_id,
                name=message.name,
                additional_kwargs=message.additional_kwargs,
            )
    return update


def _build_combat_end_plan(pending: dict, state: GraphState) -> dict:
    """把剧情结局翻译成底层 end_combat 的 defeated/departed 参数。"""
    combat = _state_value_to_dict(state.get("combat"))
    if not combat:
        return _combat_end_error("无法结束战斗：当前不在战斗中。")

    participants = _state_value_to_dict(combat.get("participants")) or {}
    outcomes = _normalize_combat_end_outcomes(pending.get("outcomes") or [])
    unknown_ids = sorted(unit_id for unit_id in outcomes if unit_id not in participants)
    if unknown_ids:
        available = ", ".join(participants.keys()) or "无"
        return _combat_end_error(f"无法结束战斗：结局包含未知单位 {', '.join(unknown_ids)}。可用单位: {available}")

    departed_ids: set[str] = set()
    defeated_ids: set[str] = set()
    missing: list[str] = []

    for unit_id, unit in participants.items():
        unit = _state_value_to_dict(unit)
        side = str(unit.get("side") or "")
        hp = int(unit.get("hp", 0) or 0)
        result = outcomes.get(unit_id, "")
        if side == "ally":
            continue
        if side != "enemy":
            continue
        if hp <= 0:
            defeated_ids.add(unit_id)
            continue
        if result in _COMBAT_END_DEFEATED_RESULTS:
            defeated_ids.add(unit_id)
            departed_ids.add(unit_id)
            continue
        if result in _COMBAT_END_DEPARTED_RESULTS:
            departed_ids.add(unit_id)
            continue
        missing.append(unit_id)

    if missing:
        labels = ", ".join(f"{unit_id}({participants[unit_id].get('name', unit_id)})" for unit_id in missing)
        return _combat_end_error(
            "无法结束战斗：仍存活的敌方单位缺少结局分类。"
            f" 请标注为 captured/surrendered/subdued/defeated 以获得 XP，或 fled/escaped/retreated/departed 表示逃离不给 XP。缺少: {labels}"
        )

    return {
        "departed_unit_ids": sorted(departed_ids),
        "defeated_unit_ids": sorted(defeated_ids),
    }


_COMBAT_END_DEFEATED_RESULTS = {
    "captured",
    "capture",
    "dead",
    "defeated",
    "destroyed",
    "killed",
    "nonlethal",
    "routed",
    "slain",
    "subdued",
    "surrender",
    "surrendered",
}
_COMBAT_END_DEPARTED_RESULTS = {"fled", "escaped", "retreated", "departed", "left", "teleported", "withdrew"}


def _normalize_combat_end_outcomes(outcomes: list[dict]) -> dict[str, str]:
    """结局字段允许 result/outcome/status 等别名，降低模型填参摩擦。"""
    normalized: dict[str, str] = {}
    for item in outcomes:
        if not isinstance(item, dict):
            continue
        unit_id = str(item.get("unit_id") or item.get("id") or "").strip()
        result = str(item.get("result") or item.get("outcome") or item.get("status") or "").strip().lower()
        if unit_id:
            normalized[unit_id] = result
    return normalized


def _combat_end_error(content: str) -> dict:
    """统一构造结束战斗 workflow 的失败消息。"""
    return {"messages": [ToolMessage(content=content, tool_call_id="workflow-combat-end")]}


def _apply_combat_start_layout(pending: dict, state: dict, *, tool_call_id: str) -> dict | None:
    """按 LLM 的遭遇布置更新地图和落点；错误直接返回可见失败消息。"""
    from langchain_core.messages import ToolMessage
    from app.graph.state import PlaneMapState, Point2D, SpaceState, UnitPlacementState
    from app.services.tools._helpers import resolve_player_reference_id
    from app.space.geometry import build_space_state, point_in_map

    space = build_space_state(state.get("space"))
    map_plan = pending.get("map_plan") or {}
    if map_plan:
        action = str(map_plan.get("action") or "create").strip().lower()
        if action == "create":
            map_id = str(map_plan.get("map_id") or "encounter_map").strip() or "encounter_map"
            plane_map = PlaneMapState(
                id=map_id,
                name=str(map_plan["name"]),
                width=float(map_plan["width"]),
                height=float(map_plan["height"]),
                grid_size=float(map_plan.get("grid_size", 5)),
                description=str(map_plan.get("description", "")),
            )
            space.maps[map_id] = plane_map
            space.active_map_id = map_id
        elif action == "switch":
            map_id = str(map_plan["map_id"])
            if map_id not in space.maps:
                return _combat_start_error(f"无法开始战斗：找不到地图 '{map_id}'。", tool_call_id)
            space.active_map_id = map_id
        else:
            return _combat_start_error(f"无法开始战斗：未知地图动作 '{action}'。", tool_call_id)

    if not space.active_map_id or space.active_map_id not in space.maps:
        return _combat_start_error("无法开始战斗：开战计划没有提供地图，且当前没有可用地图。", tool_call_id)

    active_map = space.maps[space.active_map_id]
    player_raw = state.get("player")
    player_dict = player_raw.model_dump() if hasattr(player_raw, "model_dump") else dict(player_raw) if player_raw else None
    known_ids = set(pending.get("combatant_ids") or [])
    if player_dict:
        known_ids.add(resolve_player_reference_id(player_dict, "player"))

    placements = pending.get("placements") or []
    for placement in placements:
        raw_unit_id = str(placement["unit_id"])
        unit_id = resolve_player_reference_id(player_dict, raw_unit_id)
        if unit_id not in known_ids:
            return _combat_start_error(f"无法开始战斗：落点单位 '{raw_unit_id}' 不在本次参战单位中。", tool_call_id)
        point = Point2D(x=float(placement["x"]), y=float(placement["y"]))
        if not point_in_map(active_map, point):
            return _combat_start_error(
                f"无法开始战斗：{raw_unit_id} 的落点 ({point.x:g}, {point.y:g}) 超出地图 {active_map.name}。",
                tool_call_id,
            )
        space.placements[unit_id] = UnitPlacementState(
            unit_id=unit_id,
            map_id=active_map.id,
            position=point,
            facing_deg=float(placement.get("facing_deg", 0)),
            footprint_radius=float(placement.get("footprint_radius", 2.5)),
        )

    state["space"] = SpaceState.model_validate(space).model_dump()
    return None


def _combat_start_error(content: str, tool_call_id: str) -> dict:
    """统一构造开战 workflow 的失败消息。"""
    from langchain_core.messages import ToolMessage

    return {"messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]}


def combat_executor_node(state: GraphState) -> dict:
    """执行主 Agent 委托的单单位战斗回合；工具细节在节点内收束成战报。"""
    pending = _state_value_to_dict(state.get("pending_combat_executor"))
    if not pending:
        return {}

    actor_id = str(pending.get("actor_id", ""))
    instruction = str(pending.get("instruction", "")).strip()
    combat = _state_value_to_dict(state.get("combat"))
    current_actor_id = str(combat.get("current_actor_id", ""))

    if not combat:
        return _combat_executor_blocked(actor_id, "当前不在战斗中。")
    if actor_id != current_actor_id:
        return _combat_executor_blocked(
            actor_id,
            f"执行器拒绝行动：委托单位 {actor_id} 不是当前行动者 {current_actor_id}。",
        )

    executor_state = dict(state)
    actor_before = _get_executor_actor_snapshot(executor_state, actor_id)
    messages = _build_combat_executor_messages(executor_state, actor_id, instruction)
    tools = _get_combat_executor_tools()
    tools_by_name = {tool.name: tool for tool in tools}

    tool_trace: list[dict[str, Any]] = []
    combat_events: list[dict[str, Any]] = []
    updated_keys: set[str] = set()
    status = "completed"
    blocked_reason: str | None = None
    pending_reaction = False
    final_note = ""

    for step in range(3):
        response = _get_llm_service().invoke_with_tools(
            messages=messages,
            tools=tools,
            system_prompt=_build_combat_executor_system_prompt(),
            mode=COMBAT_AGENT_MODE,
        )
        response = _keep_first_tool_call(response)
        messages.append(response)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            final_note = _message_content_to_text(getattr(response, "content", "")).strip()
            if not tool_trace:
                status = "no_viable_action"
                blocked_reason = final_note or "执行器没有选择可执行动作。"
            break

        tool_call = tool_calls[0]
        tool_name = str(tool_call.get("name", ""))
        tool = tools_by_name.get(tool_name)
        if tool is None:
            status = "needs_main_agent_decision"
            blocked_reason = f"执行器选择了未授权工具 {tool_name}。"
            messages.append(ToolMessage(content=blocked_reason, tool_call_id=str(tool_call.get("id", f"executor-{step}"))))
            break

        result = _invoke_combat_executor_tool(tool, tool_call, executor_state)
        update = dict(result.update or {}) if isinstance(result, Command) else {
            "messages": [ToolMessage(content=str(result), tool_call_id=str(tool_call.get("id", f"executor-{step}")))]
        }
        _apply_executor_update(executor_state, update)
        updated_keys.update(key for key in update if key not in {"messages", "hp_changes"})

        trace_entry = _build_executor_trace_entry(tool_name, tool_call, update)
        tool_trace.append(trace_entry)
        combat_events.extend(_build_executor_combat_events(trace_entry))
        messages.extend(_tool_messages_for_executor_llm(update, tool_call))

        if update.get("pending_reaction"):
            status = "reaction_pending"
            pending_reaction = True
            break
        if _is_executor_tool_failure(trace_entry):
            status = "blocked"
            blocked_reason = trace_entry.get("result") or "执行器动作失败。"
            break

    actor_after = _get_executor_actor_snapshot(executor_state, actor_id)
    resource_state = _build_executor_resource_state(actor_after)
    used_resources = _build_executor_used_resources(actor_before, actor_after)
    remaining_options = _build_executor_remaining_options(actor_after, status=status)
    recommended_next = _build_executor_recommended_next(
        status=status,
        pending_reaction=pending_reaction,
        blocked_reason=blocked_reason,
        tool_trace=tool_trace,
        remaining_options=remaining_options,
        resource_state=resource_state,
    )

    result_state = {
        "actor_id": actor_id,
        "status": status,
        "turn_should_end": recommended_next["kind"] == "end_turn",
        "needs_main_agent_decision": recommended_next["kind"] == "ask_main_agent",
        "blocked_reason": blocked_reason,
        "pending_reaction": pending_reaction,
        "resource_state": resource_state,
        "used_resources": used_resources,
        "remaining_meaningful_options": remaining_options,
        "recommended_next": recommended_next,
        "tool_trace": tool_trace,
        "decision_note": final_note,
        "narration_hint": _build_executor_narration_hint(tool_trace, final_note),
    }
    output = {
        "pending_combat_executor": None,
        "combat_executor_result": result_state,
        "combat_events": combat_events,
        "messages": [_build_combat_executor_report(result_state)],
    }
    for key in updated_keys:
        output[key] = executor_state.get(key)
    return output


def _combat_executor_blocked(actor_id: str, reason: str) -> dict:
    """在 workflow 层拒绝非法委托，让主 Agent 拿到结构化失败原因。"""
    result_state = {
        "actor_id": actor_id,
        "status": "blocked",
        "turn_should_end": False,
        "needs_main_agent_decision": True,
        "blocked_reason": reason,
        "pending_reaction": False,
        "resource_state": {},
        "used_resources": [],
        "remaining_meaningful_options": [],
        "recommended_next": {"kind": "ask_main_agent", "reason": reason},
        "tool_trace": [],
        "decision_note": reason,
        "narration_hint": reason,
    }
    return {
        "pending_combat_executor": None,
        "combat_executor_result": result_state,
        "combat_events": [],
        "messages": [_build_combat_executor_report(result_state)],
    }


@lru_cache(maxsize=1)
def _get_combat_executor_tools() -> tuple:
    """执行器只暴露战斗落子所需工具，避免它推进回合或改写遭遇结构。"""
    from app.services.tools.character_tools import inspect_unit
    from app.services.tools.class_action_tools import use_class_action
    from app.services.tools.combat_tools import attack_action
    from app.services.tools.item_tools import manage_inventory
    from app.services.tools.monster_action_tools import use_monster_action
    from app.services.tools.space_tools import manage_space
    from app.services.tools.spell_tools import cast_spell

    return (
        manage_space,
        use_monster_action,
        attack_action,
        cast_spell,
        use_class_action,
        manage_inventory,
        inspect_unit,
    )


def _build_combat_executor_system_prompt() -> str:
    """给执行器一份短而硬的战术契约：按委托落子，遇到阻塞就停。"""
    return (
        "你是战斗回合执行器，只负责把主 Agent 的战术委托转成当前行动者的合法战斗操作。"
        "不要推进到下一个单位，不要结束战斗，不要改写剧情目标。"
        "优先完成委托中明确要求的移动、攻击、施法、职业动作或物品使用；"
        "如果动作失败、资源不足、触发玩家反应或需要重新裁定目标，立即停止并用简短文本说明原因。"
        "完成可执行动作后停止调用工具，用简短文本说明执行结果和你建议主 Agent 下一步做什么。"
    )


def _build_combat_executor_messages(state: dict, actor_id: str, instruction: str) -> list[BaseMessage]:
    """只给执行器当前回合事实，减少主对话上下文对战术落子的干扰。"""
    payload = {
        "actor_id": actor_id,
        "instruction": instruction,
        "scene_summary": state.get("scene_summary", ""),
        "combat": _state_value_to_dict(state.get("combat")),
        "player": _state_value_to_dict(state.get("player")),
        "space": _state_value_to_dict(state.get("space")),
    }
    return [HumanMessage(content="战斗执行委托：\n" + json.dumps(payload, ensure_ascii=False, default=str))]


def _invoke_combat_executor_tool(tool: Any, tool_call: dict, state: dict) -> object:
    """按 LangChain ToolCall 形态调用工具，并把执行器局部状态注入进去。"""
    args = dict(tool_call.get("args") or {})
    args["state"] = state
    return tool.invoke({
        "name": tool.name,
        "args": args,
        "id": str(tool_call.get("id") or f"executor-{tool.name}"),
        "type": "tool_call",
    })


def _apply_executor_update(state: dict, update: dict) -> None:
    """把工具 Command.update 串行合并到执行器局部状态，下一次工具看到最新战场。"""
    for key, value in update.items():
        if key == "messages":
            state[key] = [*list(state.get(key, [])), *list(value or [])]
        else:
            state[key] = value


def _tool_messages_for_executor_llm(update: dict, tool_call: dict) -> list[ToolMessage]:
    """维持 tool-call 协议，让执行器模型能基于上一工具结果继续落子。"""
    tool_messages = [message for message in update.get("messages", []) if isinstance(message, ToolMessage)]
    if tool_messages:
        return tool_messages
    return [ToolMessage(content="工具已执行。", tool_call_id=str(tool_call.get("id") or "executor-tool"))]


def _build_executor_trace_entry(tool_name: str, tool_call: dict, update: dict) -> dict[str, Any]:
    """把一次工具调用压成主 Agent 可读、前端可转发的事实记录。"""
    messages = list(update.get("messages", []) or [])
    contents = [_message_content_to_text(getattr(message, "content", "")) for message in messages]
    attack_roll = next(
        (
            message.additional_kwargs.get("attack_roll")
            for message in messages
            if isinstance(message, ToolMessage)
            and isinstance(message.additional_kwargs, dict)
            and isinstance(message.additional_kwargs.get("attack_roll"), dict)
        ),
        None,
    )
    return {
        "tool": tool_name,
        "args": dict(tool_call.get("args") or {}),
        "result": "\n".join(content for content in contents if content).strip(),
        "attack_roll": attack_roll,
        "hp_changes": list(update.get("hp_changes", []) or []),
    }


def _build_executor_combat_events(trace_entry: dict[str, Any]) -> list[dict[str, Any]]:
    """复用工具产出的骰子与血量事实，交给 SSE 层驱动现有动画。"""
    if not trace_entry.get("result") and not trace_entry.get("hp_changes") and not trace_entry.get("attack_roll"):
        return []
    return [{
        "kind": "tool",
        "tool": trace_entry.get("tool"),
        "content": trace_entry.get("result", ""),
        "attack_roll": trace_entry.get("attack_roll"),
        "hp_changes": trace_entry.get("hp_changes", []),
    }]


def _is_executor_tool_failure(trace_entry: dict[str, Any]) -> bool:
    """工具失败标记意味着战术委托需要主 Agent 重新裁量。"""
    result = str(trace_entry.get("result", ""))
    return result.startswith("[动作失败]") or result.startswith("[攻击失败]") or result.startswith("无法")


def _get_executor_actor_snapshot(state: dict, actor_id: str) -> dict:
    """读取当前行动者快照，供执行器比较资源使用前后变化。"""
    from app.services.tools._helpers import get_combatant

    combat = _state_value_to_dict(state.get("combat"))
    player = _state_value_to_dict(state.get("player"))
    actor = get_combatant(combat, player, actor_id) if combat else None
    return dict(actor or {})


def _build_executor_resource_state(actor: dict) -> dict[str, Any]:
    """把动作经济压成稳定字段，避免主 Agent 从整张角色卡里猜。"""
    return {
        "action_available": bool(actor.get("action_available", True)),
        "extra_action_available": bool(actor.get("extra_action_available", False)),
        "bonus_action_available": bool(actor.get("bonus_action_available", True)),
        "reaction_available": bool(actor.get("reaction_available", True)),
        "movement_left": int(actor.get("movement_left", actor.get("speed", 0)) or 0),
    }


def _build_executor_used_resources(before: dict, after: dict) -> list[str]:
    """用前后快照识别执行器实际花掉的资源，不猜测战术价值。"""
    before_state = _build_executor_resource_state(before)
    after_state = _build_executor_resource_state(after)
    used: list[str] = []
    for key in ("action_available", "extra_action_available", "bonus_action_available", "reaction_available"):
        if before_state[key] and not after_state[key]:
            used.append(key.removesuffix("_available"))
    if after_state["movement_left"] < before_state["movement_left"]:
        used.append("movement")
    return used


def _build_executor_remaining_options(actor: dict, *, status: str) -> list[str]:
    """只提示客观剩余资源，不把药水/职业能力写成必须使用。"""
    if status in {"blocked", "reaction_pending", "needs_main_agent_decision"}:
        return []

    resource_state = _build_executor_resource_state(actor)
    options: list[str] = []
    if resource_state["action_available"] or resource_state["extra_action_available"]:
        options.append("主要动作仍可用；若主指令尚未完成，应继续委托执行器。")
    if resource_state["bonus_action_available"]:
        options.append("附赠动作仍可用；仅在主 Agent 明确有战术意图或可用能力时使用。")
    if resource_state["movement_left"] > 0:
        options.append(f"仍有 {resource_state['movement_left']} 尺移动；只有撤退、靠近、占位或脱离危险有意义时才继续移动。")
    return options


def _build_executor_recommended_next(
    *,
    status: str,
    pending_reaction: bool,
    blocked_reason: str | None,
    tool_trace: list[dict[str, Any]],
    remaining_options: list[str],
    resource_state: dict[str, Any],
) -> dict[str, Any]:
    """由执行器汇报下一步建议，让主 Agent 少做流程猜测。"""
    if pending_reaction or status == "reaction_pending":
        return {"kind": "wait_reaction", "reason": "执行过程中触发了玩家反应，必须等待反应选择后再继续。"}
    if status in {"blocked", "needs_main_agent_decision", "no_viable_action"}:
        return {"kind": "ask_main_agent", "reason": blocked_reason or "执行器无法完成当前委托，需要主 Agent 重新裁定。"}
    if not tool_trace:
        return {"kind": "ask_main_agent", "reason": "执行器没有产生任何工具结果，需要主 Agent 明确战术意图。"}

    has_primary_action = bool(resource_state.get("action_available") or resource_state.get("extra_action_available"))
    if has_primary_action:
        return {
            "kind": "continue_executor",
            "reason": "当前行动者仍有主要动作资源；若战术目标尚未完成，建议继续委托执行器。",
        }
    return {
        "kind": "end_turn",
        "reason": "主要动作已完成；剩余资源属于可选项，若没有明确战术收益，建议结束回合。",
        "remaining_options": remaining_options,
    }


def _build_executor_narration_hint(tool_trace: list[dict[str, Any]], final_note: str) -> str:
    """给主 Agent 一行可直接转述或改写的战斗结果提示。"""
    if final_note:
        return final_note
    return "\n".join(entry.get("result", "") for entry in tool_trace if entry.get("result")).strip()


def _build_combat_executor_report(result_state: dict[str, Any]) -> HumanMessage:
    """把执行器结果作为内部系统消息回灌，使主 Agent 下一步只读一条摘要。"""
    content = "[系统:战斗执行器]\n" + json.dumps(result_state, ensure_ascii=False, default=str)
    return HumanMessage(content=content)


def _invoke_assistant(state: GraphState, mode: str) -> dict:
    from app.utils.logger import logger

    assembler = _get_context_assembler()
    base_system_prompt = get_assistant_system_prompt(mode)
    session_id = str(state.get("session_id") or "detached")
    phase = state.get("phase")
    optional_rag_future = _start_optional_rule_rag(state, mode, session_id)
    required_context = assembler.assemble_required(state, mode, base_system_prompt=base_system_prompt)
    optional_rag_evaluation = _wait_optional_rule_rag(optional_rag_future, session_id)
    if optional_rag_evaluation and optional_rag_evaluation.evidence:
        assembled_context = assembler.append_optional_runtime_context(
            required_context,
            [optional_rag_evaluation.evidence],
        )
    else:
        assembled_context = assembler.append_optional_runtime_context(required_context, [])
    runtime_state_message = build_runtime_state_message(assembled_context.runtime_state_text)
    invocation_messages = [*assembled_context.model_input_messages, runtime_state_message]
    tools = get_tool_profile(mode)

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
        runtime_state_text=assembled_context.runtime_state_text,
        messages=invocation_messages,
        tools=tools,
    )
    started_perf = perf_counter()

    try:
        response = _get_llm_service().invoke_with_tools(
            messages=invocation_messages,
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
        "messages": [runtime_state_message, response],
        "output": output,
    }


def _start_optional_rule_rag(state: GraphState, mode: str, session_id: str) -> OptionalRuleRagTask | None:
    """实验开关开启时启动机会型规则 RAG；默认路径完全不创建后台任务。"""
    if not settings.auto_rule_rag_enabled or mode != NARRATIVE_AGENT_MODE:
        return None

    messages = list(state.get("messages", []))
    if not _last_message_is_external_human(messages):
        return None

    query = _latest_external_human_text(messages)
    if not query:
        return None

    invocation_id, started_at = start_auto_rule_rag_trace(
        session_id,
        query=query,
        top_k=settings.auto_rule_rag_top_k,
        timeout_ms=settings.auto_rule_rag_timeout_ms,
        min_score=settings.auto_rule_rag_min_score,
    )
    started_perf = perf_counter()
    future = _OPTIONAL_RAG_EXECUTOR.submit(
        evaluate_auto_rule_evidence,
        query,
        session_id=session_id,
        invocation_id=invocation_id,
        started_at=started_at,
    )
    return OptionalRuleRagTask(
        future=future,
        invocation_id=invocation_id,
        started_at=started_at,
        started_perf=started_perf,
        query=query,
        top_k=settings.auto_rule_rag_top_k,
        timeout_ms=settings.auto_rule_rag_timeout_ms,
    )


def _last_message_is_external_human(messages: list[BaseMessage]) -> bool:
    """机会型 RAG 只响应新玩家输入，避免工具返回后的二次 assistant 重复检索。"""
    if not messages:
        return False
    last_message = messages[-1]
    return (
        isinstance(last_message, HumanMessage)
        and not is_runtime_state_message(last_message)
        and not is_internal_system_human_message(last_message)
    )


def _wait_optional_rule_rag(task: OptionalRuleRagTask | None, session_id: str) -> AutoRuleEvidenceEvaluation | None:
    """只等待短窗口；超时和失败都不影响主模型调用。"""
    if task is None:
        return None

    timeout_seconds = max(float(task.timeout_ms), 0.0) / 1000
    try:
        return task.future.result(timeout=timeout_seconds)
    except TimeoutError:
        mark_auto_rule_rag_timed_out(task.invocation_id)
        timeout_auto_rule_rag_trace(
            session_id,
            invocation_id=task.invocation_id,
            started_at=task.started_at,
            duration_ms=(perf_counter() - task.started_perf) * 1000,
            query=task.query,
            top_k=task.top_k,
            timeout_ms=task.timeout_ms,
        )
        return None
    except Exception:
        return None


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
    """战斗后置收束节点保留为空壳；玩家倒地由死亡豁免与状态工具处理。"""
    return {}


def death_save_pause_node(state: GraphState) -> dict:
    """倒地玩家回合先交还话语权，避免死亡豁免在同一条流里自动跑完。"""
    combat = _state_value_to_dict(state.get("combat")) or {}
    player = _state_value_to_dict(state.get("player")) or {}
    actor_id = combat.get("current_actor_id") or player.get("id", "player")
    actor_name = player.get("name") or actor_id
    successes = int(player.get("death_save_successes", 0) or 0)
    failures = int(player.get("death_save_failures", 0) or 0)
    content = (
        f"{actor_name} 倒在地上，当前是你的回合。\n\n"
        f"死亡豁免：{successes} 成功 / {failures} 失败。\n"
        "在掷死亡豁免之前，你可以先说话、回忆、求援或描述这一刻。"
        "当你准备好后，直接说“继续”或“掷死亡豁免”。"
    )
    return {
        "messages": [AIMessage(content=content)],
        STATE_DEATH_SAVE_PAUSE_TURN_KEY: _death_save_pause_turn_id(state, combat, actor_id),
    }


def _death_save_pause_turn_id(state: GraphState, combat: dict, actor_id: str) -> str:
    """用轮次和行动者标识一次倒地玩家回合，下一轮同一玩家会重新获得插话机会。"""
    return f"{state.get('active_combat_message_start', 'detached')}:{combat.get('round', 0)}:{actor_id}"


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
