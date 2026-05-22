"""冒险运行时裁定 — 用后台语义裁定驱动节点进度，主提示词前缀保持稳定。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.adventures.models import AdventureState
from app.adventures.clue_projection import project_known_clue_window
from app.adventures.navigation import (
    apply_arrival_events,
    normalize_adventure_state,
    recent_breadcrumb_ids,
    record_node_transition,
    returnable_node_ids,
    settle_exit_local_requirements,
)
from app.adventures.rewards import sync_pending_node_rewards
from app.adventures.store import get_adventure_store
from app.memory.context_assembler import (
    ADVENTURE_NODE_FRAME_MESSAGE_PREFIX,
    is_adventure_node_frame_message,
    is_internal_system_human_message,
    message_content_to_text,
)
from app.services.llm_service import LLMService
from app.utils.agent_trace import (
    fail_adventure_director_trace,
    finish_adventure_director_trace,
    start_adventure_director_trace,
)

_LEGACY_EXIT_ID_ALIASES: dict[tuple[str, str], str] = {
    ("adventure_hook_meet_me_in_phandalin", "continue_to_ambush"): "begin_escort_journey",
    ("goblin_ambush", "follow_goblin_trail"): "investigate_goblin_trail",
    ("goblin_ambush", "continue_to_phandalin"): "go_to_phandalin_first",
    ("goblin_trail_to_cragmaw_hideout", "arrive_cragmaw_hideout"): "follow_trail_to_hideout",
    ("cragmaw_hideout_goblin_den", "return_to_eating_cave_for_sildar"): "to_overpass",
}


DIRECTOR_SYSTEM_PROMPT = (
    "你是 TRPG 冒险模组进度裁定器，只输出 JSON。"
    "输入中的 turn_context.stage 表示调用时机：pre_turn 是主主持回复前的导航裁定，post_turn 是主主持回复后的事实结算。"
    "无论 stage 如何，都必须使用同一套输入字段和同一套输出 JSON schema。"
    "根据当前节点材料、路径回溯和最近对话判断已经真实发生的稳定剧情事实。"
    "不要写叙事，不要扩展模组外内容，不要发明不存在的线索、事件、回访目标或出口。"
    "只有玩家行动、工具结果或主持回复明确支持时，才输出对应 id。"
    "post_turn 时，同时检查主持回复是否臆造了当前节点或工具结果未支持的地点、NPC、派系、任务、救援目标、奖励或主线结论。"
    "pre_turn 时，只处理玩家已经明确点名目的地、回访目标或出口意图的移动；"
    "若玩家最新输入同时明确确认当前节点已经发生的事实，可以输出当前冒险节点帧 current_node.events 或 current_node.clues 中的 id。"
    "pre_turn 不做越界警告，也不要从旧对话里补写玩家本轮没有确认的事实。"
    "pre_turn 遇到拥有多个出口的节点时，'继续'、'继续前进'、'走吧'、'往前走'、'出发'这类泛化行动必须输出 null，"
    "除非玩家同时明确说出去凡达林、追踪地精踪迹、进入洞口、返回遇袭地点等具体目标。"
    "pre_turn 如果当前节点仍需主持呈现场景、处理遭遇、等待玩家调查或结算工具结果，不要提前跳过该节点。"
    "系统授权事实高于冒险节点：开局友方准则允许引入一名名为格林的 fighter_companion 战士同伴；格林不是修达·霍温特，不要将其姓名、同行身份或简短引入判为越界。"
    "专名音译和英文名视为同一实体；不要仅因“凡达林/凡戴尔/Phandalin”或“冈德伦/甘德伦/Gundren”写法差异判定越界。"
    "如果玩家想回到之前离开的节点，或想补回被跳过的场景，可以输出 target_node_id 和 transition_kind=revisit；"
    "如果玩家或主持回复明显指向某个语义上更合适的节点，而当前节点出口不够用，可以输出 target_node_id 和 transition_kind=switch；"
    "但如果只是主持回复把检索候选节点演成现实、玩家没有明确移动到该地点，应优先标记 desync_detected，而不是顺着越界叙事切换节点。"
    "如果当前节点的硬出口已足够，优先输出 exit_option_id 和 transition_kind=advance。"
    "不要因为候选节点与玩家输入有弱相关就跳入内部房间；地点层级不确定时保持当前节点或选择入口节点。"
)

@dataclass(slots=True)
class AdventureProgressDecision:
    """后台 director 的语义裁定结果；所有 id 仍需 runtime 校验。"""

    completed_event_ids: list[str] = field(default_factory=list)
    discovered_clue_ids: list[str] = field(default_factory=list)
    exit_option_id: str | None = None
    target_node_id: str | None = None
    transition_kind: Literal["advance", "switch", "revisit"] | None = None
    needs_player_choice: bool = False
    confidence: float = 0.0
    reason: str = ""
    desync_detected: bool = False
    unsupported_claims: list[str] = field(default_factory=list)
    warning: str = ""
    visible_clue_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AdventurePreTurnDecision:
    """主 LLM 回复前的轻量导航裁定，只负责防止书签落后于玩家行动。"""

    completed_event_ids: list[str] = field(default_factory=list)
    discovered_clue_ids: list[str] = field(default_factory=list)
    exit_option_id: str | None = None
    target_node_id: str | None = None
    transition_kind: Literal["advance", "switch", "revisit"] | None = None
    needs_player_choice: bool = False
    confidence: float = 0.0
    reason: str = ""


@dataclass(slots=True)
class AdventureRuntimeUpdate:
    """runtime 写回图状态的最小结果。"""

    adventure: dict[str, Any] | None = None
    decision: AdventureProgressDecision | None = None
    applied: str = ""
    state_update: dict[str, Any] = field(default_factory=dict)
    player_notifications: list[dict[str, Any]] = field(default_factory=list)


class AdventureDirector(Protocol):
    """让运行时可注入真实 LLM 或测试替身。"""

    def adjudicate_pre_turn(
        self,
        *,
        state: dict[str, Any],
        player_message: str,
        session_id: str | None = None,
    ) -> AdventurePreTurnDecision: ...

    def adjudicate(
        self,
        *,
        state: dict[str, Any],
        recent_messages: list[Any],
        session_id: str | None = None,
    ) -> AdventureProgressDecision: ...


class LLMAdventureDirector:
    """用独立 LLM 调用做语义裁定，不污染主 DM 的消息前缀。"""

    def __init__(self, llm_service: LLMService | None = None) -> None:
        self._llm_service = llm_service or LLMService()

    def adjudicate(
        self,
        *,
        state: dict[str, Any],
        recent_messages: list[Any],
        session_id: str | None = None,
    ) -> AdventureProgressDecision:
        payload = self._build_director_payload(
            stage="post_turn",
            state=state,
            messages=recent_messages,
        )
        return self._run_director(
            payload=payload,
            session_id=session_id,
            system_prompt=DIRECTOR_SYSTEM_PROMPT,
            parser=_parse_decision,
            trace_stage="post_turn",
        )

    def adjudicate_pre_turn(
        self,
        *,
        state: dict[str, Any],
        player_message: str,
        session_id: str | None = None,
    ) -> AdventurePreTurnDecision:
        history_messages = [*list(state.get("messages", [])), HumanMessage(content=player_message)]
        payload = self._build_director_payload(
            stage="pre_turn",
            state=state,
            messages=history_messages,
            player_message=player_message,
        )
        return self._run_director(
            payload=payload,
            session_id=session_id,
            system_prompt=DIRECTOR_SYSTEM_PROMPT,
            parser=_parse_pre_turn_decision,
            trace_stage="pre_turn",
        )

    def _build_director_payload(
        self,
        *,
        stage: Literal["pre_turn", "post_turn"],
        state: dict[str, Any],
        messages: list[Any],
        player_message: str = "",
    ) -> dict[str, Any]:
        store = get_adventure_store()
        adventure = _adventure_dict(state.get("adventure"))
        node = store.get_node(adventure["active_node_id"])
        intent_query = _director_intent_query(node, messages)
        candidate_nodes = _director_candidate_nodes(intent_query, node.id, adventure=adventure)
        known_clue_window = project_known_clue_window(
            adventure,
            current_node_id=node.id,
            query=intent_query,
        )
        director_messages = _messages_with_active_node_frame(messages, node.id)
        payload = {
            "director_contract": {
                "transition_rule": {
                    "advance": "当前冒险节点帧 current_node.exits 中的硬出口；只允许沿当前出口推进到 next_node_id。",
                    "switch": "语义上已经抵达但当前节点没有直接出口时，允许切换到 candidate_nodes 中的目标节点。",
                    "revisit": "玩家明确回头、补回或重访先前离开的节点时，允许切换到 returnable_nodes 中的目标节点。",
                },
                "output_schema": {
                    "completed_event_ids": ["只允许当前冒险节点帧 current_node.events 中的 id"],
                    "discovered_clue_ids": ["只允许当前冒险节点帧 current_node.clues 中的 id"],
                    "exit_option_id": "只允许当前冒险节点帧 current_node.exits 中的 id，无法判断则 null",
                    "target_node_id": "只允许 active_node_id、candidate_nodes 或 returnable_nodes 中的 id，无法判断则 null",
                    "transition_kind": "advance、switch 或 revisit；exit_option_id 有效时通常用 advance，否则根据语义目标用 switch 或 revisit",
                    "needs_player_choice": "可选；目标节点已确定但抵达后仍需玩家选择下一步时为 true，不阻止节点提交",
                    "confidence": "0 到 1",
                    "reason": "一句话说明依据",
                    "desync_detected": "主持回复出现当前节点和工具结果均不支持的剧情事实时为 true",
                    "unsupported_claims": ["逐条列出不受支持的剧情事实，最多 5 条"],
                    "warning": "若 desync_detected 为 true，给下一轮主持的一句纠偏指令，否则空字符串",
                    "visible_clue_ids": "可选；本轮希望主主持看到的已知线索 id，只能从 known_clue_window 或 adventure_state.known_clue_ids 中选择",
                },
                "known_module_aliases": _known_module_aliases(),
            },
            "message_history": _director_message_history(director_messages),
            "active_node_pointer": _director_active_node_pointer(node),
            "route_memory": _director_route_memory(adventure, node.id),
            "returnable_nodes": _director_returnable_nodes(adventure, node.id),
            "candidate_nodes": candidate_nodes,
            "known_clue_window": known_clue_window,
            "adventure_state": adventure,
            "runtime_context": _director_runtime_context(state),
            "turn_context": {
                "stage": stage,
                "player_message": player_message or _latest_external_human_text(messages),
            },
        }
        return payload

    def _run_director(
        self,
        *,
        payload: dict[str, Any],
        session_id: str | None,
        system_prompt: str,
        parser: Any,
        trace_stage: Literal["pre_turn", "post_turn"],
    ) -> Any:
        invocation_id = ""
        started_at = ""
        start_time = time.perf_counter()
        if session_id:
            invocation_id, started_at = start_adventure_director_trace(
                session_id,
                director_input={"phase": trace_stage, **payload},
            )
        try:
            raw_message = self._invoke_director_summary(
                json.dumps(payload, ensure_ascii=False),
                system_prompt=system_prompt,
            )
            raw = message_content_to_text(getattr(raw_message, "content", "")).strip()
            decision = parser(raw)
        except Exception as exc:
            if session_id and invocation_id:
                fail_adventure_director_trace(
                    session_id,
                    invocation_id=invocation_id,
                    started_at=started_at,
                    duration_ms=(time.perf_counter() - start_time) * 1000,
                    error=exc,
                )
            raise
        if session_id and invocation_id:
            finish_adventure_director_trace(
                session_id,
                invocation_id=invocation_id,
                started_at=started_at,
                duration_ms=(time.perf_counter() - start_time) * 1000,
                raw_response=raw,
                decision=decision,
                response=raw_message,
            )
        return decision

    def _invoke_director_summary(self, summary_input: str, *, system_prompt: str) -> AIMessage:
        invoke_message = getattr(self._llm_service, "invoke_summary_message", None)
        if callable(invoke_message):
            response = invoke_message(summary_input, system_prompt=system_prompt)
            if isinstance(response, AIMessage):
                return response
            return AIMessage(content=message_content_to_text(getattr(response, "content", "")).strip())

        raw_text = self._llm_service.invoke_summary(summary_input, system_prompt=system_prompt)
        return AIMessage(content=raw_text)


def adjudicate_and_apply_adventure_progress(
    state: dict[str, Any],
    *,
    recent_messages: list[Any],
    director: AdventureDirector,
    session_id: str | None = None,
) -> AdventureRuntimeUpdate:
    """运行时只提交 director 裁定；剧情语义不再二次裁判。"""
    store = get_adventure_store()
    adventure = _adventure_dict(state.get("adventure"))
    node = store.get_node(adventure["active_node_id"])
    decision = director.adjudicate(state=state, recent_messages=recent_messages, session_id=session_id)
    state_update = _guardrail_state_update(state, node.id, decision)
    _apply_visible_clue_window(adventure, decision, state_update)
    return _commit_director_decision(
        state=state,
        adventure=adventure,
        node=node,
        decision=decision,
        state_update=state_update,
        stage="post_turn",
        allow_transition=True,
    )


def adjudicate_and_apply_pre_turn_adventure_progress(
    state: dict[str, Any],
    *,
    player_message: str,
    director: AdventureDirector,
    session_id: str | None = None,
) -> AdventureRuntimeUpdate:
    """主回复前提交 director 导航裁定，让主持模型基于正确节点开场。"""
    store = get_adventure_store()
    adventure = _adventure_dict(state.get("adventure"))
    node = store.get_node(adventure["active_node_id"])
    decision = director.adjudicate_pre_turn(
        state=state,
        player_message=player_message,
        session_id=session_id,
    )
    progress_decision = _progress_decision_from_pre_turn(decision)
    return _commit_director_decision(
        state=state,
        adventure=adventure,
        node=node,
        decision=progress_decision,
        state_update={},
        stage="pre_turn",
        allow_transition=not _pre_turn_requires_main_agent_context(node, player_message),
    )


def _commit_director_decision(
    *,
    state: dict[str, Any],
    adventure: dict[str, Any],
    node: Any,
    decision: AdventureProgressDecision,
    state_update: dict[str, Any],
    stage: Literal["pre_turn", "post_turn"],
    allow_transition: bool,
) -> AdventureRuntimeUpdate:
    """提交 Director 裁定；runtime 只校验结构契约，不再另起剧情裁判。"""
    store = get_adventure_store()
    allowed_exit_ids = {item.id for item in node.exits if store.resolve_node_id(item.next_node_id) in store._nodes}
    changed = _drop_stale_pending_exits(adventure, allowed_exit_ids)

    if _apply_director_node_facts(adventure, node, decision, state_update):
        changed = True
    if _sync_pending_rewards(adventure, node, state_update):
        changed = True

    if allow_transition:
        transition = _try_commit_director_transition(
            adventure=adventure,
            node=node,
            decision=decision,
            state_update=state_update,
        )
        if transition is not None:
            _prefix_transition_applied(transition, stage)
            if changed and transition.adventure is None:
                transition.adventure = adventure
            _sync_transition_rewards(transition)
            return transition

    if stage == "post_turn" and state.get("adventure_runtime_directive"):
        state_update["adventure_runtime_directive"] = None

    if _sync_pending_rewards(adventure, node, state_update):
        changed = True

    has_guardrail_warning = bool(state_update.get("adventure_guardrail_warning")) if state_update else False
    applied = "state_update" if changed or (state_update and not has_guardrail_warning) else "guardrail_warning" if has_guardrail_warning else ""
    return AdventureRuntimeUpdate(
        adventure=adventure if changed else None,
        decision=decision,
        applied=applied,
        state_update=state_update,
    )


def _try_commit_director_transition(
    *,
    adventure: dict[str, Any],
    node: Any,
    decision: AdventureProgressDecision,
    state_update: dict[str, Any],
) -> AdventureRuntimeUpdate | None:
    """优先提交硬出口；没有硬出口时再按 Director 指定目标切换。"""
    store = get_adventure_store()
    allowed_exit_ids = {item.id for item in node.exits if store.resolve_node_id(item.next_node_id) in store._nodes}
    exit_option_id = _resolve_exit_option_id(node.id, decision.exit_option_id, allowed_exit_ids)
    if exit_option_id in allowed_exit_ids:
        transition = _apply_exit_transition(
            adventure,
            node,
            exit_option_id,
            decision,
            state_update=state_update,
            directive_kind="node_advanced",
        )
        if transition is not None:
            return transition

    if not decision.target_node_id:
        return None

    return _apply_target_transition(
        adventure,
        node,
        decision.target_node_id,
        decision,
        state_update=state_update,
        directive_kind=decision.transition_kind or "node_switched",
    )


def _resolve_exit_option_id(node_id: str, exit_option_id: str | None, allowed_exit_ids: set[str]) -> str | None:
    """兼容旧 Director/旧存档里的出口名，运行时实际仍只走 canonical 出口。"""
    if not exit_option_id:
        return None
    if exit_option_id in allowed_exit_ids:
        return exit_option_id
    alias = _LEGACY_EXIT_ID_ALIASES.get((node_id, exit_option_id))
    if alias in allowed_exit_ids:
        return alias
    return exit_option_id


def _prefix_transition_applied(update: AdventureRuntimeUpdate, stage: Literal["pre_turn", "post_turn"]) -> None:
    """pre-turn 标记保留给 trace；post-turn 使用普通提交类型。"""
    if stage != "pre_turn":
        return
    if update.applied == "advanced":
        update.applied = "pre_turn_advanced"
    elif update.applied == "switched":
        update.applied = "pre_turn_switched"
    elif update.applied == "revisited":
        update.applied = "pre_turn_revisited"


def _sync_transition_rewards(update: AdventureRuntimeUpdate) -> None:
    """任何转场后都只在这里同步落点奖励，避免 pre/post 两套实现分叉。"""
    if not update.adventure:
        return
    node = get_adventure_store().get_node(update.adventure["active_node_id"])
    reward_changed = _sync_pending_rewards(update.adventure, node, update.state_update)
    if reward_changed and not update.applied:
        update.applied = "state_update"


def _apply_director_node_facts(
    adventure: dict[str, Any],
    node: Any,
    decision: AdventureProgressDecision,
    state_update: dict[str, Any],
) -> bool:
    """Director 只能写入当前节点声明过的事实，避免语义裁定越权改剧情。"""
    allowed_event_ids = set(node.events)
    allowed_clue_ids = {
        str(item.get("id"))
        for item in node.clues
        if isinstance(item, dict) and item.get("id")
    }
    changed = False
    for event_id in decision.completed_event_ids:
        if event_id in allowed_event_ids and event_id not in adventure["completed_event_ids"]:
            adventure["completed_event_ids"].append(event_id)
            changed = True

    for clue_id in decision.discovered_clue_ids:
        if clue_id in allowed_clue_ids and clue_id not in adventure["known_clue_ids"]:
            adventure["known_clue_ids"].append(clue_id)
            changed = True

    if changed:
        state_update["adventure"] = adventure
    return changed


def _apply_visible_clue_window(
    adventure: dict[str, Any],
    decision: AdventureProgressDecision,
    state_update: dict[str, Any],
) -> None:
    """Director 可收窄主模型看到的线索窗口；完整线索仍留在 adventure。"""
    known = set(adventure.get("known_clue_ids", []))
    visible = [clue_id for clue_id in decision.visible_clue_ids if clue_id in known]
    if visible:
        state_update["adventure_visible_clue_ids"] = visible


def _adventure_dict(value: Any) -> dict[str, Any]:
    return normalize_adventure_state(value)


def _director_message_role(message: Any) -> str:
    """统一 LangChain 与 trace 字典的角色名，避免同一历史在 pre/post 间抖动。"""
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, AIMessage):
        return "assistant"
    if isinstance(message, ToolMessage):
        return f"tool:{message.name or 'tool'}"
    if isinstance(message, dict):
        role = str(message.get("role") or message.get("type") or message.get("message_type") or "message")
        if role == "ai":
            return "assistant"
        if role == "user":
            return "human"
        if role == "tool":
            name = str(message.get("name", "") or "")
            return f"tool:{name}" if name else "tool"
        return role
    return "message"


def _message_brief(message: Any) -> dict[str, Any]:
    if is_adventure_node_frame_message(message):
        return _director_node_frame_brief(message)

    if isinstance(message, dict):
        content = message_content_to_text(message.get("content", ""))
        brief: dict[str, Any] = {"role": _director_message_role(message), "content": content[:3000]}
        name = str(message.get("name", "") or "")
        if name:
            brief["name"] = name
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            brief["tool_calls"] = [_stable_tool_call(tool_call) for tool_call in tool_calls if isinstance(tool_call, dict)]
        return brief

    content = message_content_to_text(getattr(message, "content", ""))
    brief: dict[str, Any] = {"role": _director_message_role(message), "content": content[:3000]}
    name = getattr(message, "name", "")
    if isinstance(name, str) and name:
        brief["name"] = name
    if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
        brief["tool_calls"] = [_stable_tool_call(tool_call) for tool_call in getattr(message, "tool_calls", []) if isinstance(tool_call, dict)]
    return brief


def _stable_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Director 只需要工具意图；运行时 id 会破坏历史前缀稳定性。"""
    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    name = str(tool_call.get("name") or function.get("name") or "")
    args = tool_call.get("args", function.get("arguments", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args}
    return {"name": name, "args": _stable_json_value(args)}


def _stable_json_value(value: Any) -> Any:
    """归一化 transcript 内的结构化字段，避免 key 顺序抖动影响 KC。"""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict):
        return {str(key): _stable_json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        return [_stable_json_value(item) for item in value]
    return value


def _director_message_history(messages: list[Any]) -> list[dict[str, Any]]:
    """director 吃稳定 append-only 转录；旧消息不要携带运行时随机 id。"""
    history: list[dict[str, Any]] = []
    for message in messages:
        if _is_internal_system_history_message(message) and not is_adventure_node_frame_message(message):
            continue
        history.append(_message_brief(message))
    return history


def _messages_with_active_node_frame(messages: list[Any], active_node_id: str) -> list[Any]:
    """兜底补齐当前节点帧；正常路径由会话服务持久追加，避免每轮重造。"""
    if _latest_adventure_node_frame_id(messages) == active_node_id:
        return messages
    return [*messages, build_adventure_node_frame_message(active_node_id)]


def _latest_adventure_node_frame_id(messages: list[Any]) -> str:
    """只认最后一个节点帧；回访同节点会在新位置再追加。"""
    for message in reversed(messages):
        if not is_adventure_node_frame_message(message):
            continue
        content = _message_content_text(message)
        raw = content.removeprefix(ADVENTURE_NODE_FRAME_MESSAGE_PREFIX).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        return str(payload.get("node_id", "") or "")
    return ""


def build_adventure_node_frame_message(node_id: str) -> HumanMessage:
    """把低频节点事实写成 append-only 内部帧；切节点时追加，不在历史中原地替换。"""
    node = get_adventure_store().get_node(node_id)
    payload = {
        "frame_type": "adventure_node",
        "node_id": node.id,
        "current_node": _director_current_node_view(node),
    }
    return HumanMessage(
        content=f"{ADVENTURE_NODE_FRAME_MESSAGE_PREFIX}\n{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def _director_node_frame_brief(message: Any) -> dict[str, Any]:
    """Director 将节点帧当作 ledger 事件读取，避免误认为玩家发言。"""
    content = _message_content_text(message)
    raw = content.removeprefix(ADVENTURE_NODE_FRAME_MESSAGE_PREFIX).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"role": "system:adventure_node_frame", "content": raw[:3000]}
    return {
        "role": "system:adventure_node_frame",
        "frame_type": "adventure_node",
        "node_id": str(payload.get("node_id", "")),
        "current_node": payload.get("current_node"),
    }


def _director_active_node_pointer(node: Any) -> dict[str, Any]:
    """尾部小指针声明当前有效节点；旧节点帧只作为路径历史。"""
    return {
        "active_node_id": node.id,
        "active_node_frame_role": "system:adventure_node_frame",
        "rule": "只有最后一个 node_id 等于 active_node_id 的冒险节点帧是当前节点事实；更早节点帧只作历史和回访参考。",
    }


def _director_current_node_view(node: Any) -> dict[str, Any]:
    """给 Director 的裁定视图保留所有合法 id，同时压掉重复演绎文本。"""
    return {
        "id": node.id,
        "title": node.title,
        "kind": node.kind,
        "source_refs": node.source_refs,
        "dm_summary": node.dm_summary,
        "player_visible_intro": node.player_visible_intro,
        "scene_beats": _dedupe_director_texts(getattr(node, "scene_beats", []), threshold=0.35),
        "rules_notes": _dedupe_director_texts(getattr(node, "rules_notes", []), threshold=0.35),
        "routing_notes": _dedupe_director_texts(getattr(node, "routing_notes", []), threshold=0.35),
        "clues": _dedupe_director_id_items(getattr(node, "clues", [])),
        "events": _dedupe_director_ids(getattr(node, "events", [])),
        "exits": _dedupe_director_exits([item.model_dump() for item in getattr(node, "exits", [])]),
        "fallbacks": _dedupe_director_fallbacks(getattr(node, "fallbacks", [])),
        "rewards": _dedupe_director_id_items(getattr(node, "rewards", [])),
    }


def _director_node_index_view(node: Any, *, source: str) -> dict[str, Any]:
    """非当前节点只做回访/切换索引；完整裁定等切成 current_node 后再给。"""
    return {
        "id": node.id,
        "title": node.title,
        "kind": node.kind,
        "source": source,
        "page_start": node.page_start,
        "page_end": node.page_end,
        "dm_summary": node.dm_summary,
        "player_visible_intro": node.player_visible_intro,
        "event_ids": _compact_director_ids(_dedupe_director_ids(getattr(node, "events", [])), limit=8),
        "clue_ids": _compact_director_ids(_director_item_ids(getattr(node, "clues", [])), limit=8),
        "exit_ids": _compact_director_ids([item.id for item in getattr(node, "exits", [])], limit=8),
    }


def _compact_director_ids(values: list[str], *, limit: int) -> list[str] | dict[str, Any]:
    """索引只帮定位节点；长 id 列表给前缀和总数，完整合同等切入当前节点。"""
    if len(values) <= limit:
        return values
    return {"items": values[:limit], "total": len(values)}


def _director_item_ids(values: list[Any]) -> list[str]:
    """索引视图只暴露可引用 id，具体事实在节点成为 current_node 后展开。"""
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", "")).strip()
        if item_id and item_id not in seen:
            seen.add(item_id)
            result.append(item_id)
    return result


def _dedupe_director_ids(values: list[Any]) -> list[str]:
    """id 合同必须完整但不需要重复出现。"""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _dedupe_director_texts(values: list[Any], *, threshold: float = 0.55) -> list[str]:
    """对无 id 的裁定说明做稳定近重复去重，保留第一条完整表述。"""
    result: list[str] = []
    signatures: list[set[str]] = []
    exact_seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        exact_key = _director_text_exact_key(text)
        if exact_key in exact_seen:
            continue
        signature = _director_text_signature(text)
        if signature and any(_director_signature_similarity(signature, item) >= threshold for item in signatures):
            continue
        exact_seen.add(exact_key)
        if signature:
            signatures.append(signature)
        result.append(text)
    return result


def _dedupe_director_fallbacks(values: list[Any]) -> list[dict[str, Any]]:
    """fallback 只给裁定兜底用，近重复分支保留 id 并指向首条完整指导。"""
    result: list[dict[str, Any]] = []
    signatures: list[tuple[str, set[str]]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        item = _stable_json_value(value)
        item_id = str(item.get("id", "")).strip()
        signature = _director_item_signature(item)
        alias_of = _matching_director_signature(signature, signatures, threshold=0.32)
        if alias_of and item_id:
            result.append({"id": item_id, "alias_of": alias_of})
            continue
        if signature:
            signatures.append((item_id or f"fallback_{len(result)}", signature))
        result.append(item)
    return result


def _dedupe_director_exits(values: list[Any]) -> list[dict[str, Any]]:
    """出口 id 全保留；同目标出口只保留第一条完整描述，后续作为别名。"""
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    by_target: dict[tuple[str, str], str] = {}
    signatures: list[tuple[str, set[str]]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        item = _stable_json_value(value)
        item_id = str(item.get("id", "")).strip()
        if item_id and item_id in seen_ids:
            continue
        if item_id:
            seen_ids.add(item_id)
        target_key = (
            str(item.get("next_node_id", "")),
            str(item.get("transition_kind", "")),
        )
        alias_of = by_target.get(target_key) if target_key[0] else ""
        if not alias_of:
            alias_of = _matching_director_signature(_director_item_signature(item), signatures, threshold=0.50)
        if alias_of and item_id:
            result.append(_director_alias_item(item, alias_of))
            continue
        result.append(item)
        if item_id:
            by_target[target_key] = item_id
            signature = _director_item_signature(item)
            if signature:
                signatures.append((item_id, signature))
    return result


def _dedupe_director_id_items(values: list[Any]) -> list[dict[str, Any]]:
    """有 id 的条目不删除 id；近重复内容改为指向首个事实条目。"""
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    signatures: list[tuple[str, set[str]]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        item = _stable_json_value(value)
        item_id = str(item.get("id", "")).strip()
        if item_id and item_id in seen_ids:
            continue
        signature = _director_item_signature(item)
        alias_of = _matching_director_signature(signature, signatures, threshold=0.55)
        if item_id:
            seen_ids.add(item_id)
        if alias_of and item_id:
            result.append(_director_alias_item(item, alias_of))
        else:
            result.append(item)
            if item_id and signature:
                signatures.append((item_id, signature))
    return result


def _director_alias_item(item: dict[str, Any], alias_of: str) -> dict[str, Any]:
    """重复事实保留可输出 id，把长描述交给首个等价条目承载。"""
    keep_keys = (
        "id",
        "label",
        "next_node_id",
        "requires",
        "transition_kind",
        "type",
        "amount",
        "scope",
    )
    brief = {key: item[key] for key in keep_keys if key in item}
    brief["alias_of"] = alias_of
    return brief


def _matching_director_signature(
    signature: set[str],
    signatures: list[tuple[str, set[str]]],
    *,
    threshold: float,
) -> str:
    if not signature:
        return ""
    for item_id, previous in signatures:
        if _director_signature_similarity(signature, previous) >= threshold:
            return item_id
    return ""


def _director_item_signature(item: dict[str, Any]) -> set[str]:
    fields = [
        str(item.get("label", "")),
        str(item.get("description", "")),
        str(item.get("condition", "")),
        str(item.get("dm_guidance", "")),
        str(item.get("next_node_id", "")),
        " ".join(str(value) for value in item.get("requires", []) if str(value).strip()),
    ]
    return _director_text_signature(" ".join(field for field in fields if field.strip()))


def _director_text_exact_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _director_text_signature(text: str) -> set[str]:
    """用可解释的中文 bigram/英文词签名识别明显近重复。"""
    normalized = re.sub(r"\s+", "", text.casefold())
    tokens = set(re.findall(r"[a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", normalized))
    for chunk in re.findall(r"[\u4e00-\u9fff]{3,}", normalized):
        tokens.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return tokens


def _director_signature_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _director_runtime_context(state: dict[str, Any]) -> dict[str, Any]:
    """把运行时裁定结论显式塞给 director，避免把内部帧混进对话历史。"""
    return {
        "adventure_runtime_directive": _state_value_to_dict(state.get("adventure_runtime_directive")),
        "adventure_guardrail_warning": _state_value_to_dict(state.get("adventure_guardrail_warning")),
        "system_authorized_facts": _system_authorized_facts(state),
    }


def _state_value_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_state_value_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: _state_value_to_dict(item) for key, item in value.items()}
    return value


def _message_content_text(message: Any) -> str:
    if isinstance(message, dict):
        return message_content_to_text(message.get("content", ""))
    return message_content_to_text(getattr(message, "content", ""))


def _is_real_human_message(message: Any) -> bool:
    if isinstance(message, HumanMessage):
        content = _message_content_text(message).strip()
        return bool(content) and not content.startswith("[系统")
    if isinstance(message, dict):
        role = str(message.get("role") or message.get("type") or message.get("message_type") or "")
        content = _message_content_text(message).strip()
        return role in {"human", "user"} and bool(content) and not content.startswith("[系统")
    return False


def _is_internal_system_history_message(message: Any) -> bool:
    if isinstance(message, HumanMessage) and is_internal_system_human_message(message):
        return True
    if isinstance(message, dict):
        content = _message_content_text(message).strip()
        if not content:
            return False
        return content.startswith("[系统")
    return False


def _director_intent_query(node: Any, messages: list[Any]) -> str:
    """把最近玩家意图与当前节点压成一个短查询，供语义检索和 director 共同使用。"""
    snippets: list[str] = [node.title, node.dm_summary]
    for message in reversed(messages):
        content = _message_content_text(message)
        if _is_real_human_message(message) and content:
            snippets.append(content)
        if len(" ".join(snippets)) > 300:
            break
    query = " ".join(part.strip() for part in snippets if str(part).strip())
    return query[:300]


def _latest_external_human_text(messages: list[Any]) -> str:
    """只取最近一条真实玩家文本，避免语义候选被主持人自说自话污染。"""
    for message in reversed(messages):
        if _is_real_human_message(message):
            content = _message_content_text(message).strip()
            if content:
                return content
    return ""


def _director_candidate_nodes(
    query: str,
    current_node_id: str,
    *,
    adventure: dict[str, Any],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """给 director 一小撮可切换的语义候选节点，不把整张图塞进提示词。"""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_candidate(node: Any, score: float, source: str) -> None:
        if node.id == current_node_id or node.id in seen:
            return
        seen.add(node.id)
        view = _director_node_index_view(node, source=source)
        view["score"] = score
        candidates.append(view)

    if query.strip():
        for node, score in get_adventure_store().search_nodes(query, limit=limit):
            add_candidate(node, score, "search")

    for node_id in returnable_node_ids(adventure, current_node_id):
        if node_id in seen or node_id == current_node_id:
            continue
        try:
            node = get_adventure_store().get_node(node_id)
        except Exception:
            continue
        add_candidate(node, 10.0, "history")

    return candidates[:limit]


def _director_returnable_nodes(adventure: dict[str, Any], current_node_id: str) -> list[dict[str, Any]]:
    """把待回访路径收束成更小的候选集，供 director 回头判断。"""
    nodes: list[dict[str, Any]] = []
    for node_id in returnable_node_ids(adventure, current_node_id):
        try:
            node = get_adventure_store().get_node(node_id)
        except Exception:
            continue
        source = "deferred" if node_id in adventure.get("deferred_node_ids", []) else "breadcrumb"
        nodes.append(_director_node_index_view(node, source=source))
    return nodes


def _director_route_memory(adventure: dict[str, Any], current_node_id: str) -> dict[str, Any]:
    """把路径记忆压成小块，帮助 director 区分前进、回切与回访。"""
    return {
        "breadcrumb_node_ids": recent_breadcrumb_ids(adventure, limit=8),
        "deferred_node_ids": list(adventure.get("deferred_node_ids", [])),
        "returnable_node_ids": returnable_node_ids(adventure, current_node_id),
        "transition_log_tail": list(adventure.get("transition_log", []))[-4:],
    }


def _parse_decision(raw: str) -> AdventureProgressDecision:
    text = _strip_json_fence(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return AdventureProgressDecision(reason="director 输出不是合法 JSON")
    return AdventureProgressDecision(
        completed_event_ids=[str(item) for item in payload.get("completed_event_ids", [])],
        discovered_clue_ids=[str(item) for item in payload.get("discovered_clue_ids", [])],
        exit_option_id=str(payload["exit_option_id"]) if payload.get("exit_option_id") else None,
        target_node_id=str(payload["target_node_id"]) if payload.get("target_node_id") else None,
        transition_kind=payload.get("transition_kind") if payload.get("transition_kind") in {"advance", "switch", "revisit"} else None,
        needs_player_choice=bool(payload.get("needs_player_choice", False)),
        confidence=float(payload.get("confidence", 0.0) or 0.0),
        reason=str(payload.get("reason", "")),
        desync_detected=bool(payload.get("desync_detected", False)),
        unsupported_claims=[str(item) for item in payload.get("unsupported_claims", [])][:5],
        warning=str(payload.get("warning", "")),
        visible_clue_ids=[str(item) for item in payload.get("visible_clue_ids", [])][:8],
    )


def _parse_pre_turn_decision(raw: str) -> AdventurePreTurnDecision:
    text = _strip_json_fence(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return AdventurePreTurnDecision(reason="director 输出不是合法 JSON")
    return AdventurePreTurnDecision(
        completed_event_ids=[str(item) for item in payload.get("completed_event_ids", [])],
        discovered_clue_ids=[str(item) for item in payload.get("discovered_clue_ids", [])],
        exit_option_id=str(payload["exit_option_id"]) if payload.get("exit_option_id") else None,
        target_node_id=str(payload["target_node_id"]) if payload.get("target_node_id") else None,
        transition_kind=payload.get("transition_kind") if payload.get("transition_kind") in {"advance", "switch", "revisit"} else None,
        needs_player_choice=bool(payload.get("needs_player_choice", False)),
        confidence=float(payload.get("confidence", 0.0) or 0.0),
        reason=str(payload.get("reason", "")),
    )


def _progress_decision_from_pre_turn(decision: AdventurePreTurnDecision) -> AdventureProgressDecision:
    return AdventureProgressDecision(
        completed_event_ids=decision.completed_event_ids,
        discovered_clue_ids=decision.discovered_clue_ids,
        exit_option_id=decision.exit_option_id,
        target_node_id=decision.target_node_id,
        transition_kind=decision.transition_kind,
        needs_player_choice=decision.needs_player_choice,
        confidence=decision.confidence,
        reason=decision.reason,
    )


def _drop_stale_pending_exits(adventure: dict[str, Any], allowed_exit_ids: set[str]) -> bool:
    pending = [item for item in adventure.get("pending_exit_option_ids", []) if item in allowed_exit_ids]
    if pending == adventure.get("pending_exit_option_ids", []):
        return False
    adventure["pending_exit_option_ids"] = pending
    return True


def _pre_turn_requires_main_agent_context(node: Any, player_message: str) -> bool:
    """多出口节点的泛化行动先让主模型演绎，避免后台把“继续”误解成具体出口。"""
    if len(getattr(node, "exits", []) or []) <= 1:
        return False
    normalized = player_message.strip().lower()
    return normalized in {"继续", "继续前进", "往前走", "前进", "走", "出发", "继续走"}


def _apply_exit_transition(
    adventure: dict[str, Any],
    node: Any,
    exit_option_id: str,
    decision: AdventureProgressDecision,
    *,
    state_update: dict[str, Any],
    directive_kind: str,
) -> AdventureRuntimeUpdate | None:
    ready_exit = next((item for item in node.exits if item.id == exit_option_id), None)
    if ready_exit is None:
        return None

    store = get_adventure_store()
    target_node_id = store.resolve_node_id(ready_exit.next_node_id)
    if target_node_id not in store._nodes:
        return None

    if settle_exit_local_requirements(adventure, node, ready_exit):
        state_update["adventure"] = adventure
    if _settle_director_selected_exit_clues(adventure, node, ready_exit):
        state_update["adventure"] = adventure

    # 中文注释：Director 已完成语义裁定；requires 只沉淀本地事实，不再充当第二道门锁。
    adventure = record_node_transition(
        adventure,
        from_node_id=node.id,
        to_node_id=target_node_id,
        kind="advance",
        reason=decision.reason,
        complete_current=True,
    )
    target_node = store.get_node(target_node_id)
    if apply_arrival_events(adventure, target_node, exit_option_id=ready_exit.id):
        state_update["adventure"] = adventure
    state_update.update(_node_transition_directive(target_node_id, directive_kind))
    return AdventureRuntimeUpdate(adventure=adventure, decision=decision, applied="advanced", state_update=state_update)

def _apply_target_transition(
    adventure: dict[str, Any],
    node: Any,
    target_node_id: str,
    decision: AdventureProgressDecision,
    *,
    state_update: dict[str, Any],
    directive_kind: str,
) -> AdventureRuntimeUpdate | None:
    store = get_adventure_store()
    original_target_node_id = store.resolve_node_id(target_node_id)
    target_node_id = _normalize_semantic_switch_target(adventure, node.id, original_target_node_id)
    if target_node_id == node.id:
        return AdventureRuntimeUpdate(decision=decision, applied="", state_update=state_update)

    returnable_ids = {store.resolve_node_id(item) for item in returnable_node_ids(adventure, node.id)}
    exit_match = next((item for item in node.exits if store.resolve_node_id(item.next_node_id) == target_node_id), None)
    transition_kind = decision.transition_kind or ("revisit" if target_node_id in returnable_ids else "switch")

    if transition_kind == "advance" and exit_match is not None:
        return _apply_exit_transition(
            adventure,
            node,
            exit_match.id,
            decision,
            state_update=state_update,
            directive_kind=directive_kind,
        )

    if target_node_id not in store._nodes:
        return None

    target_node = store.get_node(target_node_id)
    adventure = record_node_transition(
        adventure,
        from_node_id=node.id,
        to_node_id=target_node.id,
        kind=transition_kind,
        reason=decision.reason,
        complete_current=False,
    )
    if apply_arrival_events(adventure, target_node):
        state_update["adventure"] = adventure
    directive_kind_name = "node_revisited" if transition_kind == "revisit" else "node_switched"
    state_update.update(_node_transition_directive(target_node.id, directive_kind_name))
    return AdventureRuntimeUpdate(
        adventure=adventure,
        decision=decision,
        applied="revisited" if transition_kind == "revisit" else "switched",
        state_update=state_update,
    )


def _settle_director_selected_exit_clues(adventure: dict[str, Any], node: Any, exit_option: Any) -> bool:
    """Director 精确选择出口时，出口依赖的本地线索随转场写入状态。"""
    local_clue_ids = {
        str(item.get("id"))
        for item in getattr(node, "clues", []) or []
        if isinstance(item, dict) and item.get("id")
    }
    changed = False
    for requirement in getattr(exit_option, "requires", []) or []:
        if requirement in local_clue_ids and requirement not in adventure["known_clue_ids"]:
            adventure["known_clue_ids"].append(requirement)
            changed = True
    return changed


def _normalize_semantic_switch_target(
    adventure: dict[str, Any],
    current_node_id: str,
    target_node_id: str,
) -> str:
    """节点可声明入口路由；跨层级语义跳转先落入口，避免绕过里程碑。"""
    store = get_adventure_store()
    try:
        target_node = store.get_node(target_node_id)
    except KeyError:
        return target_node_id

    routing = getattr(target_node, "routing", {}) or {}
    if not isinstance(routing, dict) or not routing.get("requires_entry_before_inner_switch", False):
        return target_node_id
    entry_node_id = store.resolve_node_id(str(routing.get("entry_node_id", "")).strip())
    if not entry_node_id or entry_node_id == target_node_id or entry_node_id == current_node_id:
        return target_node_id
    if _has_visited_node(adventure, entry_node_id):
        return target_node_id
    return entry_node_id


def _has_visited_node(adventure: dict[str, Any], node_id: str) -> bool:
    """入口是否已建立只看通用路径事实，不绑定任何具体模组。"""
    if node_id in adventure.get("breadcrumb_node_ids", []):
        return True
    if node_id in adventure.get("unlocked_node_ids", []):
        return True
    return any(item.get("to_node_id") == node_id for item in adventure.get("transition_log", []) if isinstance(item, dict))


def _guardrail_state_update(
    state: dict[str, Any],
    node_id: str,
    decision: AdventureProgressDecision,
) -> dict[str, Any]:
    unsupported_claims = _filter_unsupported_claims(state, decision.unsupported_claims)
    warning = decision.warning
    desync_detected = decision.desync_detected
    if decision.unsupported_claims and not unsupported_claims:
        warning = ""
        desync_detected = False

    if desync_detected or warning or unsupported_claims:
        return {
            "adventure_guardrail_warning": {
                "node_id": node_id,
                "warning": warning or "上一轮主持回复包含当前节点未支持的剧情事实，下一轮必须先纠偏。",
                "unsupported_claims": unsupported_claims,
                "reason": decision.reason,
            }
        }
    if state.get("adventure_guardrail_warning"):
        return {"adventure_guardrail_warning": None}
    return {}


def _node_transition_directive(node_id: str, kind: str) -> dict[str, Any]:
    """节点切换后，下一次主持必须按新节点事实开场，避免继续沿旧叙事惯性发挥。"""
    verb = "推进到" if kind == "node_advanced" else "回访到" if kind == "node_revisited" else "切换到"
    return {
        "adventure_runtime_directive": {
            "kind": kind,
            "node_id": node_id,
            "instruction": f"冒险书签已由后台{verb} {node_id}。下一次叙事前先按当前节点事实重整场景，不要向玩家复述本内部指令。",
        },
        "adventure_guardrail_warning": None,
    }


def _sync_pending_rewards(
    adventure: dict[str, Any],
    node: Any,
    state_update: dict[str, Any],
) -> bool:
    """节点奖励只同步到待领取队列，实际发放交给专用工具。"""
    changed = sync_pending_node_rewards(adventure, node)
    if changed:
        state_update["adventure"] = adventure
    return changed


def _system_authorized_facts(state: dict[str, Any]) -> list[str]:
    """把稳定系统规则显式交给 director，避免把开局同伴误判为模组臆造。"""
    facts = [
        "Gundren Rockseeker / Gundren / 冈德伦·洛克希尔 / 甘德伦 是同一模组委托人；只提及其作为委托人、雇主、信件来源或目的地背景不算越界。",
        "Phandalin / 凡达林 / 凡戴尔 是同一模组目的地；只提及其作为目的地或地名音译不算越界。",
    ]
    if _opening_companion_authorized(state):
        facts.insert(
            0,
            "开局友方准则授权一名名为格林的 fighter_companion 战士同伴；格林是玩家开局同行队友，不是修达·霍温特，其姓名、同行身份与简短引入不属于冒险节点越界。",
        )
    return facts


def _filter_unsupported_claims(state: dict[str, Any], claims: list[str]) -> list[str]:
    """写入警告前过滤系统层已授权的事实，保留真正越过模组的剧情。"""
    filtered: list[str] = []
    for claim in claims:
        if _opening_companion_authorized(state) and _is_opening_companion_claim(claim):
            continue
        if _is_known_module_alias_only_claim(claim):
            continue
        filtered.append(claim)
    return filtered


def _opening_companion_authorized(state: dict[str, Any]) -> bool:
    player = state.get("player")
    scene_units = state.get("scene_units") or {}
    if hasattr(scene_units, "model_dump"):
        scene_units = scene_units.model_dump()
    has_companion = hasattr(scene_units, "__contains__") and "fighter_companion" in scene_units
    return has_companion or (bool(player) and not scene_units)


def _is_opening_companion_claim(claim: str) -> bool:
    text = claim.lower()
    if any(term in text for term in ("修达", "sildar", "霍温特", "hallwinter")):
        return False
    companion_terms = (
        "fighter_companion",
        "开局友方",
        "友方",
        "同伴",
        "同行",
        "旅伴",
        "战士",
        "格林",
        "甘德伦",
        "信件",
        "结识",
    )
    adventure_leap_terms = (
        "红标",
        "克拉格玛",
        "酒馆",
        "矿坑",
        "城堡",
        "营地",
        "黑蜘蛛",
        "救出",
        "被绑",
        "被抓",
        "关押",
        "奖励",
        "主线",
    )
    return any(term in text for term in companion_terms) and not any(term in text for term in adventure_leap_terms)


def _known_module_aliases() -> dict[str, list[str]]:
    """把常见音译差异交给 director，避免专名拼法把事实判断带偏。"""
    return {
        "gundren_rockseeker": ["Gundren Rockseeker", "Gundren", "冈德伦·洛克希尔", "冈德伦", "甘德伦"],
        "phandalin": ["Phandalin", "凡达林", "凡戴尔"],
        "triboar_trail": ["Triboar Trail", "三猪小径", "三野猪小径"],
        "neverwinter": ["Neverwinter", "无冬城"],
    }


def _is_known_module_alias_only_claim(claim: str) -> bool:
    text = claim.lower()
    alias_terms = tuple(alias.lower() for aliases in _known_module_aliases().values() for alias in aliases)
    if not any(alias in text for alias in alias_terms):
        return False
    unsupported_state_terms = (
        "已抵达",
        "抵达",
        "到达",
        "进入",
        "救出",
        "被救出",
        "已会合",
        "见到",
        "死亡",
        "杀死",
        "被绑",
        "被抓",
        "关押",
        "红标",
        "酒馆",
        "城堡",
        "营地",
    )
    return not any(term in text for term in unsupported_state_terms)


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    return text
