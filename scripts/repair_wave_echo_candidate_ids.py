"""修复潮音洞穴候选节点的父级归属。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _rewrite_node(node: dict[str, Any]) -> dict[str, Any]:
    """把误挂到经验奖励下的潮音洞穴节点迁回第4部分。"""
    if node["id"] == "xp_awards":
        node["id"] = "wave_echo_cave__经验奖励"
        node["title"] = "第4 部分：潮音洞穴 / 经验奖励"
        node["parent_id"] = "wave_echo_cave"
        return node

    if str(node["id"]).startswith("xp_awards__"):
        node["id"] = "wave_echo_cave__" + str(node["id"])[len("xp_awards__"):]
        node["title"] = str(node["title"]).replace("经验奖励 / ", "第4 部分：潮音洞穴 / ", 1)
        node["parent_id"] = "wave_echo_cave"
    return node


def repair_file(path: Path) -> int:
    """就地修复候选 JSON，返回被改写的节点数。"""
    nodes = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    repaired_nodes = []
    for node in nodes:
        before = (node.get("id"), node.get("title"), node.get("parent_id"))
        repaired = _rewrite_node(node)
        after = (repaired.get("id"), repaired.get("title"), repaired.get("parent_id"))
        if before != after:
            changed += 1
        repaired_nodes.append(repaired)

    path.write_text(json.dumps(repaired_nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def repair_report(path: Path) -> int:
    """同步修复报告中的节点 ID 与标题，避免 QA 对不上。"""
    report = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for item in report.get("items", []):
        before = (item.get("id"), item.get("title"))
        if item.get("id") == "xp_awards":
            item["id"] = "wave_echo_cave__经验奖励"
            item["title"] = "第4 部分：潮音洞穴 / 经验奖励"
        elif str(item.get("id", "")).startswith("xp_awards__"):
            item["id"] = "wave_echo_cave__" + str(item["id"])[len("xp_awards__"):]
            item["title"] = str(item.get("title", "")).replace("经验奖励 / ", "第4 部分：潮音洞穴 / ", 1)
        after = (item.get("id"), item.get("title"))
        if before != after:
            changed += 1

    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--nodes",
        type=Path,
        default=Path("backend/data/adventures/lost_mine/nodes.candidate.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("backend/data/adventures/lost_mine/extraction_report.json"),
    )
    args = parser.parse_args()

    node_count = repair_file(args.nodes)
    report_count = repair_report(args.report)
    print(f"repaired nodes={node_count} report_items={report_count}")


if __name__ == "__main__":
    main()
