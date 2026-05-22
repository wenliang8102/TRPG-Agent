from __future__ import annotations

import argparse
import bisect
import json
import pickle
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

import fitz
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config.settings import settings
from app.rag import phb_pipeline
from app.rag.embeddings import build_embeddings

BACKEND_DIR = Path(__file__).resolve().parents[2]
BOOKS_DIR = Path.home() / "Downloads" / "DND_5E" / "DND_5E_规则书" / "三宝书"

DEFAULT_DMG_PATH = BOOKS_DIR / "DND_5E_城主指南CN.pdf"
DEFAULT_MM_PATH = BOOKS_DIR / "DND_5E_怪物图鉴CN.pdf"
DEFAULT_REPORT_PATH = BACKEND_DIR / "data" / "rag_build_reports" / "core_cn_sections.jsonl"
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "rag_core_cn_db"
VECTOR_READY_MARKER = "VECTOR_READY"

SKIP_LAYOUT_TITLES = {
    "制作组",
    "封面故事",
    "关于翻译",
    "目录Contents",
    "空白页面",
    "DUNGEON MASTER’S GUIDE",
    "MONSTER MANUAL",
}


@dataclass(frozen=True, slots=True)
class LayoutBookSpec:
    source: str
    book: str
    pdf_path: Path
    min_page_index: int
    title_detector: Callable[[phb_pipeline.PdfLine], bool]
    level_detector: Callable[[phb_pipeline.PdfLine], int]
    category_infer: Callable[[str], str]
    parallel_page_spans: bool = False


@dataclass(slots=True)
class LayoutAnchor:
    level: int
    title: str
    page_index: int
    x0: float
    y0: float
    section_path: str
    category: str
    anchor_id: str


def _normalize_title(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _is_skip_title(text: str) -> bool:
    normalized = _normalize_title(text)
    if normalized in SKIP_LAYOUT_TITLES:
        return True
    return bool(re.fullmatch(r"\d+", normalized))


def _dmg_title_detector(line: phb_pipeline.PdfLine) -> bool:
    text = _normalize_title(line.text)
    if _is_skip_title(text):
        return False
    if line.y0 < 55:
        return False
    return line.max_size >= 9.6 and line.color == 8388608


def _dmg_level_detector(line: phb_pipeline.PdfLine) -> int:
    if line.max_size >= 17.0:
        return 1
    if line.max_size >= 14.0:
        return 2
    if line.max_size >= 11.5:
        return 3
    return 4


def _mm_title_detector(line: phb_pipeline.PdfLine) -> bool:
    text = _normalize_title(line.text)
    if _is_skip_title(text):
        return False
    if line.page_index < 4:
        return False
    if line.max_size < 13.2:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text) and re.search(r"[A-Za-z]", text))


def _mm_level_detector(line: phb_pipeline.PdfLine) -> int:
    return 1


def _infer_dmg_category(section_path: str) -> str:
    path = section_path.lower()
    if "魔法物品" in section_path or "稀有度" in section_path or "神器" in section_path:
        return "magic_items"
    if "宝藏" in section_path or "财宝" in section_path:
        return "treasure"
    if "第1 章" in section_path or "你的世界" in section_path:
        return "worldbuilding"
    if "第2 章" in section_path or "多元宇宙" in section_path or "位面" in section_path:
        return "planes"
    if "第3 章" in section_path or "冒险" in section_path and "环境" not in section_path:
        return "adventure_design"
    if "第4 章" in section_path or "npc" in path or "非玩家角色" in section_path:
        return "npcs"
    if "第5 章" in section_path or "地城" in section_path or "荒野" in section_path:
        return "adventure_environments"
    if "第6 章" in section_path or "休整" in section_path:
        return "downtime"
    if "第9 章" in section_path or "选用规则" in section_path or "可选规则" in section_path:
        return "rules_options"
    if "第8 章" in section_path or "运行游戏" in section_path or "裁定" in section_path:
        return "running_game"
    return "dmg_general"


def _infer_mm_category(section_path: str) -> str:
    if "传奇动作" in section_path or "挑战等级" in section_path:
        return "monsters"
    return "monsters"


def _layout_anchors(lines: list[phb_pipeline.PdfLine], spec: LayoutBookSpec, *, limit: int | None = None) -> list[LayoutAnchor]:
    anchors: list[LayoutAnchor] = []
    stack: list[str] = []

    for line in lines:
        if line.page_index < spec.min_page_index:
            continue
        if not spec.title_detector(line):
            continue

        title = _normalize_title(line.text)
        level = spec.level_detector(line)
        stack = stack[: level - 1]
        stack.append(title)
        section_path = " > ".join(stack)
        category = spec.category_infer(section_path)
        anchor_id = f"{spec.source}:{line.page_index + 1}:{phb_pipeline._slug(section_path)}"
        anchors.append(
            LayoutAnchor(
                level=level,
                title=title,
                page_index=line.page_index,
                x0=line.x0,
                y0=line.y0,
                section_path=section_path,
                category=category,
                anchor_id=anchor_id,
            )
        )
        if limit and len(anchors) >= limit:
            break

    anchors.sort(key=lambda item: (*phb_pipeline._reading_key(item.page_index, item.x0, item.y0), item.level))
    return anchors


