"""冒险模组工具 — 让模型按 PDF 节点图读取与推进剧情。"""

from __future__ import annotations

import json
import re
from typing import Any, Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.adventures.models import AdventureNode, AdventureState
from app.adventures.navigation import apply_arrival_events, normalize_adventure_state, record_node_transition, settle_exit_local_requirements
from app.adventures.rewards import claim_pending_reward, sync_pending_node_rewards
from app.adventures.store import get_adventure_store
from app.services.skills import load_skill_content


SURPRISE_RULE_OVERRIDE = {
    "topic": "surprise",
    "rule": (
        "本项目使用新版突袭规则：模组中“突袭导致第一轮无法行动/无法执行动作”的旧规则不适用。"
        "被突袭者只在开战先攻检定上获得劣势；不跳过首回合，也不因突袭禁用反应。"
    ),
}

SURPRISE_RULE_HINT = (
    "按本项目新版突袭规则处理：该角色被突袭时只在开战先攻检定上获得劣势，"
    "不跳过首回合，也不因突袭禁用反应"
)


def _adventure_dict(state: dict | None) -> dict:
    """读取当前冒险状态；空状态默认从 Lost Mine 初始节点开始。"""
    if not state:
        return AdventureState().model_dump()
    return normalize_adventure_state(state.get("adventure"))


def _normalize_project_rules(value: object) -> object:
    """在不改写源数据的前提下，把旧模组规则转换成本项目可执行口径。"""
    if isinstance(value, str):
        normalized = re.sub(
            r"被突袭且\s*在战斗第一轮无法执行任何动作(?:（见规则书的“突袭”）)?",
            f"被突袭；{SURPRISE_RULE_HINT}",
            value,
        )
        normalized = normalized.replace("见规则书中的突袭规则", "按本项目新版突袭规则处理")
        normalized = normalized.replace("见规则书的“突袭”", "按本项目新版突袭规则处理")
        normalized = normalized.replace("突袭轮", "伏击展开阶段")
        return normalized

    if isinstance(value, list):
        return [_normalize_project_rules(item) for item in value]

    if isinstance(value, dict):
        return {key: _normalize_project_rules(item) for key, item in value.items()}

    return value


def _mentions_surprise(value: object) -> bool:
    """识别需要向模型声明新版突袭覆盖规则的节点材料。"""
    if isinstance(value, str):
        return "突袭" in value
    if isinstance(value, list):
        return any(_mentions_surprise(item) for item in value)
    if isinstance(value, dict):
        return any(_mentions_surprise(item) for item in value.values())
    return False


def _node_payload(node: AdventureNode, adventure: dict) -> dict:
    """返回给模型的节点材料，显式区分玩家可见信息与 DM 私密信息。"""
    store = get_adventure_store()
    completed_events = set(adventure.get("completed_event_ids", []))
    known_clues = set(adventure.get("known_clue_ids", []))
    available_exits = []
    for exit_option in node.exits:
        resolved_next_node_id = store.resolve_node_id(exit_option.next_node_id)
        missing = [item for item in exit_option.requires if item not in completed_events and item not in known_clues]
        available_exits.append(
            {
                "id": exit_option.id,
                "label": exit_option.label,
                "next_node_id": exit_option.next_node_id,
                "resolved_next_node_id": resolved_next_node_id,
                "requires": exit_option.requires,
                "available": not missing and resolved_next_node_id in store._nodes,
                "missing": missing,
                "description": exit_option.description,
                "transition_kind": getattr(exit_option, "transition_kind", "advance"),
            }
        )

    # 中文注释：推进只能看 available_exits；PDF 解析候选出口不是运行时出口，避免模型把空候选误判为无出口。
    node_payload = {
        "id": node.id,
        "title": node.title,
        "kind": node.kind,
        "source_pages": [node.page_start, node.page_end],
        "source_refs": node.source_refs,
        "source_excerpt": node.source_excerpt,
        "source_text": node.source_text,
        "subsections": node.subsections,
        "dm_summary": node.dm_summary,
        "player_visible_intro": node.player_visible_intro,
        "scene_beats": node.scene_beats,
        "npc_reveals": node.npc_reveals,
        "rules_notes": node.rules_notes,
        "fallbacks": node.fallbacks,
        "secrets": node.secrets,
        "checks": node.checks,
        "encounters": [item.model_dump() for item in node.encounters],
        "rewards": node.rewards,
        "clues": node.clues,
        "events": node.events,
        "dm_guidance": node.dm_guidance,
    }
    if node.candidate_exits:
        node_payload["candidate_exits"] = node.candidate_exits

    payload = {
        "node": {
            **node_payload,
        },
        "progression_rule": "剧情推进出口只看顶层 available_exits；available_exits 非空且 available=true 时即可用对应 id 调用 advance。",
        "available_exits": available_exits,
        "adventure_state": adventure,
    }
    if _mentions_surprise(payload["node"]):
        payload["node"]["rules_overrides"] = [SURPRISE_RULE_OVERRIDE]
    return _normalize_project_rules(payload)


