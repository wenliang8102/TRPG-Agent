"""候选冒险节点质量概览。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


DM_ONLY_TITLE_MARKERS = (
    " / 发展",
    " / 休息",
    " / 对抗",
    " / 通用特征物",
    " / 游荡怪物",
    " / 经验奖励",
    " / 1.困惑射线",
    " / 2.麻痹射线",
    " / 3.恐惧射线",
    " / 4.致伤射线",
)


def _expects_player_intro(node: dict) -> bool:
    """纯主持提示节点不强求玩家开场，避免 QA 噪音淹没真实缺陷。"""
    if node.get("kind") in {"treasure", "clue"}:
        return False
    title = str(node.get("title", ""))
    return not any(marker in title for marker in DM_ONLY_TITLE_MARKERS)


def qa(path: Path) -> dict:
    nodes = json.loads(path.read_text(encoding="utf-8"))
    kind_counts = Counter(node.get("kind", "unknown") for node in nodes)
    encounter_count = sum(len(node.get("encounters", [])) for node in nodes)
    check_count = sum(len(node.get("checks", [])) for node in nodes)
    clue_count = sum(len(node.get("clues", [])) for node in nodes)
    exit_count = sum(len(node.get("candidate_exits", [])) for node in nodes)
    risks: list[dict] = []

    for node in nodes:
        node_risks: list[str] = []
        if _expects_player_intro(node) and not node.get("player_visible_intro"):
            node_risks.append("缺少玩家可见开场")
        if not node.get("dm_summary"):
            node_risks.append("缺少 DM 摘要")
        if not node.get("source_excerpt"):
            node_risks.append("缺少来源摘录")
        visible = str(node.get("player_visible_intro", ""))
        for secret in node.get("secrets", []):
            if secret and str(secret) in visible:
                node_risks.append("秘密疑似泄露到玩家可见文本")
                break
        if node_risks:
            risks.append(
                {
                    "id": node.get("id"),
                    "title": node.get("title"),
                    "risks": node_risks,
                }
            )

    return {
        "file": str(path),
        "node_count": len(nodes),
        "kind_counts": dict(kind_counts),
        "encounter_count": encounter_count,
        "check_count": check_count,
        "clue_count": clue_count,
        "candidate_exit_count": exit_count,
        "risk_count": len(risks),
        "risks": risks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("backend/data/adventures/lost_mine/nodes.candidate.json"),
    )
    args = parser.parse_args()
    print(json.dumps(qa(args.path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