def _line_in_span(
    line: phb_pipeline.PdfLine,
    start: LayoutAnchor,
    end: LayoutAnchor | None,
    *,
    parallel_page_spans: bool = False,
) -> bool:
    if (
        parallel_page_spans
        and end
        and line.page_index == start.page_index == end.page_index
        and start.x0 < 300
        and end.x0 < 300
        and line.x0 >= 300
    ):
        return start.y0 - 3.0 <= line.y0 < end.y0 - 3.0

    line_key = phb_pipeline._reading_key(line.page_index, line.x0, line.y0)
    start_key = phb_pipeline._reading_key(start.page_index, start.x0, max(start.y0 - 3.0, 0.0))
    if line_key < start_key:
        return False
    if end and line_key >= phb_pipeline._reading_key(end.page_index, end.x0, max(end.y0 - 3.0, 0.0)):
        return False
    return True


def _span_lines(
    lines: list[phb_pipeline.PdfLine],
    line_keys: list[tuple[int, int, float]],
    start: LayoutAnchor,
    end: LayoutAnchor | None,
    *,
    parallel_page_spans: bool = False,
) -> list[phb_pipeline.PdfLine]:
    start_key = phb_pipeline._reading_key(start.page_index, start.x0, max(start.y0 - 3.0, 0.0))
    start_index = bisect.bisect_left(line_keys, start_key)
    if end is None:
        return lines[start_index:]

    end_key = phb_pipeline._reading_key(end.page_index, end.x0, max(end.y0 - 3.0, 0.0))
    end_index = bisect.bisect_left(line_keys, end_key)
    span = lines[start_index:end_index]
    if not (
        parallel_page_spans
        and start.page_index == end.page_index
        and start.x0 < 300
        and end.x0 < 300
    ):
        return span

    right_start_key = phb_pipeline._reading_key(start.page_index, 300.0, max(start.y0 - 3.0, 0.0))
    right_end_key = phb_pipeline._reading_key(end.page_index, 300.0, max(end.y0 - 3.0, 0.0))
    right_start_index = bisect.bisect_left(line_keys, right_start_key)
    right_end_index = bisect.bisect_left(line_keys, right_end_key)
    return [*span, *lines[right_start_index:right_end_index]]


def _section_record(spec: LayoutBookSpec, anchor: LayoutAnchor, lines: list[phb_pipeline.PdfLine]) -> phb_pipeline.SectionRecord:
    text = phb_pipeline._merge_text_lines(lines)
    page_end = lines[-1].page_index + 1 if lines else anchor.page_index + 1
    return phb_pipeline.SectionRecord(
        anchor_id=anchor.anchor_id,
        source=spec.source,
        book=spec.book,
        category=anchor.category,
        title=anchor.title,
        section_path=anchor.section_path,
        page_start=anchor.page_index + 1,
        page_end=page_end,
        toc_level=anchor.level,
        line_count=len(lines),
        char_count=len(text),
        text=text,
    )


def build_layout_sections(spec: LayoutBookSpec, *, limit: int | None = None, min_chars: int = 60) -> list[phb_pipeline.SectionRecord]:
    with fitz.open(spec.pdf_path) as doc:
        lines = phb_pipeline._all_lines(doc)
        anchors = _layout_anchors(lines, spec, limit=limit + 1 if limit else None)

    sections: list[phb_pipeline.SectionRecord] = []
    emit_anchors = anchors[:limit] if limit else anchors
    line_keys = [phb_pipeline._reading_key(line.page_index, line.x0, line.y0) for line in lines]
    for index, anchor in enumerate(emit_anchors):
        next_anchor = anchors[index + 1] if index + 1 < len(anchors) else None
        span_lines = _span_lines(
            lines,
            line_keys,
            anchor,
            next_anchor,
            parallel_page_spans=spec.parallel_page_spans,
        )
        text = phb_pipeline._merge_text_lines(span_lines)
        if len(text) < min_chars:
            continue
        sections.append(_section_record(spec, anchor, span_lines))
    return sections


def _default_layout_specs(dmg_path: Path = DEFAULT_DMG_PATH, mm_path: Path = DEFAULT_MM_PATH) -> list[LayoutBookSpec]:
    return [
        LayoutBookSpec(
            source="dmg_cn",
            book="城主指南",
            pdf_path=dmg_path,
            min_page_index=8,
            title_detector=_dmg_title_detector,
            level_detector=_dmg_level_detector,
            category_infer=_infer_dmg_category,
        ),
        LayoutBookSpec(
            source="mm_cn",
            book="怪物图鉴",
            pdf_path=mm_path,
            min_page_index=4,
            title_detector=_mm_title_detector,
            level_detector=_mm_level_detector,
            category_infer=_infer_mm_category,
            parallel_page_spans=True,
        ),
    ]