def _tool_message(payload: dict, tool_call_id: str | None) -> ToolMessage:
    """冒险工具统一输出 JSON，降低模型误读自然语言的概率。"""
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        tool_call_id=tool_call_id,
    )


# 冒险运行时公共实现：后台 Director、脚本和测试共用同一套状态写入。
def _load_adventure_node_impl(node_id: str | None, state: dict | None, tool_call_id: str | None) -> Command:
    adventure = _adventure_dict(state)
    store = get_adventure_store()
    target_node_id = store.resolve_node_id(node_id or adventure["active_node_id"])
    node = store.get_node(target_node_id)
    if target_node_id not in adventure["unlocked_node_ids"]:
        adventure["unlocked_node_ids"].append(target_node_id)

    payload = _node_payload(node, adventure)
    return Command(update={"adventure": adventure, "messages": [_tool_message(payload, tool_call_id)]})


def _inspect_adventure_state_impl(include_help: bool, state: dict | None, tool_call_id: str | None) -> Command:
    if include_help:
        return Command(update={"messages": [
            ToolMessage(content=load_skill_content("adventure_module"), tool_call_id=tool_call_id)
        ]})

    adventure = _adventure_dict(state)
    node = get_adventure_store().get_node(adventure["active_node_id"])
    payload = {
        "adventure_state": adventure,
        "active_node": {
            "id": node.id,
            "title": node.title,
            "kind": node.kind,
            "source_pages": [node.page_start, node.page_end],
        },
    }
    return Command(update={"messages": [_tool_message(payload, tool_call_id)]})


def _search_adventure_nodes_impl(query: str, limit: int, state: dict | None, tool_call_id: str | None) -> Command:
    results = get_adventure_store().search_nodes(query, limit=max(1, min(limit, 8)))
    payload = {
        "query": query,
        "results": [_node_brief(node, score) for node, score in results],
        "adventure_state": _adventure_dict(state),
    }
    return Command(update={"messages": [_tool_message(payload, tool_call_id)]})


def _switch_adventure_node_impl(node_id: str, reason: str, state: dict | None, tool_call_id: str | None) -> Command:
    adventure = _adventure_dict(state)
    current_node_id = adventure["active_node_id"]
    store = get_adventure_store()
    resolved_node_id = store.resolve_node_id(node_id)
    node = store.get_node(resolved_node_id)
    adventure = record_node_transition(
        adventure,
        from_node_id=current_node_id,
        to_node_id=node.id,
        kind="switch",
        reason=reason,
        complete_current=False,
    )

    payload = _node_payload(node, adventure)
    payload["result"] = {"from": current_node_id, "to": node.id, "reason": reason}
    return Command(update={"adventure": adventure, "messages": [_tool_message(payload, tool_call_id)]})


def _reveal_adventure_clue_impl(clue_id: str, state: dict | None, tool_call_id: str | None) -> Command:
    adventure = _adventure_dict(state)
    if clue_id not in adventure["known_clue_ids"]:
        adventure["known_clue_ids"].append(clue_id)

    node = get_adventure_store().get_node(adventure["active_node_id"])
    payload = _node_payload(node, adventure)
    payload["result"] = f"已解锁线索: {clue_id}"
    return Command(update={"adventure": adventure, "messages": [_tool_message(payload, tool_call_id)]})


def _mark_adventure_event_impl(event_id: str, state: dict | None, tool_call_id: str | None) -> Command:
    adventure = _adventure_dict(state)
    node = get_adventure_store().get_node(adventure["active_node_id"])
    if event_id not in node.events:
        payload = {
            "error": f"事件不属于当前节点: {event_id}",
            "allowed_event_ids": node.events,
            "adventure_state": adventure,
        }
        return Command(update={"messages": [_tool_message(payload, tool_call_id)]})
    if event_id not in adventure["completed_event_ids"]:
        adventure["completed_event_ids"].append(event_id)

    payload = {"result": f"已记录剧情事件: {event_id}", "adventure_state": adventure}
    return Command(update={"adventure": adventure, "messages": [_tool_message(payload, tool_call_id)]})


