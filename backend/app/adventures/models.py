"""冒险节点模型 — PDF 解析结果与运行时进度的稳定契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AdventureExit(BaseModel, extra="allow"):
    """节点出口 — 用条件把玩家选择映射到下一个剧情节点。"""
    id: str
    label: str
    next_node_id: str
    requires: list[str] = Field(default_factory=list)
    description: str = ""


class AdventureEncounter(BaseModel, extra="allow"):
    """遭遇声明 — 只引用怪物 slug，规则细节交给战斗系统。"""
    id: str
    monster_slug: str
    count: int = 1
    faction: str = "enemy"
    trigger: str = ""


class AdventureNode(BaseModel, extra="allow"):
    """剧情节点 — 每个节点必须能追溯到 PDF 页码。"""
    id: str
    module_id: str = "lost_mine"
    title: str
    kind: Literal["stage", "scene", "encounter", "location", "npc", "clue", "treasure"] = "scene"
    page_start: int
    page_end: int
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    parent_id: str | None = None
    source_excerpt: str = ""
    source_text: str = ""
    subsections: list[dict] = Field(default_factory=list)
    dm_summary: str = ""
    player_visible_intro: str = ""
    scene_beats: list[str] = Field(default_factory=list)
    npc_reveals: list[dict[str, Any]] = Field(default_factory=list)
    rules_notes: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    checks: list[dict] = Field(default_factory=list)
    encounters: list[AdventureEncounter] = Field(default_factory=list)
    rewards: list[dict] = Field(default_factory=list)
    clues: list[dict] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    fallbacks: list[dict[str, Any]] = Field(default_factory=list)
    routing_notes: list[str] = Field(default_factory=list)
    dm_guidance: dict[str, list[str]] = Field(default_factory=dict)
    exits: list[AdventureExit] = Field(default_factory=list)
    candidate_exits: list[dict] = Field(default_factory=list)


class AdventureState(BaseModel, extra="allow"):
    """当前冒险进度 — 控制 PDF 节点按阶段解锁。"""
    module_id: str = "lost_mine"
    active_node_id: str = "adventure_hook_meet_me_in_phandalin"
    unlocked_node_ids: list[str] = Field(default_factory=lambda: ["adventure_hook_meet_me_in_phandalin"])
    completed_node_ids: list[str] = Field(default_factory=list)
    known_clue_ids: list[str] = Field(default_factory=list)
    completed_event_ids: list[str] = Field(default_factory=list)
    claimed_reward_ids: list[str] = Field(default_factory=list)
    pending_reward_grants: list[dict[str, Any]] = Field(default_factory=list)
    pending_exit_option_ids: list[str] = Field(default_factory=list)
    breadcrumb_node_ids: list[str] = Field(default_factory=list)
    deferred_node_ids: list[str] = Field(default_factory=list)
    transition_log: list[dict[str, Any]] = Field(default_factory=list)
