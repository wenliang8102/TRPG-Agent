"""检查冒险节点是否保留了关键主持信息。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.adventures.models import AdventureNode  # noqa: E402


DEFAULT_KEYWORDS = [
    "30 尺",
    "最后一只",
    "尝试逃跑",
    "发展",
    "洗劫一空",
    "巴森补给",
    "休息",
    "75 XP",
    "DC 10",
    "克拉摩窝点",
]


def _node_text(node: dict[str, Any]) -> str:
    """把 LLM 运行时能看到的字段合并，用来判断关键事实是否仍可检索。"""
    return "\n".join(
        [
            str(node.get("title", "")),
            str(node.get("source_excerpt", "")),
            str(node.get("source_text", "")),
            str(node.get("dm_summary", "")),
            str(node.get("player_visible_intro", "")),
            json.dumps(node.get("subsections", []), ensure_ascii=False),
            json.dumps(node.get("secrets", []), ensure_ascii=False),
            json.dumps(node.get("checks", []), ensure_ascii=False),
            json.dumps(node.get("clues", []), ensure_ascii=False),
            json.dumps(node.get("rewards", []), ensure_ascii=False),
            json.dumps(node.get("dm_guidance", {}), ensure_ascii=False),
            json.dumps(node.get("candidate_exits", []), ensure_ascii=False),
        ]
    )


def _match_text(value: str) -> str:
    """验收关注事实是否存在，忽略 PDF 中常见的中英/数字空格差异。"""
    return "".join(value.split())


def _load_nodes(path: Path) -> list[dict[str, Any]]:
    """用 AdventureNode 校验契约，避免 QA 误读坏 JSON。"""
    raw_nodes = json.loads(path.read_text(encoding="utf-8"))
    return [AdventureNode.model_validate(item).model_dump() for item in raw_nodes]


def qa_coverage(path: Path, *, node_id: str, keywords: list[str]) -> dict[str, Any]:
    """检查目标节点及其子节点是否覆盖关键字。"""
    nodes = _load_nodes(path)
    scoped_nodes = [
        node for node in nodes
        if node["id"] == node_id or node.get("parent_id") == node_id or node["id"].startswith(f"{node_id}__")
    ]
    if not scoped_nodes:
        raise ValueError(f"找不到节点或子节点: {node_id}")

    combined = "\n".join(_node_text(node) for node in scoped_nodes)
    normalized_combined = _match_text(combined)
    missing = [keyword for keyword in keywords if _match_text(keyword) not in normalized_combined]
    return {
        "file": str(path),
        "node_id": node_id,
        "scoped_node_count": len(scoped_nodes),
        "scoped_nodes": [
            {
                "id": node["id"],
                "title": node["title"],
                "kind": node["kind"],
                "source_pages": [node["page_start"], node["page_end"]],
                "dm_guidance_keys": [key for key, values in node.get("dm_guidance", {}).items() if values],
                "subsection_titles": [item.get("title", "") for item in node.get("subsections", [])],
            }
            for node in scoped_nodes
        ],
        "keywords": keywords,
        "missing": missing,
        "ok": not missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--node-id", default="goblin_ambush")
    parser.add_argument("--keyword", action="append", default=[], help="额外关键字；不传则使用地精伏击验收集。")
    args = parser.parse_args()

    keywords = args.keyword or DEFAULT_KEYWORDS
    result = qa_coverage(args.path, node_id=args.node_id, keywords=keywords)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
