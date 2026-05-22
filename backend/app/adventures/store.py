"""冒险节点读取 — 运行时只使用正式节点文件，旧 ID 通过别名兼容。"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.adventures.models import AdventureNode

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ADVENTURE_DIR = BACKEND_DIR / "data" / "adventures" / "lost_mine"
CANONICAL_NODES_FILENAME = "nodes.json"
NODE_GENERATION_REFERENCE_FILENAME = "node_generation_reference.json"
_NODE_ID_ALIASES: dict[str, str] = {
    "lost_mine_start": "adventure_hook_meet_me_in_phandalin",
    "goblin_arrows": "goblin_ambush",
    "driving_the_wagon": "goblin_ambush",
    "phandalin_arrival": "phandalin",
    "goblin_arrows_road_to_phandalin": "goblin_ambush",
    "goblin_arrows_driving_the_wagon": "goblin_ambush",
    "goblin_trail": "goblin_trail_to_cragmaw_hideout",
    "cragmaw_hideout": "cragmaw_hideout_entrance",
    "cragmaw_hideout__6_goblin_den": "cragmaw_hideout_goblin_den",
    "cragmaw_hideout_area6": "cragmaw_hideout_goblin_den",
    "cragmaw_hideout__8_klargs_cave": "cragmaw_hideout_klarg_cave",
    "klargs_cave": "cragmaw_hideout_klarg_cave",
    "cragmaw_hideout_area8": "cragmaw_hideout_klarg_cave",
    "cragmaw_hideout_area5": "cragmaw_hideout_overpass",
    "cragmaw_hideout_area7": "cragmaw_hideout_twin_pools",
    "cragmaw_hideout_area_8": "cragmaw_hideout_klarg_cave",
    "cragmaw_hideout_area_8_klargs_cave": "cragmaw_hideout_klarg_cave",
    "cragmaw_hideout_kennel_or_steep_passage": "cragmaw_hideout_entrance",
    "cragmaw_hideout_steep_passage_continues": "cragmaw_hideout_steep_passage",
    "cragmaw_hideout_western_branch": "cragmaw_hideout_goblin_den",
    "cragmaw_hideout_inner_caves": "cragmaw_hideout_overpass",
    "redbrand_ruffians": "sleeping_giant_redbrand_ruffians",
    "redbrand_hideout": "tresendar_manor_approach",
    "redbrand_hideout_entrance": "tresendar_manor_approach",
    "spiders_web": "spider_web_overview",
    "spider_web": "spider_web_overview",
    "cragmaw_castle": "cragmaw_castle_search",
    "wave_echo_cave": "wave_echo_overview",
}
ALIAS_GROUPS: list[tuple[str, list[str]]] = [
    ("phandalin", ["phandalin", "凡达林", "凡戴尔"]),
    ("gundren", ["gundren", "gundren rockseeker", "冈德伦", "甘德伦", "冈德伦·洛克希尔"]),
    ("cragmaw", ["cragmaw", "克拉摩", "克拉格玛"]),
    ("triboar trail", ["triboar trail", "三猪小径", "三野猪小径"]),
    ("neverwinter", ["neverwinter", "无冬城"]),
]


class AdventureStore:
    """按 ID 读取冒险节点，避免工具层关心文件格式。"""

    def __init__(self, data_dir: Path = DEFAULT_ADVENTURE_DIR) -> None:
        self._data_dir = data_dir
        canonical_filename = os.getenv("ADVENTURE_NODES_FILENAME", CANONICAL_NODES_FILENAME)
        self._nodes = self._load_nodes_file(canonical_filename)

    def get_node(self, node_id: str) -> AdventureNode:
        resolved_id = self.resolve_node_id(node_id)
        if resolved_id in self._nodes:
            return self._nodes[resolved_id]
        raise KeyError(f"未知冒险节点: {node_id}")

    def list_nodes(self) -> list[AdventureNode]:
        return list(self._nodes.values())

    def search_nodes(self, query: str, limit: int = 5) -> list[tuple[AdventureNode, float]]:
        """按玩家意图或地点/NPC/线索检索候选剧情节点。"""
        query = query.strip()
        if not query:
            return []

        tokens = _query_tokens(query)
        scored: list[tuple[AdventureNode, float]] = []
        for node in self.list_nodes():
            score = _score_node(node, query, tokens)
            if score > 0:
                scored.append((node, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def resolve_node_id(self, node_id: str) -> str:
        """把别名、占位出口或旧节点名收敛到实际存在的节点 ID。"""
        if alias := _NODE_ID_ALIASES.get(node_id):
            if alias in self._nodes:
                return alias

        normalized = _normalize_node_id(node_id)
        if alias := _NODE_ID_ALIASES.get(normalized):
            if alias in self._nodes:
                return alias

        if node_id in self._nodes:
            return node_id
        if normalized in self._nodes:
            return normalized

        suffix_matches = [item_id for item_id in self._nodes if item_id.endswith(f"_{normalized}") or item_id.endswith(normalized)]
        if len(suffix_matches) == 1:
            return suffix_matches[0]

        prefix_matches = [item_id for item_id in self._nodes if item_id.startswith(f"{normalized}_")]
        if len(prefix_matches) == 1:
            return prefix_matches[0]

        return node_id

    def _load_nodes_file(self, filename: str) -> dict[str, AdventureNode]:
        nodes_path = self._data_dir / filename
        if not nodes_path.exists():
            raise FileNotFoundError(f"冒险节点文件不存在: {nodes_path}")

        with nodes_path.open("r", encoding="utf-8") as file:
            raw_nodes = json.load(file)

        nodes: dict[str, AdventureNode] = {}
        for item in raw_nodes:
            normalized = _normalize_raw_node(item)
            node = AdventureNode.model_validate(normalized)
            nodes[node.id] = node
        return nodes


def _query_tokens(query: str) -> list[str]:
    """抽取轻量检索词；中文短语保留整段并补 2 字 gram。"""
    lowered = query.lower()
    tokens = re.findall(r"[a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", lowered)
    grams: list[str] = []
    for token in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", token):
            grams.extend(token[index:index + 2] for index in range(len(token) - 1))
    dedup: list[str] = []
    for token in [lowered, *tokens, *grams]:
        token = token.strip()
        if token and token not in dedup:
            dedup.append(token)
    for canonical, aliases in ALIAS_GROUPS:
        if any(alias in lowered for alias in aliases):
            for alias in [canonical, *aliases]:
                if alias not in dedup:
                    dedup.append(alias)
    return dedup


def _normalize_node_id(node_id: str) -> str:
    text = node_id.strip().lower()
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def _normalize_raw_node(item: dict[str, Any]) -> dict[str, Any]:
    raw = dict(item)
    raw["events"] = _normalize_events(raw.get("events", []))
    raw["scene_beats"] = _normalize_text_list(raw.get("scene_beats", []))
    raw["rules_notes"] = _normalize_text_list(raw.get("rules_notes", []))
    raw["fallbacks"] = _normalize_dict_list(raw.get("fallbacks", []))
    raw["npc_reveals"] = _normalize_dict_list(raw.get("npc_reveals", []))
    raw["source_refs"] = _normalize_dict_list(raw.get("source_refs", []))
    if "page_start" not in raw or "page_end" not in raw:
        page_start, page_end = _page_range_from_source_refs(raw.get("source_refs", []))
        if page_start is not None:
            raw["page_start"] = page_start
        if page_end is not None:
            raw["page_end"] = page_end
    return raw


def _normalize_events(value: Any) -> list[str]:
    events: list[str] = []
    if not isinstance(value, list):
        return events
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                events.append(text)
            continue
        if isinstance(item, dict):
            event_id = str(item.get("id", "")).strip()
            if event_id:
                events.append(event_id)
    return events


def _normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _normalize_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(dict(item))
    return result


def _page_range_from_source_refs(source_refs: Any) -> tuple[int | None, int | None]:
    if not isinstance(source_refs, list) or not source_refs:
        return None, None

    page_starts: list[int] = []
    page_ends: list[int] = []
    for ref in source_refs:
        if not isinstance(ref, dict):
            continue
        try:
            page_starts.append(int(ref.get("page_start")))
            page_ends.append(int(ref.get("page_end")))
        except (TypeError, ValueError):
            continue
    if not page_starts or not page_ends:
        return None, None
    return min(page_starts), max(page_ends)


def _score_node(node: AdventureNode, query: str, tokens: list[str]) -> float:
    """简单可解释的本地检索评分，避免为了小语料引入新索引。"""
    title = node.title.lower()
    node_id = node.id.lower()
    body = " ".join(
        [
            node.source_excerpt,
            node.source_text,
            node.dm_summary,
            node.player_visible_intro,
            " ".join(getattr(node, "scene_beats", [])),
            " ".join(getattr(node, "rules_notes", [])),
            " ".join(str(item) for item in node.clues),
            " ".join(str(item) for item in node.encounters),
            " ".join(str(item) for item in node.rewards),
            " ".join(str(item) for item in node.subsections),
            " ".join(str(item) for item in getattr(node, "fallbacks", [])),
            " ".join(str(item) for values in node.dm_guidance.values() for item in values),
            " ".join(f"{item.id} {item.label} {item.next_node_id} {item.description}" for item in node.exits),
        ]
    ).lower()

    score = 0.0
    query_lower = query.lower().strip()
    if query_lower and query_lower in title:
        score += 8
    if query_lower and query_lower in node_id:
        score += 6
    if query_lower and query_lower in body:
        score += 3

    for token in tokens:
        if token in title:
            score += 4
        if token in node_id:
            score += 3
        if token in body:
            score += 1
    return score


@lru_cache(maxsize=1)
def get_node_generation_reference() -> dict[str, Any]:
    """读取节点生成契约，运行时只使用其中稳定的路由元数据。"""
    reference_path = DEFAULT_ADVENTURE_DIR / NODE_GENERATION_REFERENCE_FILENAME
    if not reference_path.exists():
        return {}
    with reference_path.open("r", encoding="utf-8") as file:
        return json.load(file)


@lru_cache(maxsize=1)
def get_adventure_store() -> AdventureStore:
    """缓存冒险数据，避免每轮工具调用重复读 JSON。"""
    return AdventureStore()