def build_core_sections(
    *,
    phb_path: Path = phb_pipeline.DEFAULT_PHB_PATH,
    dmg_path: Path = DEFAULT_DMG_PATH,
    mm_path: Path = DEFAULT_MM_PATH,
    limit: int | None = None,
) -> list[phb_pipeline.SectionRecord]:
    phb_sections = phb_pipeline.build_sections(phb_path, limit=limit)
    sections = list(phb_sections)
    for spec in _default_layout_specs(dmg_path=dmg_path, mm_path=mm_path):
        sections.extend(build_layout_sections(spec, limit=limit))
    return sections


def build_chunk_documents(sections: list[phb_pipeline.SectionRecord]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=360,
        chunk_overlap=70,
        separators=["\n\n", "\n", "。", "；", "，", ""],
    )
    documents: list[Document] = []

    for section in sections:
        base_metadata = {
            "source": section.source,
            "book": section.book,
            "category": section.category,
            "title": section.title,
            "section_path": section.section_path,
            "page_start": section.page_start,
            "page_end": section.page_end,
            "parent_anchor_id": section.anchor_id,
        }
        for chunk_index, chunk in enumerate(splitter.split_text(section.text)):
            chunk = chunk.strip()
            if len(chunk) < 60:
                continue
            metadata = dict(base_metadata)
            metadata.update({"doc_type": "chunk", "chunk_index": chunk_index})
            documents.append(Document(page_content=chunk, metadata=metadata))

    return documents


def write_report(sections: Iterable[phb_pipeline.SectionRecord], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as file:
        for section in sections:
            payload = asdict(section)
            payload["text_preview"] = " ".join(section.text.split())[:360]
            payload.pop("text")
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _reset_db_dir(path: Path) -> None:
    resolved = path.resolve()
    data_dir = (BACKEND_DIR / "data").resolve()
    if data_dir not in resolved.parents:
        raise ValueError(f"Refuse to reset RAG DB outside backend/data: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def build_vector_index(documents: list[Document], db_path: Path) -> None:
    bm25_backup = (db_path / "bm25_index.pkl").read_bytes() if (db_path / "bm25_index.pkl").exists() else None
    embeddings = build_embeddings(settings)
    resume = (db_path / "chroma.sqlite3").exists() and not (db_path / VECTOR_READY_MARKER).exists()
    if not resume:
        _reset_db_dir(db_path)
    try:
        vectorstore = Chroma(
            persist_directory=str(db_path),
            embedding_function=embeddings,
        )
        start_index = vectorstore._collection.count() if resume else 0
        # 三宝书中有大量表格与中英混排条目；小批量写入能更快定位供应商输入限制问题。
        for start in range(start_index, len(documents), 16):
            vectorstore.add_documents(documents[start : start + 16])
        with (db_path / "bm25_index.pkl").open("wb") as file:
            pickle.dump(documents, file)
        (db_path / VECTOR_READY_MARKER).write_text("ok\n", encoding="utf-8")
    except Exception:
        if bm25_backup is not None:
            (db_path / "bm25_index.pkl").write_bytes(bm25_backup)
        raise


# 离线评估先保证本地 BM25 基线可重复运行，避免外部 embedding 不可用时完全无报告。
def build_bm25_index(documents: list[Document], db_path: Path) -> None:
    _reset_db_dir(db_path)
    with (db_path / "bm25_index.pkl").open("wb") as file:
        pickle.dump(documents, file)


def _summary(sections: list[phb_pipeline.SectionRecord], chunks: list[Document]) -> dict[str, object]:
    by_book: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for section in sections:
        by_book[section.book] = by_book.get(section.book, 0) + 1
        by_category[section.category] = by_category.get(section.category, 0) + 1

    return {
        "sections": len(sections),
        "chunks": len(chunks),
        "books": dict(sorted(by_book.items())),
        "categories": dict(sorted(by_category.items())),
        "short_sections": sum(1 for section in sections if section.char_count < 80),
        "long_sections": sum(1 for section in sections if section.char_count > 5000),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="构建中文 PHB/DMG/MM 三宝书 RAG 索引。")
    parser.add_argument("--phb", type=Path, default=phb_pipeline.DEFAULT_PHB_PATH)
    parser.add_argument("--dmg", type=Path, default=DEFAULT_DMG_PATH)
    parser.add_argument("--mm", type=Path, default=DEFAULT_MM_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=0, help="每本书只处理前 N 个锚点；0 表示全量。")
    parser.add_argument("--build-index", action="store_true", help="写入三宝书向量索引。")
    parser.add_argument("--build-bm25", action="store_true", help="只写入 BM25 索引，便于无 embedding key 时离线评估。")
    args = parser.parse_args()

    limit = args.limit or None
    sections = build_core_sections(phb_path=args.phb, dmg_path=args.dmg, mm_path=args.mm, limit=limit)
    chunks = build_chunk_documents(sections)
    write_report(sections, args.report)
    if args.build_index:
        build_vector_index(chunks, args.db)
    elif args.build_bm25:
        build_bm25_index(chunks, args.db)

    print(json.dumps(_summary(sections, chunks), ensure_ascii=False, indent=2))
    print(f"Report: {args.report}")
    if args.build_index or args.build_bm25:
        print(f"ChromaDB: {args.db}")


if __name__ == "__main__":
    main()