def _resolve_adventure_node_impl(
    outcome: str,
    clue_ids: list[str] | None,
    event_ids: list[str] | None,
    state: dict | None,
    tool_call_id: str | None,
) -> Command:
    """收束当前节点的稳定结果，并把下一步推进选择交还给节点出口。"""
    adventure = _adventure_dict(state)
    node = get_adventure_store().get_node(adventure["active_node_id"])
    allowed_clue_ids = {
        str(item.get("id"))
        for item in node.clues
        if isinstance(item, dict) and item.get("id")
    }
    for clue_id in clue_ids or []:
        if clue_id in allowed_clue_ids and clue_id not in adventure["known_clue_ids"]:
            adventure["known_clue_ids"].append(clue_id)
    for event_id in event_ids or []:
        if event_id in node.events and event_id not in adventure["completed_event_ids"]:
            adventure["completed_event_ids"].append(event_id)

    payload = _node_payload(node, adventure)
    ready_exits = [item for item in payload["available_exits"] if item["available"]]
    payload["result"] = {
        "outcome": outcome,
        "recorded_clue_ids": clue_ids or [],
        "recorded_event_ids": event_ids or [],
    }
    if len(ready_exits) == 1:
        payload["recommended_action"] = {
            "kind": "advance",
            "option_id": ready_exits[0]["id"],
            "reason": "当前节点只有一个可用出口，收束后应沿该出口完成节点并推进书签。",
        }
    elif len(ready_exits) > 1:
        payload["recommended_action"] = {
            "tool": "ask_player_or_advance_declared_choice",
            "available_option_ids": [item["id"] for item in ready_exits],
            "reason": "当前节点有多个可用出口；若玩家已明确去向，用对应 option_id 推进，否则把选择呈现给玩家。",
        }
    else:
        payload["recommended_action"] = {
            "tool": "continue_current_node",
            "reason": "当前节点暂无满足条件的可用出口，继续围绕本节点主持。",
        }
    return Command(update={"adventure": adventure, "messages": [_tool_message(payload, tool_call_id)]})


def _advance_adventure_impl(option_id: str, state: dict | None, tool_call_id: str | None) -> Command:
    store = get_adventure_store()
    adventure = _adventure_dict(state)
    current_node = store.get_node(adventure["active_node_id"])
    exit_option = next((item for item in current_node.exits if item.id == option_id), None)
    if exit_option is None:
        payload = {
            "error": f"当前节点没有出口: {option_id}",
            "hint": "advance 的 option_id 必须来自 available_exits.id；事件 id 应通过 resolve 的 event_ids 记录。",
            "available_exit_ids": [item.id for item in current_node.exits],
            "current_node_event_ids": current_node.events,
            "adventure_state": adventure,
        }
        return Command(update={"messages": [_tool_message(payload, tool_call_id)]})

    resolved_next_node_id = store.resolve_node_id(exit_option.next_node_id)
    if resolved_next_node_id not in store._nodes:
        payload = {
            "error": f"出口目标节点不存在: {exit_option.next_node_id}",
            "hint": "当前 canonical 节点图里还没有这个落点；请先补齐节点文件或改走其它出口。",
            "available_exit_ids": [item.id for item in current_node.exits],
            "current_node_event_ids": current_node.events,
            "adventure_state": adventure,
        }
        return Command(update={"messages": [_tool_message(payload, tool_call_id)]})

    settle_exit_local_requirements(adventure, current_node, exit_option)

    completed_events = set(adventure["completed_event_ids"])
    known_clues = set(adventure["known_clue_ids"])
    missing = [item for item in exit_option.requires if item not in completed_events and item not in known_clues]
    if missing:
        payload = {"error": f"出口条件未满足: {', '.join(missing)}", "adventure_state": adventure}
        return Command(update={"messages": [_tool_message(payload, tool_call_id)]})

    adventure = record_node_transition(
        adventure,
        from_node_id=current_node.id,
        to_node_id=resolved_next_node_id,
        kind="advance",
        reason=f"advance:{option_id}",
        complete_current=True,
    )

    next_node = store.get_node(resolved_next_node_id)
    apply_arrival_events(adventure, next_node, exit_option_id=exit_option.id)
    sync_pending_node_rewards(adventure, next_node)
    payload = _node_payload(next_node, adventure)
    payload["result"] = f"已推进剧情: {current_node.id} -> {next_node.id}"
    if adventure.get("pending_reward_grants"):
        payload["pending_reward_grants"] = adventure["pending_reward_grants"]
    update: dict[str, Any] = {"adventure": adventure, "messages": [_tool_message(payload, tool_call_id)]}
    return Command(update=update)


