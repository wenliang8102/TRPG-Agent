"""用轻量 LLM 从 Lost Mine PDF 片段抽取候选冒险节点。

输出是候选文件，不直接覆盖运行时 nodes.json。人工确认后再晋升为正式节点图。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(ROOT_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "scripts"))

from app.adventures.models import AdventureNode  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.monsters.lost_mine import LOST_MINE_ACTIONS  # noqa: E402
from app.services.llm_service import LLMService  # noqa: E402
from parse_lost_mine_pdf import build_nodes  # noqa: E402


EXTRACTION_SYSTEM_PROMPT = """你负责把 D&D 冒险模组 PDF 片段抽取成可运行的 TRPG 冒险节点。

必须遵守：
1. 只能依据输入片段，不要补充片段外的信息。
2. 输出必须是单个 JSON 对象，不要 Markdown，不要解释。
3. 玩家可见信息与 DM 私密信息必须分开。
4. 不确定的字段留空数组或空字符串。
5. 不要把后续秘密写入 player_visible_intro。
6. 怪物请尽量使用 Open5e 英文 slug，例如 goblin、wolf、bugbear、redbrand-ruffian。
7. candidate_exits 只是候选出口，next_title 不确定时留空，不要编造。
8. 必须特别保留 DM 指导信息：怪物战术、发展 Developments、休息 Rests、奖励经验值、失败后果、里程碑、追踪/调查后续。

