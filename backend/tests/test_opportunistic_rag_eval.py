import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from scripts.evaluate_opportunistic_rag import _score_allows_injection, _summary


def test_score_gate_requires_rerank_score_above_threshold():
    assert _score_allows_injection([], 0.5) == (False, "no_rerank_score")
    assert _score_allows_injection([0.49], 0.5) == (False, "below_min_score")
    assert _score_allows_injection([0.51], 0.5) == (True, "injected")


def test_summary_counts_sync_and_opportunistic_tradeoffs():
    results = [
        {
            "retrieval_passed": True,
            "duration_ms": 100.0,
            "sync_auto_rag": {"injected": True, "misinjected": False, "missed_relevant": False},
            "opportunistic_auto_rag": {"injected": True, "misinjected": False, "missed_relevant": False},
        },
        {
            "retrieval_passed": True,
            "duration_ms": 900.0,
            "sync_auto_rag": {"injected": True, "misinjected": False, "missed_relevant": False},
            "opportunistic_auto_rag": {"injected": False, "misinjected": False, "missed_relevant": True},
        },
        {
            "retrieval_passed": False,
            "duration_ms": 200.0,
            "sync_auto_rag": {"injected": True, "misinjected": True, "missed_relevant": False},
            "opportunistic_auto_rag": {"injected": True, "misinjected": True, "missed_relevant": False},
        },
    ]

    summary = _summary(results, timeout_ms=800, min_score=0.5, top_k=6)

    assert summary["total"] == 3
    assert summary["retrieval_passed"] == 2
    assert summary["sync_injected"] == 3
    assert summary["opportunistic_injected"] == 2
    assert summary["sync_misinjected"] == 1
    assert summary["opportunistic_misinjected"] == 1
    assert summary["opportunistic_missed_relevant"] == 1
    assert summary["duration_p50_ms"] == 200.0
