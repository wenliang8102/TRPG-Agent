from __future__ import annotations

import json
import re
from pathlib import Path

REPORT_PATH = Path("backend/data/rag_build_reports/phb_cn_sections.jsonl")


def _title_zh(title: str) -> str:
    match = re.match(r"([\u4e00-\u9fff]{2,})", title.strip())
    return match.group(1) if match else ""


def _load_sections() -> list[dict]:
    return [
        json.loads(line)
        for line in REPORT_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    sections = _load_sections()
    bad_starts: list[dict] = []
    missing_title_echo: list[dict] = []
    previous_topic_leaks: list[dict] = []

    for index, section in enumerate(sections):
        preview = section["text_preview"].strip()
        if preview.startswith(("。", "，", "、", "；", "：", "）", "》", "”", "⚫")):
            bad_starts.append(section)

        title_zh = _title_zh(section["title"])
        if title_zh and title_zh not in preview[:120]:
            missing_title_echo.append(section)

        if index == 0:
            continue
        previous = sections[index - 1]
        previous_title_zh = _title_zh(previous["title"])
        if (
            previous_title_zh
            and previous_title_zh in preview[:360]
            and title_zh
            and title_zh not in preview[:120]
        ):
            previous_topic_leaks.append(section)

    payload = {
        "total": len(sections),
        "bad_starts": [
            _brief(section)
            for section in bad_starts[:30]
        ],
        "missing_title_echo": [
            _brief(section)
            for section in missing_title_echo[:30]
        ],
        "previous_topic_leaks": [
            _brief(section)
            for section in previous_topic_leaks[:30]
        ],
        "counts": {
            "bad_starts": len(bad_starts),
            "missing_title_echo": len(missing_title_echo),
            "previous_topic_leaks": len(previous_topic_leaks),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _brief(section: dict) -> dict:
    return {
        "title": section["title"],
        "page_start": section["page_start"],
        "page_end": section["page_end"],
        "category": section["category"],
        "preview": section["text_preview"][:220],
    }


if __name__ == "__main__":
    main()
