"""冒险路径导航 — 统一维护节点回溯、回访与转场记录。"""

from __future__ import annotations

import time
from typing import Any, Literal

from app.adventures.models import AdventureState
from app.adventures.rewards import normalize_pending_reward_grants
from app.adventures.store import get_node_generation_reference


AdventureTransitionKind = Literal["advance", "switch", "revisit"]
AUTOMATIC_ENTRY_EVENT_PREFIXES = ("enter_", "arrive_", "arrived_")
_LEGACY_NODE_ID_ALIASES: dict[str, str] = {
    "lost_mine_start": "adventure_hook_meet_me_in_phandalin",
    "phandalin_arrival": "phandalin",
    "cragmaw_hideout": "cragmaw_hideout_entrance",
    "redbrand_ruffians": "sleeping_giant_redbrand_ruffians",
    "redbrand_hideout": "tresendar_manor_approach",
    "redbrand_hideout_entrance": "tresendar_manor_approach",
    "spiders_web": "spider_web_overview",
    "spider_web": "spider_web_overview",
    "cragmaw_castle": "cragmaw_castle_search",
    "wave_echo_cave": "wave_echo_overview",
}


def normalize_adventure_state(value: Any) -> dict[str, Any]:
    """把旧状态补成带路径历史的新契约。"""
    if not value:
        adventure = AdventureState().model_dump()
    elif hasattr(value, "model_dump"):
        adventure = value.model_dump()
    else:
        adventure = AdventureState.model_validate(value).model_dump()

    adventure["module_id"] = str(adventure.get("module_id") or AdventureState().module_id)
    adventure["active_node_id"] = _canonical_node_id(str(adventure.get("active_node_id") or AdventureState().active_node_id))
    adventure["unlocked_node_ids"] = _dedupe_node_ids(adventure.get("unlocked_node_ids", []))
    adventure["completed_node_ids"] = _dedupe_strings(adventure.get("completed_node_ids", []))
    adventure["known_clue_ids"] = _dedupe_strings(adventure.get("known_clue_ids", []))
    adventure["completed_event_ids"] = _dedupe_strings(adventure.get("completed_event_ids", []))
    adventure["claimed_reward_ids"] = _dedupe_strings(adventure.get("claimed_reward_ids", []))
    adventure["pending_reward_grants"] = normalize_pending_reward_grants(adventure.get("pending_reward_grants", []))
    adventure["pending_exit_option_ids"] = _dedupe_strings(adventure.get("pending_exit_option_ids", []))
    adventure["breadcrumb_node_ids"] = _normalize_breadcrumbs(adventure.get("breadcrumb_node_ids", []), adventure["active_node_id"])
    adventure["deferred_node_ids"] = _normalize_deferred(adventure.get("deferred_node_ids", []), adventure["active_node_id"])
    adventure["transition_log"] = _normalize_transition_log(adventure.get("transition_log", []))
    if adventure["active_node_id"] not in adventure["unlocked_node_ids"]:
        adventure["unlocked_node_ids"].append(adventure["active_node_id"])
    return AdventureState.model_validate(adventure).model_dump()


def record_node_transition(
    adventure: Any,
    *,
    from_node_id: str,
    to_node_id: str,
    kind: AdventureTransitionKind,
    reason: str = "",
    complete_current: bool = False,
) -> dict[str, Any]:
    """把一次节点切换写进状态；完成与回访由 kind 决定。"""
    adventure_dict = normalize_adventure_state(adventure)
    if from_node_id == to_node_id:
        return adventure_dict

    current_time = time.time()

    if complete_current:
        adventure_dict["completed_node_ids"] = _append_unique(adventure_dict["completed_node_ids"], from_node_id)
        adventure_dict["deferred_node_ids"] = [node_id for node_id in adventure_dict["deferred_node_ids"] if node_id != from_node_id]
    elif from_node_id not in adventure_dict["completed_node_ids"]:
        adventure_dict["deferred_node_ids"] = _append_unique(adventure_dict["deferred_node_ids"], from_node_id)

    adventure_dict["active_node_id"] = to_node_id
    adventure_dict["unlocked_node_ids"] = _append_unique(adventure_dict["unlocked_node_ids"], to_node_id)
    adventure_dict["pending_exit_option_ids"] = []
    adventure_dict["deferred_node_ids"] = [node_id for node_id in adventure_dict["deferred_node_ids"] if node_id != to_node_id]
    adventure_dict["breadcrumb_node_ids"] = _append_breadcrumb(adventure_dict["breadcrumb_node_ids"], to_node_id)
    adventure_dict["transition_log"].append(
        {
            "timestamp": current_time,
            "from_node_id": from_node_id,
            "to_node_id": to_node_id,
            "kind": kind,
            "reason": reason,
        }
    )
    return AdventureState.model_validate(adventure_dict).model_dump()


