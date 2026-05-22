from __future__ import annotations

import argparse
import bisect
import json
import pickle
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import fitz
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config.settings import settings
from app.rag.embeddings import build_embeddings

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PHB_PATH = (
    Path.home()
    / "Downloads"
    / "DND_5E"
    / "DND_5E_规则书"
    / "三宝书"
    / "DND_5E_玩家手册CN.pdf"
)
DEFAULT_REPORT_PATH = BACKEND_DIR / "data" / "rag_build_reports" / "phb_cn_sections.jsonl"
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "rag_phb_cn_db"
VECTOR_READY_MARKER = "VECTOR_READY"

SOURCE_TAG = "phb_cn"
BOOK_TITLE = "玩家手册"

SKIP_TOC_TITLES = {"制作组", "封面故事", "关于翻译", "目录Contents"}


@dataclass(slots=True)
class PdfLine:
    page_index: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    max_size: float
    color: int


@dataclass(slots=True)
class PhbAnchor:
    level: int
    title: str
    page_index: int
    x0: float
    y0: float
    section_path: str
    category: str
    anchor_id: str


@dataclass(slots=True)
class SectionRecord:
    anchor_id: str
    source: str
    book: str
    category: str
    title: str
    section_path: str
    page_start: int
    page_end: int
    toc_level: int
    line_count: int
    char_count: int
    text: str


@dataclass(slots=True)
class SectionDraft:
    anchor: PhbAnchor
    lines: list[PdfLine]