def _node_brief(node: AdventureNode, score: float | None = None) -> dict:
    """搜索结果只返回足够模型判断是否加载的摘要。"""
    payload = {
        "id": node.id,
        "title": node.title,
        "kind": node.kind,
        "source_pages": [node.page_start, node.page_end],
        "source_excerpt": node.source_excerpt,
        "dm_guidance_keys": [key for key, values in node.dm_guidance.items() if values],
        "has_encounters": bool(node.encounters),
        "clue_ids": [str(item.get("id", "")) for item in node.clues if isinstance(item, dict)],
    }
    if score is not None:
        payload["score"] = score
    return payload


@tool
def claim_adventure_reward(
    reward_id: str,
    *,
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None,
) -> Command:
    """领取后台已判定为待发放的剧情节点奖励，并把奖励内容告知玩家。
    只能逐字复制运行状态帧或冒险工具结果中列出的 pending_reward_grants.id；没有待发放奖励时不得调用本工具。
    不要根据工具说明、节点摘要或历史对话猜测 reward_id；重复领取同一个 reward_id 不会再次加 XP。
    XP 会写入角色卡；金币写入钱袋；财宝和道具写入背包。
    成功后要让玩家知道拿到了什么奖励，以及这笔奖励为什么发放。

    Args:
        reward_id: 待领取剧情奖励 ID，必须来自 pending_reward_grants。
    """
    adventure, player, result = claim_pending_reward(state or {}, reward_id)
    if result.get("ok"):
        reward = result["reward"]
        reward_summary: dict[str, Any] = {
            "ok": True,
            "reward_id": reward["id"],
            "type": reward["type"],
            "amount": reward["amount"],
            "pending_reward_ids": result.get("pending_reward_ids", []),
        }
        if reward.get("currency"):
            reward_summary["currency"] = reward["currency"]
        if "previous_xp" in reward:
            reward_summary["previous_xp"] = reward["previous_xp"]
        if "current_xp" in reward:
            reward_summary["current_xp"] = reward["current_xp"]
        if "current_amount" in reward:
            reward_summary["current_amount"] = reward["current_amount"]
        if "inventory_item_id" in reward:
            reward_summary["inventory_item_id"] = reward["inventory_item_id"]
        if reward.get("description"):
            reward_summary["description"] = reward["description"]
        payload: dict[str, Any] = {
            "tool": "claim_adventure_reward",
            "result": reward_summary,
        }
        description = str(reward.get("description", "")).strip()
        payload["message"] = _reward_claim_message(reward, description)
    else:
        # 中文注释：失败时只返回纠错所需字段，避免把完整冒险状态塞回模型上下文。
        payload = {
            "tool": "claim_adventure_reward",
            "result": {
                "ok": False,
                "error": result.get("error", "未知错误"),
                "pending_reward_ids": result.get("pending_reward_ids", []),
                "claimed_reward_ids": result.get("claimed_reward_ids", []),
            },
        }
        payload["message"] = (
            f"剧情奖励未发放：{result.get('error', '未知错误')}。"
            "只能领取 pending_reward_grants 中当前列出的奖励，不要根据工具说明或历史内容猜测奖励 ID。"
        )

    update: dict[str, Any] = {"messages": [_tool_message(payload, tool_call_id)]}
    if result.get("ok") or result.get("error", "").startswith("奖励已领取"):
        update["adventure"] = adventure
    if player is not None:
        update["player"] = player
    return Command(update=update)


def _reward_claim_message(reward: dict[str, Any], description: str) -> str:
    """工具消息直接给出玩家可见奖励，避免主模型猜测奖励内容。"""
    reward_type = str(reward.get("type", "")).lower()
    if reward_type == "xp":
        message = f"已发放剧情奖励 {reward['id']}: +{reward['amount']} XP，当前 XP {reward['current_xp']}。"
    elif reward_type == "gold":
        currency = str(reward.get("currency") or "gp").upper()
        message = f"已发放剧情奖励 {reward['id']}: +{reward.get('amount', 0)} {currency}，当前 {reward.get('current_amount', 0)} {currency}。"
    else:
        message = f"已加入背包 {reward['id']}: {reward.get('amount', 0)} 项 {reward_type or 'reward'}。"
    return message + (description if description else "")


