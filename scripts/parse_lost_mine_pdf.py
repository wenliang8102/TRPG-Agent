"""从《凡戴尔的失落矿坑》PDF 生成第一版冒险节点 JSON。

脚本只做确定性版面切分，不让 LLM 改写剧情事实。后续可以在本输出基础上
增加结构化抽取步骤，把遭遇、线索、出口补得更细。
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import fitz


DEFAULT_TITLES = [
    "冒险引子",
    "第1 部分：地精箭头",
    "地精伏击",
    "克拉摩窝点",
    "第2 部分：凡达林",
    "凡达林的遭遇",
    "重要非玩家角色",
    "小镇详述",
    "红标帮恶霸",
    "红标帮窝点",
    "第3 部分：蜘蛛之网",
    "三猪小径",
    "兔莓与阿加莎的巢穴",
    "古枭井",
    "雷树废墟",
    "飞龙岩",
    "克拉摩堡",
    "第4 部分：潮音洞穴",
]

SUBSECTION_TITLES = [
    "发展",
    "休息",
    "奖励经验值",
    "奖励经验",
    "地精踪迹",
    "驾驶货车",
    "对抗",
    "宝藏",
    "套圈陷阱",
    "陷坑",
    "通用特征物",
    "游荡怪物",
    "关键遭遇",
    "角色等级",
    "经验奖励",
    "结局",
]


@dataclass(slots=True)
class Anchor:
    title: str
    page_index: int
    line_index: int


@dataclass(slots=True)
class SpanLine:
    page_index: int
    line_index: int
    text: str


def normalize_title(text: str) -> str:
    """中英混排标题只取中文主标题，稳定生成节点 ID。"""
    compact = re.sub(r"\s+", " ", text).strip()
    compact = re.split(r"[A-Z][A-Za-z’' -]{2,}$", compact)[0].strip()
    return compact or text.strip()


def slugify(value: str) -> str:
    """为中文标题生成可读但稳定的轻量 ID。"""
    mapping = {
        "冒险引子": "adventure_hook",
        "第1 部分：地精箭头": "goblin_arrows",
        "地精伏击": "goblin_ambush",
        "克拉摩窝点": "cragmaw_hideout",
        "第2 部分：凡达林": "phandalin",
        "凡达林的遭遇": "phandalin_encounters",
        "重要非玩家角色": "important_npcs",
        "小镇详述": "town_description",
        "红标帮恶霸": "redbrand_ruffians",
        "红标帮窝点": "redbrand_hideout",
        "第3 部分：蜘蛛之网": "spiders_web",
        "三猪小径": "triboar_trail",
        "兔莓与阿加莎的巢穴": "conyberry_agathas_lair",
        "古枭井": "old_owl_well",
        "雷树废墟": "ruins_of_thundertree",
        "飞龙岩": "wyvern_tor",
        "克拉摩堡": "cragmaw_castle",
        "第4 部分：潮音洞穴": "wave_echo_cave",
        "角色等级": "character_level",
        "经验奖励": "xp_awards",
    }
    return mapping.get(value, re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "node")


def slugify_any(value: str) -> str:
    """给小节标题生成稳定 ID，中文保留、其他字符压成下划线。"""
    lowered = value.strip().lower()
    lowered = re.sub(r"[^\w\u4e00-\u9fff]+", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    return lowered[:60] or "section"


def page_lines(doc: fitz.Document) -> list[list[str]]:
    """按页提取非空文本行。"""
    pages: list[list[str]] = []
    for page in doc:
        lines = [line.strip() for line in page.get_text("text").splitlines() if line.strip()]
        pages.append(lines)
    return pages


def find_anchors(pages: list[list[str]], titles: list[str]) -> list[Anchor]:
    """用目录标题在正文中定位节点起点。"""
    anchors: list[Anchor] = []
    seen_titles: set[str] = set()
    for page_index, lines in enumerate(pages):
        if page_index == 0:
            continue
        for line_index, line in enumerate(lines):
            normalized = normalize_title(line)
            for title in titles:
                is_exact_title = normalized == title or normalized.startswith(f"{title} ")
                is_part_title = title.startswith("第") and title in normalized and len(normalized) <= len(title) + 24
                if title not in seen_titles and (is_exact_title or is_part_title):
                    anchors.append(Anchor(title=title, page_index=page_index, line_index=line_index))
                    seen_titles.add(title)
                    break

    anchors.sort(key=lambda item: (item.page_index, item.line_index))
    return anchors


def span_text(pages: list[list[str]], start: Anchor, end: Anchor | None) -> str:
    """截取两个标题锚点之间的正文。"""
    collected: list[str] = []
    end_page = end.page_index if end else len(pages) - 1
    for page_index in range(start.page_index, end_page + 1):
        lines = pages[page_index]
        line_start = start.line_index if page_index == start.page_index else 0
        line_end = end.line_index if end and page_index == end.page_index else len(lines)
        collected.extend(lines[line_start:line_end])
    return "\n".join(collected).strip()


def span_lines(pages: list[list[str]], start: Anchor, end: Anchor | None) -> list[SpanLine]:
    """截取两个标题锚点之间的带页码文本行。"""
    collected: list[SpanLine] = []
    end_page = end.page_index if end else len(pages) - 1
    for page_index in range(start.page_index, end_page + 1):
        lines = pages[page_index]
        line_start = start.line_index if page_index == start.page_index else 0
        line_end = end.line_index if end and page_index == end.page_index else len(lines)
        for line_index in range(line_start, line_end):
            collected.append(SpanLine(page_index=page_index, line_index=line_index, text=lines[line_index]))
    return collected


def text_from_span_lines(lines: list[SpanLine]) -> str:
    """把带页码行还原成原文片段。"""
    return "\n".join(line.text for line in lines).strip()


def build_nodes(pdf_path: Path) -> list[dict]:
    """生成可被 AdventureStore 读取的节点列表。"""
    with fitz.open(pdf_path) as doc:
        pages = page_lines(doc)
    anchors = find_anchors(pages, DEFAULT_TITLES)

    nodes: list[dict] = []
    for index, anchor in enumerate(anchors):
        next_anchor = anchors[index + 1] if index + 1 < len(anchors) else None
        parent_lines = span_lines(pages, anchor, next_anchor)
        content = text_from_span_lines(parent_lines)
        if len(content) < 80:
            continue

        title = anchor.title
        node_id = slugify(title)
        kind = "stage" if title.startswith("第") else "scene"
        parent_node = make_node(
            node_id=node_id,
            title=title,
            kind=kind,
            lines=parent_lines,
            parent_id=None,
        )
        nodes.append(parent_node)
        nodes.extend(make_child_nodes(parent_node, parent_lines))
    return nodes


def extract_subsections(content: str) -> list[dict]:
    """提取片段内的常见 DM 指导小节，防止发展/奖励等信息被大段摘要吞没。"""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    anchors: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        normalized = normalize_title(line)
        for title in SUBSECTION_TITLES:
            if normalized == title or normalized.startswith(f"{title} "):
                anchors.append((title, index))
                break

    subsections: list[dict] = []
    for position, (title, start_index) in enumerate(anchors):
        end_index = anchors[position + 1][1] if position + 1 < len(anchors) else min(len(lines), start_index + 36)
        text = "\n".join(lines[start_index:end_index]).strip()
        if len(text) >= 16:
            subsections.append({"title": title, "text": text})
    return subsections


def make_node(
    *,
    node_id: str,
    title: str,
    kind: str,
    lines: list[SpanLine],
    parent_id: str | None,
) -> dict:
    """统一生成节点 dict，确保原文完整保留给 LLM。"""
    content = text_from_span_lines(lines)
    source_excerpt = " ".join(content.split())[:240]
    return {
        "id": node_id,
        "module_id": "lost_mine",
        "title": title,
        "kind": kind,
        "page_start": lines[0].page_index + 1,
        "page_end": lines[-1].page_index + 1,
        "parent_id": parent_id,
        "source_excerpt": source_excerpt,
        "source_text": "\n".join(content.splitlines()),
        "dm_summary": " ".join(content.split())[:900],
        "subsections": extract_subsections(content),
        "player_visible_intro": "",
        "secrets": [],
        "checks": [],
        "encounters": [],
        "rewards": [],
        "clues": [],
        "events": [],
        "exits": [],
    }


def make_child_nodes(parent_node: dict, lines: list[SpanLine]) -> list[dict]:
    """从章节内部拆出编号区域和重要 DM 指导小节。"""
    anchors = child_anchors(lines)
    children: list[dict] = []
    id_counts: dict[str, int] = {}
    for index, (title, start_index, kind) in enumerate(anchors):
        end_index = anchors[index + 1][1] if index + 1 < len(anchors) else len(lines)
        child_lines = lines[start_index:end_index]
        content = text_from_span_lines(child_lines)
        if len(content) < 80 and kind not in {"clue", "treasure", "scene"}:
            continue
        base_child_id = f"{parent_node['id']}__{slugify_any(title)}"
        id_counts[base_child_id] = id_counts.get(base_child_id, 0) + 1
        child_id = base_child_id if id_counts[base_child_id] == 1 else f"{base_child_id}_{id_counts[base_child_id]}"
        children.append(
            make_node(
                node_id=child_id,
                title=f"{parent_node['title']} / {title}",
                kind=kind,
                lines=child_lines,
                parent_id=parent_node["id"],
            )
        )
    return children


def child_anchors(lines: list[SpanLine]) -> list[tuple[str, int, str]]:
    """识别父节点内部的小节起点。"""
    anchors: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int]] = set()
    for index, line in enumerate(lines[1:], start=1):
        title, kind = child_title_kind(line.text)
        if not title:
            continue
        key = (title, line.page_index)
        if key in seen:
            continue
        seen.add(key)
        anchors.append((title, index, kind))
    return anchors


def child_title_kind(text: str) -> tuple[str, str]:
    """返回内部标题与建议节点类型。"""
    normalized = normalize_title(text)
    numbered = re.match(r"^(\d{1,2})\.\s*(.+)$", normalized)
    if numbered:
        return normalized, "location"

    for title in SUBSECTION_TITLES:
        if normalized == title or normalized.startswith(f"{title} "):
            if title in {"奖励经验值", "奖励经验"}:
                return title, "treasure"
            if title in {"地精踪迹"}:
                return title, "clue"
            return title, "scene"
    return "", ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("backend/data/adventures/lost_mine/nodes.parsed.preview.json"),
    )
    args = parser.parse_args()

    nodes = build_nodes(args.pdf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(nodes)} nodes to {args.out}")


if __name__ == "__main__":
    main()