JSON schema:
{
  "kind": "stage|scene|encounter|location|npc|clue|treasure",
  "dm_summary": "供 DM 使用的简洁摘要",
  "player_visible_intro": "玩家进入该节点时可听到的开场描述",
  "secrets": ["仅 DM 可知的信息"],
  "checks": [{"ability": "wis", "skill": "perception", "dc": 10, "reason": "为什么检定"}],
  "encounters": [{"id": "短 id", "monster_slug": "goblin", "count": 4, "trigger": "触发条件"}],
  "clues": [{"id": "短 id", "label": "线索名", "description": "线索内容"}],
  "events": ["短事件 id"],
  "rewards": [{"type": "xp|gold|item|story", "description": "奖励"}],
  "dm_guidance": {
    "tactics": ["怪物或 NPC 的行动策略"],
    "developments": ["战斗或探索后的后续发展"],
    "rests": ["休息相关提示"],
    "xp": ["经验值或里程碑奖励"],
    "failure": ["玩家失败、被俘、绕路等后果"],
    "tracking": ["追踪、调查、线索路径"],
    "other": ["其他对主持有用的信息"]
  },
  "candidate_exits": [{"id": "短 id", "label": "玩家选择", "next_title": "目标标题或空", "requires": ["线索或事件 id"], "description": "何时可用"}]
}
"""


def _json_from_text(text: str) -> dict[str, Any]:
    """从模型回复中抽出 JSON 对象，兼容偶发代码围栏。"""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    if cleaned.startswith("{"):
        return json.loads(cleaned)

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("模型回复中没有 JSON 对象")
    return json.loads(match.group(0))


def _normalize_id(value: str, fallback: str) -> str:
    """把 LLM 生成的 id 收敛成工具友好的 snake-ish 标识。"""
    raw = (value or fallback).strip().lower()
    raw = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", raw)
    raw = raw.strip("_-")
    return raw or fallback


def _parse_count(value: object) -> int:
    """LLM 偶尔把数量填成伤害骰表达式；取第一个整数继续校验。"""
    if isinstance(value, int):
        return max(1, value)
    match = re.search(r"\d+", str(value or ""))
    if match:
        return max(1, int(match.group(0)))
    return 1


def _source_input(raw_node: dict[str, Any], *, max_chars: int) -> str:
    """给抽取模型的单片段输入，保留页码与原文摘要。"""
    source_text = str(raw_node.get("source_text") or raw_node["dm_summary"])
    if raw_node.get("subsections") and len(source_text) > 1200:
        first_subsection = str(raw_node["subsections"][0].get("text", "")).strip()
        source_text = source_text.split(first_subsection, 1)[0].strip() if first_subsection else source_text[:1200]
        subsection_index = [
            {
                "title": item.get("title", ""),
                "preview": " ".join(str(item.get("text", "")).split())[:260],
            }
            for item in raw_node.get("subsections", [])
        ]
    else:
        subsection_index = raw_node.get("subsections", [])
    if len(source_text) > max_chars:
        source_text = source_text[:max_chars] + "\n[TRUNCATED]"
    payload = {
        "node_id": raw_node["id"],
        "title": raw_node["title"],
        "page_start": raw_node["page_start"],
        "page_end": raw_node["page_end"],
        "source_excerpt": raw_node.get("source_excerpt", ""),
        "subsections": subsection_index,
        "source_text": source_text,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _candidate_to_node(raw_node: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    """把 LLM 抽取结果合并成 AdventureNode 候选。"""
    node_id = raw_node["id"]
    raw_kind = raw_node.get("kind") or "scene"
    node_kind = raw_kind if raw_kind in {"stage", "treasure", "clue"} else (extracted.get("kind") or raw_kind)
    encounters = []
    if node_kind != "stage":
        for index, encounter in enumerate(extracted.get("encounters") or [], start=1):
            if not isinstance(encounter, dict):
                continue
            monster_slug = str(encounter.get("monster_slug", "")).strip()
            if not monster_slug:
                continue
            encounters.append(
                {
                    "id": _normalize_id(str(encounter.get("id", "")), f"{node_id}_encounter_{index}"),
                    "monster_slug": monster_slug,
                    "count": _parse_count(encounter.get("count")),
                    "trigger": str(encounter.get("trigger", "")),
                }
            )

    clues = [
        item for item in extracted.get("clues", [])
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    ]

    node = {
        "id": node_id,
        "module_id": "lost_mine",
        "title": raw_node["title"],
        "kind": node_kind,
        "page_start": raw_node["page_start"],
        "page_end": raw_node["page_end"],
        "parent_id": raw_node.get("parent_id"),
        "source_excerpt": raw_node["source_excerpt"],
        "source_text": raw_node.get("source_text", ""),
        "subsections": raw_node.get("subsections", []),
        "dm_summary": extracted.get("dm_summary") or raw_node["dm_summary"],
        "player_visible_intro": extracted.get("player_visible_intro") or "",
        "secrets": extracted.get("secrets") or [],
        "checks": extracted.get("checks") or [],
        "encounters": encounters,
        "rewards": extracted.get("rewards") or [],
        "clues": clues,
        "events": extracted.get("events") or [],
        "dm_guidance": extracted.get("dm_guidance") or {},
        "exits": [],
        "candidate_exits": extracted.get("candidate_exits") or [],
    }
    return AdventureNode.model_validate(node).model_dump()


def _validate_candidate(node: dict[str, Any]) -> list[str]:
    """对候选节点做轻量校验，问题写入报告而非中断整批。"""
    warnings: list[str] = []
    if not node.get("source_excerpt"):
        warnings.append("缺少 source_excerpt")
    if not node.get("dm_summary"):
        warnings.append("缺少 dm_summary")

    visible = str(node.get("player_visible_intro", ""))
    for secret in node.get("secrets", []):
        if secret and str(secret) in visible:
            warnings.append("DM 私密信息疑似泄露到 player_visible_intro")
            break

    known_local_slugs = set(LOST_MINE_ACTIONS)
    for encounter in node.get("encounters", []):
        slug = encounter.get("monster_slug", "")
        if slug and slug not in known_local_slugs:
            warnings.append(f"怪物 slug 未在本地 Lost Mine 动作表中确认: {slug}")
    return warnings


def extract_nodes(
    pdf_path: Path,
    *,
    limit: int | None,
    max_chars: int,
    only_id: str | None = None,
    existing_candidates: list[dict[str, Any]] | None = None,
    existing_report_items: list[dict[str, Any]] | None = None,
    stop_after_failures: int = 3,
    sleep_seconds: float = 0.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """执行切片与 LLM 抽取。"""
    raw_nodes = build_nodes(pdf_path)
    if only_id:
        raw_nodes = [node for node in raw_nodes if node["id"] == only_id]
    if limit is not None:
        raw_nodes = raw_nodes[:limit]

    service = LLMService()
    candidates: list[dict[str, Any]] = list(existing_candidates or [])
    report_items: list[dict[str, Any]] = list(existing_report_items or [])
    completed_ids = {node["id"] for node in candidates}
    consecutive_failures = 0

    for index, raw_node in enumerate(raw_nodes, start=1):
        if raw_node["id"] in completed_ids:
            print(f"[{index}/{len(raw_nodes)}] skip {raw_node['id']} already extracted")
            continue

        summary_input = _source_input(raw_node, max_chars=max_chars)
        try:
            response_text = service.invoke_summary(summary_input, system_prompt=EXTRACTION_SYSTEM_PROMPT)
            extracted = _json_from_text(response_text)
            candidate = _candidate_to_node(raw_node, extracted)
            warnings = _validate_candidate(candidate)
            candidates.append(candidate)
            completed_ids.add(candidate["id"])
            consecutive_failures = 0
            report_items.append(
                {
                    "id": candidate["id"],
                    "title": candidate["title"],
                    "status": "ok",
                    "warnings": warnings,
                    "source_pages": [candidate["page_start"], candidate["page_end"]],
                }
            )
            print(f"[{index}/{len(raw_nodes)}] ok {candidate['id']} warnings={len(warnings)}")
        except Exception as exc:
            consecutive_failures += 1
            report_items.append(
                {
                    "id": raw_node.get("id"),
                    "title": raw_node.get("title"),
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "source_pages": [raw_node.get("page_start"), raw_node.get("page_end")],
                }
            )
            print(f"[{index}/{len(raw_nodes)}] failed {raw_node.get('id')}: {type(exc).__name__}: {exc}")
            if consecutive_failures >= stop_after_failures:
                raise RuntimeError(f"连续失败 {consecutive_failures} 次，停止批量抽取以便检查配置或模型超时") from exc
        if sleep_seconds:
            time.sleep(sleep_seconds)

    report = {
        "pdf": str(pdf_path),
        "model": settings.memory_summary_model or settings.llm_model,
        "max_chars_per_node": max_chars,
        "requested_count": len(raw_nodes),
        "candidate_count": len(candidates),
        "failed_count": sum(1 for item in report_items if item["status"] == "failed"),
        "items": report_items,
    }
    return candidates, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--limit", type=int, default=None, help="只抽取前 N 个 PDF 切片，便于试跑。")
    parser.add_argument("--only-id", default=None, help="只抽取指定节点 ID，便于重试失败节点。")
    parser.add_argument("--max-chars", type=int, default=18000, help="每个 PDF 切片最多发送给 LLM 的字符数。")
    parser.add_argument("--model", default=None, help="覆盖抽取模型；默认使用 TRPG_MEMORY_SUMMARY_MODEL。")
    parser.add_argument("--timeout", type=float, default=None, help="覆盖单次抽取请求超时时间。")
    parser.add_argument("--resume", action="store_true", help="读取现有候选文件并跳过已成功抽取的节点。")
    parser.add_argument("--stop-after-failures", type=int, default=3, help="连续失败多少次后停止，避免整批空跑。")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="每次请求后暂停，便于规避速率限制。")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("backend/data/adventures/lost_mine/nodes.candidate.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("backend/data/adventures/lost_mine/extraction_report.json"),
    )
    args = parser.parse_args()

    if args.model:
        settings.memory_summary_model = args.model
    if args.timeout is not None:
        settings.memory_summary_timeout_seconds = args.timeout

    existing_candidates = []
    existing_report_items = []
    if args.resume and args.out.exists():
        existing_candidates = json.loads(args.out.read_text(encoding="utf-8"))
    if args.resume and args.report.exists():
        existing_report_items = json.loads(args.report.read_text(encoding="utf-8")).get("items", [])

    candidates, report = extract_nodes(
        args.pdf,
        limit=args.limit,
        max_chars=args.max_chars,
        only_id=args.only_id,
        existing_candidates=existing_candidates,
        existing_report_items=existing_report_items,
        stop_after_failures=args.stop_after_failures,
        sleep_seconds=args.sleep_seconds,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote candidates: {args.out}")
    print(f"wrote report: {args.report}")


if __name__ == "__main__":
    main()