def _normalize_key(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\s:：？?·.,，。()（）\-—’'\"“”]+", "", value)


def _slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized[:120] or "section"


def _line_text(line: dict) -> str:
    return "".join(span["text"] for span in line["spans"]).strip()


# 中文双栏 PDF 的物理文本流常常串栏；后续所有切分都依赖这里的阅读顺序。
def _extract_page_lines(page: fitz.Page, page_index: int) -> list[PdfLine]:
    page_width = page.rect.width
    page_height = page.rect.height
    raw = page.get_text("dict")
    lines: list[PdfLine] = []

    for block in raw["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = _line_text(line)
            if not text or text.isdigit():
                continue

            x0, y0, x1, y1 = line["bbox"]
            if y0 < 45 or y0 > page_height - 35:
                continue

            spans = line["spans"]
            lines.append(
                PdfLine(
                    page_index=page_index,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    text=text,
                    max_size=max(span["size"] for span in spans),
                    color=spans[0].get("color", 0),
                )
            )

    mid_x = page_width / 2
    return sorted(lines, key=lambda item: (0 if item.x0 < mid_x else 1, item.y0, item.x0))


def _all_lines(doc: fitz.Document) -> list[PdfLine]:
    lines: list[PdfLine] = []
    for page_index in range(doc.page_count):
        lines.extend(_extract_page_lines(doc[page_index], page_index))
    return lines


def _reading_key(page_index: int, x0: float, y0: float) -> tuple[int, int, float]:
    return page_index, 0 if x0 < 300 else 1, y0


def _toc_destination(item: list) -> tuple[int, float, float] | None:
    if len(item) < 4:
        return None

    dest = item[3]
    if not isinstance(dest, dict) or dest.get("page", -1) < 0:
        return None

    point = dest.get("to")
    if point is None:
        return int(dest["page"]), 0.0, 0.0
    return int(dest["page"]), float(point.x), float(point.y)


def _infer_category(section_path: str) -> str:
    path = section_path.lower()
    if "第2章：种族" in section_path:
        return "species"
    if "第3章：职业" in section_path:
        return "classes"
    if "第4章：个性与背景" in section_path:
        return "backgrounds"
    if "第5章：装备" in section_path:
        return "equipment"
    if "第6章：自定义选项" in section_path:
        return "character_options"
    if "第7章：属性值应用" in section_path:
        return "ability_checks"
    if "第8章：冒险" in section_path:
        return "adventuring"
    if "第9章：战斗" in section_path:
        return "combat"
    if "第10章：施法" in section_path:
        return "spellcasting"
    if "第11章：法术" in section_path or "法术" in section_path and "spell" in path:
        return "spells"
    if "附录a" in path or "状态" in section_path:
        return "conditions"
    if "第1章：一步步创建角色" in section_path:
        return "character_creation"
    return "general"


# PHB 的书签自带坐标和层级，section_path 直接从书签栈生成，避免后补元数据。
def _phb_anchors(doc: fitz.Document, *, limit: int | None = None) -> list[PhbAnchor]:
    anchors: list[PhbAnchor] = []
    stack: list[str] = []

    for item in doc.get_toc(simple=False):
        level, raw_title = int(item[0]), str(item[1]).strip()
        if raw_title in SKIP_TOC_TITLES:
            continue

        destination = _toc_destination(item)
        if destination is None:
            continue

        page_index, x0, y0 = destination
        stack = stack[: level - 1]
        stack.append(raw_title)
        section_path = " > ".join(stack)
        category = _infer_category(section_path)
        anchor_id = f"{SOURCE_TAG}:{page_index + 1}:{_slug(section_path)}"
        anchors.append(
            PhbAnchor(
                level=level,
                title=raw_title,
                page_index=page_index,
                x0=x0,
                y0=y0,
                section_path=section_path,
                category=category,
                anchor_id=anchor_id,
            )
        )
        if limit and len(anchors) >= limit:
            break

    anchors.sort(key=lambda item: (*_reading_key(item.page_index, item.x0, item.y0), item.level))
    return anchors


def _line_in_span(line: PdfLine, start: PhbAnchor, end: PhbAnchor | None) -> bool:
    line_key = _reading_key(line.page_index, line.x0, line.y0)
    # 书签目标点常比实际标题基线低一两点；边界前移可以避免把下一个标题粘到当前段尾。
    start_key = _reading_key(start.page_index, start.x0, max(start.y0 - 3.0, 0.0))
    if line_key < start_key:
        return False
    if end and line_key >= _reading_key(end.page_index, end.x0, max(end.y0 - 3.0, 0.0)):
        return False
    return True


def _span_lines(
    lines: list[PdfLine],
    line_keys: list[tuple[int, int, float]],
    start: PhbAnchor,
    end: PhbAnchor | None,
) -> list[PdfLine]:
    start_key = _reading_key(start.page_index, start.x0, max(start.y0 - 3.0, 0.0))
    start_index = bisect.bisect_left(line_keys, start_key)
    if end is None:
        return lines[start_index:]

    end_key = _reading_key(end.page_index, end.x0, max(end.y0 - 3.0, 0.0))
    end_index = bisect.bisect_left(line_keys, end_key)
    return lines[start_index:end_index]


def _is_title_line(line: PdfLine) -> bool:
    return line.max_size >= 11.5 or line.color not in {0, 197381}


def _merge_text_lines(lines: Iterable[PdfLine]) -> str:
    paragraphs: list[str] = []
    current = ""
    previous: PdfLine | None = None

    for line in lines:
        text = line.text.strip()
        if not text:
            continue

        gap = line.y0 - previous.y1 if previous and previous.page_index == line.page_index else 99
        starts_new = not current or _is_title_line(line) or text.startswith("•") or gap > 8

        if starts_new:
            if current:
                paragraphs.append(current.strip())
            current = text
        else:
            joiner = " " if re.search(r"[A-Za-z0-9]$", current) and re.match(r"[A-Za-z0-9]", text) else ""
            current = f"{current}{joiner}{text}"

        previous = line

    if current:
        paragraphs.append(current.strip())

    text = "\n".join(paragraphs)
    text = re.sub(r"([，。；：！？、）])\n(?=[^\n•])", r"\1", text)
    return text.strip()


def _section_record(anchor: PhbAnchor, lines: list[PdfLine]) -> SectionRecord:
    text = _merge_text_lines(lines)
    page_end = lines[-1].page_index + 1 if lines else anchor.page_index + 1
    return SectionRecord(
        anchor_id=anchor.anchor_id,
        source=SOURCE_TAG,
        book=BOOK_TITLE,
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


def _title_head_zh(title: str) -> str:
    match = re.match(r"([\u4e00-\u9fff]{2,})", title.strip())
    return match.group(1) if match else ""


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _inserted_block_split_index(previous: SectionDraft, current: SectionDraft) -> int | None:
    if previous.anchor.page_index != current.anchor.page_index:
        return None
    if previous.anchor.x0 >= 300 or current.anchor.x0 < 300:
        return None
    if current.anchor.y0 >= previous.anchor.y0:
        return None

    body_indexes = [
        index
        for index, line in enumerate(current.lines)
        if line.page_index == current.anchor.page_index
        and line.x0 >= 300
        and not _is_title_line(line)
    ]
    if len(body_indexes) < 6:
        return None

    early_sizes = [current.lines[index].max_size for index in body_indexes[:5]]
    inserted_body_size = _median(early_sizes)
    previous_topic = _title_head_zh(previous.anchor.title)
    if not previous_topic:
        return None

    for index in body_indexes[5:]:
        line = current.lines[index]
        if line.max_size < inserted_body_size + 0.35:
            continue

        suffix_text = _merge_text_lines(current.lines[index:])
        if previous_topic not in suffix_text[:420]:
            continue
        return index

    return None


def _repair_inserted_blocks(drafts: list[SectionDraft]) -> list[SectionDraft]:
    repaired = list(drafts)
    for index in range(1, len(repaired)):
        previous = repaired[index - 1]
        current = repaired[index]
        split_index = _inserted_block_split_index(previous, current)
        if split_index is None:
            continue

        # 右栏插入块会打断左栏小节续文；把插入块后的正文归还给上一节。
        previous.lines.extend(current.lines[split_index:])
        previous.lines.sort(key=lambda line: _reading_key(line.page_index, line.x0, line.y0))
        current.lines = current.lines[:split_index]

    return [draft for draft in repaired if len(_merge_text_lines(draft.lines)) >= 30]


def build_sections(pdf_path: Path, *, limit: int | None = None, min_chars: int = 30) -> list[SectionRecord]:
    with fitz.open(pdf_path) as doc:
        lines = _all_lines(doc)
        # dry-run 限量时仍多取一个锚点作为边界，避免最后一个样本吞掉后续整本书。
        anchors = _phb_anchors(doc, limit=limit + 1 if limit else None)

    drafts: list[SectionDraft] = []
    emit_anchors = anchors[:limit] if limit else anchors
    line_keys = [_reading_key(line.page_index, line.x0, line.y0) for line in lines]
    for index, anchor in enumerate(emit_anchors):
        next_anchor = anchors[index + 1] if index + 1 < len(anchors) else None
        span_lines = _span_lines(lines, line_keys, anchor, next_anchor)
        text = _merge_text_lines(span_lines)
        if len(text) < min_chars:
            continue

        drafts.append(SectionDraft(anchor=anchor, lines=span_lines))

    return [_section_record(draft.anchor, draft.lines) for draft in _repair_inserted_blocks(drafts)]


def build_chunk_documents(sections: list[SectionRecord]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=520,
        chunk_overlap=80,
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


def write_report(sections: list[SectionRecord], report_path: Path) -> None:
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
        for start in range(start_index, len(documents), 16):
            vectorstore.add_documents(documents[start : start + 16])
        with (db_path / "bm25_index.pkl").open("wb") as file:
            pickle.dump(documents, file)
        (db_path / VECTOR_READY_MARKER).write_text("ok\n", encoding="utf-8")
    except Exception:
        if bm25_backup is not None:
            (db_path / "bm25_index.pkl").write_bytes(bm25_backup)
        raise


# 离线评估不能依赖外部 embedding 服务；BM25-only 索引用来测本地召回下限。
def build_bm25_index(documents: list[Document], db_path: Path) -> None:
    _reset_db_dir(db_path)
    with (db_path / "bm25_index.pkl").open("wb") as file:
        pickle.dump(documents, file)


def _summary(sections: list[SectionRecord], chunks: list[Document]) -> dict[str, object]:
    by_category: dict[str, int] = {}
    for section in sections:
        by_category[section.category] = by_category.get(section.category, 0) + 1

    return {
        "sections": len(sections),
        "chunks": len(chunks),
        "categories": dict(sorted(by_category.items())),
        "short_sections": sum(1 for section in sections if section.char_count < 80),
        "long_sections": sum(1 for section in sections if section.char_count > 4000),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="试切分中文玩家手册，输出 JSONL 质量报告。")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PHB_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=120, help="只处理前 N 个 TOC 锚点，便于先看样本质量。")
    parser.add_argument("--build-index", action="store_true", help="写入独立 PHB 测试向量索引。")
    parser.add_argument("--build-bm25", action="store_true", help="只写入 BM25 索引，便于无 embedding key 时离线评估。")
    args = parser.parse_args()

    sections = build_sections(args.pdf, limit=args.limit)
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
