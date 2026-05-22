"""冒险线索投影 — 保留完整事实，但只把少量相关线索喂给模型。"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.adventures.navigation import normalize_adventure_state
from app.adventures.store import get_adventure_store


def project_known_clue_window(
    adventure: Any,
    *,
    current_node_id: str = "",
    query: str = "",
    limit: int = 6,
    recent_limit: int = 3,
) -> list[str]:
    """把累计线索压成一个小窗口，避免长列表持续干扰主持和裁定。"""
    adventure_dict = normalize_adventure_state(adventure or {})
    known_clue_ids = [str(item).strip() for item in adventure_dict.get("known_clue_ids", []) if str(item).strip()]
    if not known_clue_ids or limit <= 0:
        return []

    current_node_clues = set(_current_node_clue_ids(current_node_id))
    semantic_hits = set(_query_matched_clue_ids(query, known_clue_ids))
    recent_clues = set(known_clue_ids[-recent_limit:]) if recent_limit > 0 else set()

    scored_ids: list[tuple[int, int, str]] = []
    known_index = {clue_id: index for index, clue_id in enumerate(known_clue_ids)}
    for clue_id in known_clue_ids:
        score = known_index[clue_id]
        if clue_id in current_node_clues:
            score += 10_000
        if clue_id in semantic_hits:
            score += 5_000
        if clue_id in recent_clues:
            score += 2_000 + known_index[clue_id]
        scored_ids.append((score, known_index[clue_id], clue_id))

    scored_ids.sort(key=lambda item: (-item[0], item[1]))
    return [clue_id for _, _, clue_id in scored_ids[:limit]]


def format_known_clue_window(clue_ids: list[str], *, total_count: int) -> str:
    """只展示精选窗口，额外线索数量保留成计数，不展开长列表。"""
    if not clue_ids:
        return "[]"
    text = repr(clue_ids)
    omitted = max(0, total_count - len(clue_ids))
    if omitted:
        return f"{text}（另有 {omitted} 条）"
    return text


@lru_cache(maxsize=1)
def _clue_catalog() -> dict[str, dict[str, str]]:
    """给简单语义命中准备一个轻量索引，只构建一次。"""
    catalog: dict[str, dict[str, str]] = {}
    for node in get_adventure_store().list_nodes():
        for clue in getattr(node, "clues", []) or []:
            if not isinstance(clue, dict):
                continue
            clue_id = str(clue.get("id", "")).strip()
            if not clue_id or clue_id in catalog:
                continue
            catalog[clue_id] = {
                "node_id": node.id,
                "node_title": node.title,
                "label": str(clue.get("label", "")).strip(),
                "description": str(clue.get("description", "")).strip(),
            }
    return catalog


def _current_node_clue_ids(node_id: str) -> list[str]:
    if not node_id:
        return []
    try:
        node = get_adventure_store().get_node(node_id)
    except Exception:
        return []
    clue_ids: list[str] = []
    for clue in getattr(node, "clues", []) or []:
        if not isinstance(clue, dict):
            continue
        clue_id = str(clue.get("id", "")).strip()
        if clue_id:
            clue_ids.append(clue_id)
    return clue_ids


def _query_matched_clue_ids(query: str, known_clue_ids: list[str]) -> list[str]:
    text = query.strip().lower()
    if not text:
        return []

    catalog = _clue_catalog()
    matched: list[str] = []
    for clue_id in known_clue_ids:
        clue_meta = catalog.get(clue_id, {})
        haystacks = [
            clue_id.lower(),
            clue_meta.get("label", "").lower(),
            clue_meta.get("description", "").lower(),
            clue_meta.get("node_title", "").lower(),
        ]
        if any(text in haystack for haystack in haystacks if haystack):
            matched.append(clue_id)
    return matched
