"""展开查看 LLM 抽取出的候选冒险节点。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _print_block(title: str, value: object) -> None:
    print(f"\n[{title}]")
    if isinstance(value, str):
        print(value or "（空）")
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def inspect(path: Path, *, limit: int | None) -> None:
    nodes = json.loads(path.read_text(encoding="utf-8"))
    if limit is not None:
        nodes = nodes[:limit]

    print(f"candidate_file={path}")
    print(f"node_count={len(nodes)}")
    for index, node in enumerate(nodes, start=1):
        print("\n" + "=" * 80)
        print(f"{index}. {node.get('title')} [{node.get('id')}] pages={node.get('page_start')}-{node.get('page_end')}")
        _print_block("source_excerpt", node.get("source_excerpt", ""))
        _print_block("player_visible_intro", node.get("player_visible_intro", ""))
        _print_block("dm_summary", node.get("dm_summary", ""))
        _print_block("secrets", node.get("secrets", []))
        _print_block("checks", node.get("checks", []))
        _print_block("encounters", node.get("encounters", []))
        _print_block("clues", node.get("clues", []))
        _print_block("events", node.get("events", []))
        _print_block("dm_guidance", node.get("dm_guidance", {}))
        _print_block("candidate_exits", node.get("candidate_exits", []))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("backend/data/adventures/lost_mine/nodes.candidate.json"),
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    inspect(args.path, limit=args.limit)


if __name__ == "__main__":
    main()
