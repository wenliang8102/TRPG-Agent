from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ROOT_DIR))

from app.config.settings import settings  # noqa: E402
from app.rag.retriever import TRPGHybridRetriever  # noqa: E402
from scripts.evaluate_rag_retrieval import (  # noqa: E402
    DEFAULT_CASES_PATH,
    _document_eval_text,
    _hit_terms,
    _load_cases,
    _resolve_workspace_path,
)

DEFAULT_REPORT_PATH = BACKEND_DIR / "data" / "rag_build_reports" / "opportunistic_rag_eval.json"


def _diagnostics_payload(diagnostics: Any, result_count: int) -> dict[str, Any]:
    return {
        "result_count": result_count,
        "candidate_count": diagnostics.candidate_count,
        "bm25_candidate_count": diagnostics.bm25_candidate_count,
        "vector_candidate_count": diagnostics.vector_candidate_count,
        "rerank_duration_ms": diagnostics.rerank_duration_ms,
        "top_scores": diagnostics.top_scores or [],
        "failure_reason": diagnostics.failure_reason,
    }


def _retrieval_passed(case: Any, docs: list) -> tuple[bool, bool, bool, list[str]]:
    joined = _document_eval_text(docs)
    hit_terms = _hit_terms(joined, case.expected_terms)
    term_hit = len(hit_terms) == len(case.expected_terms)
    category_hit = (
        case.expected_category is None
        or any(doc.metadata.get("category") == case.expected_category for doc in docs)
    )
    return term_hit and category_hit, term_hit, category_hit, hit_terms


def _score_allows_injection(top_scores: list[float], min_score: float) -> tuple[bool, str]:
    if not top_scores:
        return False, "no_rerank_score"
    if max(top_scores) < min_score:
        return False, "below_min_score"
    return True, "injected"


def _evaluate_case(
    retriever: TRPGHybridRetriever,
    case: Any,
    *,
    top_k: int,
    timeout_ms: int,
    min_score: float,
) -> dict[str, Any]:
    # 中文注释：这里不跑主 LLM，只观察同一检索结果在三种调度策略下会不会进入上下文。
    started = perf_counter()
    docs, diagnostics = retriever.search_with_diagnostics(case.query, top_k=top_k)
    duration_ms = round((perf_counter() - started) * 1000, 3)
    retrieval_passed, term_hit, category_hit, hit_terms = _retrieval_passed(case, docs)
    top_scores = diagnostics.top_scores or []
    score_injected, score_reason = _score_allows_injection(top_scores, min_score)
    sync_injected = bool(docs) and score_injected
    opportunistic_injected = sync_injected and duration_ms <= timeout_ms
    opportunistic_reason = score_reason
    if sync_injected and not opportunistic_injected:
        opportunistic_reason = "timeout"
    elif not docs:
        opportunistic_reason = diagnostics.failure_reason or "no_results"

    return {
        "id": case.id,
        "query": case.query,
        "expected_terms": case.expected_terms,
        "expected_category": case.expected_category,
        "retrieval_passed": retrieval_passed,
        "term_hit": term_hit,
        "category_hit": category_hit,
        "hit_terms": hit_terms,
        "duration_ms": duration_ms,
        "pure_tool": {
            "available": bool(docs),
            "passed": retrieval_passed,
        },
        "sync_auto_rag": {
            "injected": sync_injected,
            "reason": score_reason if docs else diagnostics.failure_reason or "no_results",
            "misinjected": sync_injected and not retrieval_passed,
            "missed_relevant": retrieval_passed and not sync_injected,
        },
        "opportunistic_auto_rag": {
            "injected": opportunistic_injected,
            "reason": opportunistic_reason,
            "misinjected": opportunistic_injected and not retrieval_passed,
            "missed_relevant": retrieval_passed and not opportunistic_injected,
        },
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


def _summary(results: list[dict[str, Any]], timeout_ms: int, min_score: float, top_k: int) -> dict[str, Any]:
    total = len(results)
    retrieval_passed = sum(1 for item in results if item["retrieval_passed"])
    sync_injected = sum(1 for item in results if item["sync_auto_rag"]["injected"])
    opportunistic_injected = sum(1 for item in results if item["opportunistic_auto_rag"]["injected"])
    sync_misinjected = sum(1 for item in results if item["sync_auto_rag"]["misinjected"])
    opportunistic_misinjected = sum(1 for item in results if item["opportunistic_auto_rag"]["misinjected"])
    sync_missed = sum(1 for item in results if item["sync_auto_rag"]["missed_relevant"])
    opportunistic_missed = sum(1 for item in results if item["opportunistic_auto_rag"]["missed_relevant"])
    durations = sorted(float(item["duration_ms"]) for item in results)
    p50 = durations[len(durations) // 2] if durations else 0.0
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0.0
    return {
        "total": total,
        "retrieval_passed": retrieval_passed,
        "retrieval_pass_rate": round(retrieval_passed / total, 3) if total else 0,
        "sync_injected": sync_injected,
        "opportunistic_injected": opportunistic_injected,
        "sync_misinjected": sync_misinjected,
        "opportunistic_misinjected": opportunistic_misinjected,
        "sync_missed_relevant": sync_missed,
        "opportunistic_missed_relevant": opportunistic_missed,
        "duration_p50_ms": round(p50, 3),
        "duration_p95_ms": round(p95, 3),
        "timeout_ms": timeout_ms,
        "min_score": min_score,
        "top_k": top_k,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="对比纯工具、同步自动 RAG 与机会型 RAG 的离线表现。")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--top-k", type=int, default=settings.auto_rule_rag_top_k)
    parser.add_argument("--timeout-ms", type=int, default=settings.auto_rule_rag_timeout_ms)
    parser.add_argument("--min-score", type=float, default=settings.auto_rule_rag_min_score)
    args = parser.parse_args()

    db_path = _resolve_workspace_path(args.db)
    report_path = _resolve_workspace_path(args.report) or args.report
    cases = _load_cases(_resolve_workspace_path(args.cases) or args.cases)
    retriever = TRPGHybridRetriever(db_path=db_path) if db_path else TRPGHybridRetriever()
    results = [
        _evaluate_case(
            retriever,
            case,
            top_k=args.top_k,
            timeout_ms=args.timeout_ms,
            min_score=args.min_score,
        )
        for case in cases
    ]
    summary = _summary(results, args.timeout_ms, args.min_score, args.top_k)
    report = {
        "summary": summary,
        "results": results,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
