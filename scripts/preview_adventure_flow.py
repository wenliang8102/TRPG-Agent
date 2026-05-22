"""预览冒险节点连续推进过程。

用于人工检查 LLM 在每个阶段大概会看到什么、出口如何解锁、状态如何变化。
脚本不调用模型、不修改存档，只读取当前运行时的 Lost Mine 节点图。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.adventures.models import AdventureNode, AdventureState  # noqa: E402
from app.adventures.store import get_adventure_store  # noqa: E402


DEFAULT_OPTIONS = ["continue_to_ambush", "follow_goblin_trail"]


def _print_json(title: str, payload: object) -> None:
    print(f"\n[{title}]")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _available_exits(node: AdventureNode, adventure: AdventureState) -> list[dict]:
    """按当前状态标注节点出口是否可用。"""
    completed_events = set(adventure.completed_event_ids)
    known_clues = set(adventure.known_clue_ids)
    exits: list[dict] = []
    for exit_option in node.exits:
        missing = [
            item for item in exit_option.requires
            if item not in completed_events and item not in known_clues
        ]
        exits.append(
            {
                "id": exit_option.id,
                "label": exit_option.label,
                "next_node_id": exit_option.next_node_id,
                "requires": exit_option.requires,
                "available": not missing,
                "missing": missing,
                "description": exit_option.description,
            }
        )
    return exits


def _llm_visible_payload(node: AdventureNode, adventure: AdventureState) -> dict:
    """模拟 load_adventure_node 返回给 LLM 的核心内容。"""
    return {
        "node": {
            "id": node.id,
            "title": node.title,
            "kind": node.kind,
            "source_pages": [node.page_start, node.page_end],
            "source_excerpt": node.source_excerpt,
            "source_text": node.source_text,
            "subsections": node.subsections,
            "dm_summary": node.dm_summary,
            "player_visible_intro": node.player_visible_intro,
            "secrets": node.secrets,
            "checks": node.checks,
            "encounters": [item.model_dump() for item in node.encounters],
            "rewards": node.rewards,
            "clues": node.clues,
            "events": node.events,
            "dm_guidance": node.dm_guidance,
            "candidate_exits": node.candidate_exits,
        },
        "available_exits": _available_exits(node, adventure),
        "adventure_state": adventure.model_dump(),
    }


def _reveal_node_clues(node: AdventureNode, adventure: AdventureState) -> list[str]:
    """预览模式下自动解锁当前节点列出的线索，模拟玩家调查成功后的状态。"""
    revealed: list[str] = []
    for clue in node.clues:
        clue_id = str(clue.get("id", "")).strip()
        if clue_id and clue_id not in adventure.known_clue_ids:
            adventure.known_clue_ids.append(clue_id)
            revealed.append(clue_id)
    return revealed


def _mark_node_events(node: AdventureNode, adventure: AdventureState) -> list[str]:
    """预览模式下自动记录当前节点列出的事件，模拟节点已解决。"""
    marked: list[str] = []
    for event_id in node.events:
        if event_id not in adventure.completed_event_ids:
            adventure.completed_event_ids.append(event_id)
            marked.append(event_id)
    return marked


def _advance(option_id: str, adventure: AdventureState) -> tuple[AdventureState, dict]:
    """按节点出口推进，返回新状态和推进报告。"""
    store = get_adventure_store()
    current_node = store.get_node(adventure.active_node_id)
    exit_option = next((item for item in current_node.exits if item.id == option_id), None)
    if exit_option is None:
        raise ValueError(f"当前节点 {current_node.id} 没有出口 {option_id}")

    completed_events = set(adventure.completed_event_ids)
    known_clues = set(adventure.known_clue_ids)
    missing = [
        item for item in exit_option.requires
        if item not in completed_events and item not in known_clues
    ]
    if missing:
        raise ValueError(f"出口 {option_id} 条件未满足: {', '.join(missing)}")

    before = adventure.model_dump()
    if current_node.id not in adventure.completed_node_ids:
        adventure.completed_node_ids.append(current_node.id)
    adventure.active_node_id = exit_option.next_node_id
    if exit_option.next_node_id not in adventure.unlocked_node_ids:
        adventure.unlocked_node_ids.append(exit_option.next_node_id)

    return adventure, {
        "option_id": option_id,
        "from": current_node.id,
        "to": exit_option.next_node_id,
        "state_before": before,
        "state_after": adventure.model_dump(),
    }


def preview_flow(options: list[str], *, auto_resolve: bool) -> None:
    """打印连续节点内容与推进报告。"""
    store = get_adventure_store()
    adventure = AdventureState()

    print("Adventure flow preview")
    print(f"module={adventure.module_id}")
    print(f"planned_options={options}")
    print(f"auto_resolve={auto_resolve}")

    for index in range(len(options) + 1):
        node = store.get_node(adventure.active_node_id)
        print("\n" + "=" * 80)
        print(f"STEP {index + 1}: {node.title} [{node.id}]")
        _print_json("LLM 可见节点材料", _llm_visible_payload(node, adventure))

        if index >= len(options):
            break

        if auto_resolve:
            revealed = _reveal_node_clues(node, adventure)
            marked = _mark_node_events(node, adventure)
            if revealed or marked:
                _print_json("预览模式自动解锁", {"revealed_clues": revealed, "marked_events": marked})

        adventure, report = _advance(options[index], adventure)
        _print_json("推进报告", report)


def preview_search_switch(query: str, target_node_id: str | None) -> None:
    """演示轻量资料库模式：按玩家意图搜索节点，再切换当前书签。"""
    store = get_adventure_store()
    adventure = AdventureState()

    print("Adventure search/switch preview")
    print(f"query={query}")

    results = store.search_nodes(query, limit=5)
    _print_json(
        "搜索结果",
        [
            {
                "id": node.id,
                "title": node.title,
                "kind": node.kind,
                "score": score,
                "source_pages": [node.page_start, node.page_end],
                "source_excerpt": node.source_excerpt,
            }
            for node, score in results
        ],
    )
    if not results:
        return

    chosen_id = target_node_id or results[0][0].id
    node = store.get_node(chosen_id)
    before = adventure.model_dump()
    if adventure.active_node_id not in adventure.completed_node_ids:
        adventure.completed_node_ids.append(adventure.active_node_id)
    adventure.active_node_id = chosen_id
    if chosen_id not in adventure.unlocked_node_ids:
        adventure.unlocked_node_ids.append(chosen_id)

    _print_json("切换报告", {"state_before": before, "state_after": adventure.model_dump()})
    _print_json("切换后 LLM 可见节点材料", _llm_visible_payload(node, adventure))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--options",
        nargs="+",
        default=DEFAULT_OPTIONS,
        help="连续使用的出口 ID。默认展示 start -> ambush -> hideout。",
    )
    parser.add_argument(
        "--no-auto-resolve",
        action="store_true",
        help="不自动解锁当前节点线索/事件，用于检查条件阻塞。",
    )
    parser.add_argument("--search", default="", help="演示按玩家意图搜索节点，不走硬出口推进。")
    parser.add_argument("--switch-to", default=None, help="搜索模式下指定要切换到的节点 ID。")
    args = parser.parse_args()

    if args.search:
        preview_search_switch(args.search, args.switch_to)
        return

    preview_flow(args.options, auto_resolve=not args.no_auto_resolve)


if __name__ == "__main__":
    main()
