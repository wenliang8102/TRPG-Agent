from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any, Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
import os

os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.documents import Document  # noqa: E402

from app.rag.phb_pipeline import DEFAULT_DB_PATH, DEFAULT_PHB_PATH, build_chunk_documents, build_sections  # noqa: E402
from app.rag.retriever import TRPGHybridRetriever  # noqa: E402
from app.rag.retriever import RetrievalDiagnostics  # noqa: E402

DEFAULT_CASES_PATH = BACKEND_DIR / "tests" / "fixtures" / "phb_cn_eval_cases.json"
DEFAULT_REPORT_PATH = BACKEND_DIR / "data" / "rag_build_reports" / "phb_cn_eval.json"


@dataclass(slots=True)
class EvalCase:
    id: str
    query: str
    expected_terms: list[str | list[str]]
    expected_category: str
    expected_top_titles: list[str | list[str]] = field(default_factory=list)


def _load_cases(path: Path) -> list[EvalCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        EvalCase(
            id=item["id"],
            query=item["query"],
            expected_terms=list(item["expected_terms"]),
            expected_category=item["expected_category"],
            expected_top_titles=list(item.get("expected_top_titles", [])),
        )
        for item in payload
    ]


def _resolve_workspace_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT_DIR / path


def _load_chunk_documents(pdf_path: Path) -> list[Document]:
    return build_chunk_documents(build_sections(pdf_path, limit=None))


def _contains_all_terms(text: str, terms: list[str | list[str]]) -> tuple[bool, list[str]]:
    normalized = text.lower()
    hits: list[str] = []
    for term in terms:
        variants = term if isinstance(term, list) else [term]
        matched = next((variant for variant in variants if variant.lower() in normalized), None)
        if matched is not None:
            hits.append(matched)
    return len(hits) == len(terms), hits


def _document_eval_text(docs: list[Document]) -> str:
    parts: list[str] = []
    for doc in docs:
        parts.extend(
            str(doc.metadata.get(key) or "")
            for key in ("title", "section_path", "category")
        )
        parts.append(doc.page_content)
    return "\n".join(parts)


def _matches_any_title(title: str, expected_titles: list[str | list[str]]) -> tuple[bool, str | None]:
    if not expected_titles:
        return True, None

    normalized = title.lower()
    for expected_title in expected_titles:
        variants = expected_title if isinstance(expected_title, list) else [expected_title]
        matched = next((variant for variant in variants if variant.lower() in normalized), None)
        if matched is not None:
            return True, matched
    return False, None


def _diagnostics_payload(diagnostics: RetrievalDiagnostics, result_count: int) -> dict[str, Any]:
    return {
        "result_count": result_count,
        "candidate_count": diagnostics.candidate_count,
        "bm25_candidate_count": diagnostics.bm25_candidate_count,
        "vector_candidate_count": diagnostics.vector_candidate_count,
        "rerank_duration_ms": diagnostics.rerank_duration_ms,
        "top_scores": diagnostics.top_scores or [],
        "failure_reason": diagnostics.failure_reason,
    }


# PHB/Core 共用评估逻辑，报告要暴露候选与 rerank 指标来定位丢失层级。
def _evaluate_case(retriever: TRPGHybridRetriever, case: EvalCase, top_k: int) -> dict[str, Any]:
    docs, diagnostics = retriever.search_with_diagnostics(
        case.query,
        filter_category=case.expected_category,
        top_k=top_k,
    )
    joined = _document_eval_text(docs)
    term_hit, hit_terms = _contains_all_terms(joined, case.expected_terms)
    category_hit = any(doc.metadata.get("category") == case.expected_category for doc in docs)
    top_title = docs[0].metadata.get("title", "") if docs else ""
    top_title_hit, matched_top_title = _matches_any_title(top_title, case.expected_top_titles)

    return {
        "id": case.id,
        "query": case.query,
        "passed": term_hit and category_hit and top_title_hit,
        "term_hit": term_hit,
        "category_hit": category_hit,
        "top_title_hit": top_title_hit,
        "matched_top_title": matched_top_title,
        "top_title": top_title,
        "hit_terms": hit_terms,
        "expected_terms": case.expected_terms,
        "expected_category": case.expected_category,
        "expected_top_titles": case.expected_top_titles,
        "diagnostics": _diagnostics_payload(diagnostics, len(docs)),
        "results": [
            {
                "category": doc.metadata.get("category"),
                "title": doc.metadata.get("title"),
                "section_path": doc.metadata.get("section_path"),
                "page_start": doc.metadata.get("page_start"),
                "page_end": doc.metadata.get("page_end"),
                "preview": " ".join(doc.page_content.split())[:320],
            }
            for doc in docs
        ],
    }


def main(
    *,
    default_db_path: Path = DEFAULT_DB_PATH,
    default_cases_path: Path = DEFAULT_CASES_PATH,
    default_report_path: Path = DEFAULT_REPORT_PATH,
    document_loader: Callable[[Path], list[Document]] = _load_chunk_documents,
) -> None:
    parser = argparse.ArgumentParser(description="观察 PHB 中文测试索引的召回命中效果。")
    parser.add_argument("--cases", type=Path, default=default_cases_path)
    parser.add_argument("--db", type=Path, default=default_db_path)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PHB_PATH)
    parser.add_argument("--report", type=Path, default=default_report_path)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    db_path = _resolve_workspace_path(args.db)
    pdf_path = _resolve_workspace_path(args.pdf)
    report_path = _resolve_workspace_path(args.report)

    cases = _load_cases(args.cases)
    retriever = TRPGHybridRetriever(
        db_path=db_path,
        bm25_documents=document_loader(pdf_path),
    )
    results = [_evaluate_case(retriever, case, args.top_k) for case in cases]
    passed = sum(1 for item in results if item["passed"])
    report = {
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 3) if results else 0,
        "top_k": args.top_k,
        "results": results,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["total", "passed", "pass_rate", "top_k"]}, ensure_ascii=False, indent=2))
    for item in results:
        print(
            f"{'PASS' if item['passed'] else 'FAIL'} {item['id']} "
            f"terms={item['hit_terms']} top={item['top_title']}"
        )
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