def returnable_node_ids(adventure: Any, current_node_id: str | None = None) -> list[str]:
    """把历史路径里仍可能回访的节点按新鲜度排出来。"""
    adventure_dict = normalize_adventure_state(adventure)
    current = current_node_id or adventure_dict["active_node_id"]
    ordered: list[str] = []
    for node_id in reversed(adventure_dict.get("deferred_node_ids", [])):
        if node_id and node_id != current and node_id not in ordered:
            ordered.append(node_id)
    for node_id in reversed(adventure_dict.get("breadcrumb_node_ids", [])):
        if node_id and node_id != current and node_id not in ordered:
            ordered.append(node_id)
    return _filter_stale_returnable_nodes(ordered, current)


def _filter_stale_returnable_nodes(node_ids: list[str], current_node_id: str) -> list[str]:
    """进入新章节后隐藏过久远的房间级回访点，避免 Director 被旧路径牵回开局。"""
    rules = get_node_generation_reference().get("stale_return_rules", [])
    if not rules:
        return node_ids
    hidden: set[str] = set()
    kept: set[str] = set()
    for rule in rules:
        prefixes = [str(item) for item in rule.get("when_active_prefixes", [])]
        if not any(current_node_id == prefix or current_node_id.startswith(prefix) for prefix in prefixes):
            continue
        hidden.update(str(item) for item in rule.get("hide_node_ids", []))
        kept.update(str(item) for item in rule.get("keep_node_ids", []))
    if not hidden:
        return node_ids
    return [node_id for node_id in node_ids if node_id not in hidden or node_id in kept]


def settle_exit_local_requirements(adventure: dict[str, Any], node: Any, exit_option: Any) -> bool:
    """出口已被明确选中时，沉淀该出口依赖的当前节点事实。"""
    allow_local_clues = len(getattr(node, "exits", []) or []) <= 1
    local_event_ids = set(getattr(node, "events", []) or [])
    local_clue_ids = {
        str(item.get("id"))
        for item in getattr(node, "clues", []) or []
        if isinstance(item, dict) and item.get("id")
    }
    changed = False
    for requirement in getattr(exit_option, "requires", []) or []:
        if requirement in local_event_ids and requirement not in adventure["completed_event_ids"]:
            adventure["completed_event_ids"].append(requirement)
            changed = True
        if allow_local_clues and requirement in local_clue_ids and requirement not in adventure["known_clue_ids"]:
            adventure["known_clue_ids"].append(requirement)
            changed = True
    return changed


def apply_arrival_events(adventure: dict[str, Any], target_node: Any, *, exit_option_id: str | None = None) -> bool:
    """落点节点的进入事实由转场统一写入，奖励和主持视角共用这一事实。"""
    completed = adventure["completed_event_ids"]
    added = False
    for event_id in getattr(target_node, "events", []) or []:
        if event_id == exit_option_id or event_id.startswith(AUTOMATIC_ENTRY_EVENT_PREFIXES):
            if event_id not in completed:
                completed.append(event_id)
                added = True
    return added


def recent_breadcrumb_ids(adventure: Any, limit: int = 6) -> list[str]:
    """取最近访问过的节点，给运行帧和路由器做轻量提示。"""
    adventure_dict = normalize_adventure_state(adventure)
    trail = [node_id for node_id in adventure_dict.get("breadcrumb_node_ids", []) if node_id]
    if limit <= 0:
        return []
    return trail[-limit:]


def _dedupe_strings(values: list[Any]) -> list[str]:
    deduped: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _canonical_node_id(value: str) -> str:
    """旧节点文件删除后，历史状态里的旧 ID 在边界处统一收敛。"""
    return _LEGACY_NODE_ID_ALIASES.get(value, value)


def _dedupe_node_ids(values: list[Any]) -> list[str]:
    return _dedupe_strings([_canonical_node_id(str(value).strip()) for value in values or []])


def _normalize_breadcrumbs(values: list[Any], active_node_id: str) -> list[str]:
    breadcrumbs = [_canonical_node_id(node_id) for node_id in _string_list(values)]
    if not breadcrumbs:
        return [active_node_id]
    if breadcrumbs[-1] != active_node_id:
        breadcrumbs.append(active_node_id)
    return breadcrumbs


def _normalize_deferred(values: list[Any], active_node_id: str) -> list[str]:
    deferred = [node_id for node_id in _dedupe_node_ids(values) if node_id != active_node_id]
    return deferred


def _normalize_transition_log(values: list[Any]) -> list[dict[str, Any]]:
    log: list[dict[str, Any]] = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        entry = {
            "timestamp": item.get("timestamp", 0.0),
            "from_node_id": _canonical_node_id(str(item.get("from_node_id", ""))),
            "to_node_id": _canonical_node_id(str(item.get("to_node_id", ""))),
            "kind": str(item.get("kind", "")),
            "reason": str(item.get("reason", "")),
        }
        if entry["from_node_id"] and entry["to_node_id"] and entry["kind"]:
            log.append(entry)
    return log


def _append_unique(values: list[str], item: str) -> list[str]:
    if item and item not in values:
        return [*values, item]
    return values


def _append_breadcrumb(values: list[str], item: str) -> list[str]:
    if not item:
        return values
    if values and values[-1] == item:
        return values
    return [*values, item]


def _string_list(values: list[Any]) -> list[str]:
    items: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text:
            items.append(text)
    return items
