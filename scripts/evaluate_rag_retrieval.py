from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from app.rag.retriever import TRPGHybridRetriever  # noqa: E402
from app.rag.retriever import RetrievalDiagnostics  # noqa: E402

DEFAULT_CASES_PATH = BACKEND_DIR / "tests" / "fixtures" / "rag_eval_cases.json"
DEFAULT_REPORT_PATH = BACKEND_DIR / "data" / "rag_build_reports" / "rag_baseline_eval.json"


@dataclass(slots=True)
class EvalCase:
    id: str
    query: str
    expected_terms: list[str | list[str]]
    expected_category: str | None


def _load_cases(path: Path) -> list[EvalCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        EvalCase(
            id=item["id"],
            query=item["query"],
            expected_terms=list(item["expected_terms"]),
            expected_category=item.get("expected_category"),
        )
        for item in payload
    ]


def _resolve_workspace_path(path: Path | None) -> Path | None:
    if path is None or path.is_absolute():
        return path
    return ROOT_DIR / path


def _document_eval_text(docs: list) -> str:
    parts: list[str] = []
    for doc in docs:
        parts.extend(
            str(doc.metadata.get(key) or "")
            for key in ("title", "section", "section_path", "category")
        )
        parts.append(doc.page_content)
    return "\n".join(parts)


def _hit_terms(text: str, expected_terms: list[str | list[str]]) -> list[str]:
    normalized = text.lower()
    hits: list[str] = []
    for term in expected_terms:
        variants = term if isinstance(term, list) else [term]
        matched = next((variant for variant in variants if variant.lower() in normalized), None)
        if matched is not None:
            hits.append(matched)
    return hits


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


# 离线评估需要保留检索链路指标，方便区分召回失败和 rerank 失败。
def _evaluate_case(retriever: TRPGHybridRetriever, case: EvalCase, top_k: int) -> dict[str, Any]:
    docs, diagnostics = retriever.search_with_diagnostics(case.query, top_k=top_k)
    joined = _document_eval_text(docs)
    hit_terms = _hit_terms(joined, case.expected_terms)
    category_hit = (
        case.expected_category is None
        or any(doc.metadata.get("category") == case.expected_category for doc in docs)
    )
    term_hit = len(hit_terms) == len(case.expected_terms)

    return {
        "id": case.id,
        "query": case.query,
        "passed": term_hit and category_hit,
        "term_hit": term_hit,
        "category_hit": category_hit,
        "hit_terms": hit_terms,
        "expected_terms": case.expected_terms,
        "expected_category": case.expected_category,
        "diagnostics": _diagnostics_payload(diagnostics, len(docs)),
        "results": [
            {
                "category": doc.metadata.get("category"),
                "section": doc.metadata.get("section") or doc.metadata.get("title"),
                "source": doc.metadata.get("source"),
                "page_start": doc.metadata.get("page_start"),
                "page_end": doc.metadata.get("page_end"),
                "preview": " ".join(doc.page_content.split())[:260],
            }
            for doc in docs
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="运行规则 RAG 召回基线评测。")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()
    db_path = _resolve_workspace_path(args.db)
    report_path = _resolve_workspace_path(args.report) or args.report

    cases = _load_cases(args.cases)
    retriever = TRPGHybridRetriever(db_path=db_path) if db_path else TRPGHybridRetriever()
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
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
