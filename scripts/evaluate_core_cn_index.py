from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.chdir(BACKEND_DIR)
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(BACKEND_DIR))

from app.rag.core_books_pipeline import DEFAULT_DB_PATH  # noqa: E402
from app.rag.retriever import TRPGHybridRetriever  # noqa: E402
from scripts.evaluate_phb_cn_index import _evaluate_case, _load_cases  # noqa: E402

DEFAULT_CASES_PATH = BACKEND_DIR / "tests" / "fixtures" / "core_cn_eval_cases.json"
DEFAULT_REPORT_PATH = BACKEND_DIR / "data" / "rag_build_reports" / "core_cn_eval.json"


def main() -> None:
    cases = _load_cases(DEFAULT_CASES_PATH)
    retriever = TRPGHybridRetriever(db_path=DEFAULT_DB_PATH)
    results = [_evaluate_case(retriever, case, top_k=5) for case in cases]
    passed = sum(1 for item in results if item["passed"])
    report = {
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 3) if results else 0,
        "top_k": 5,
        "results": results,
    }

    DEFAULT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["total", "passed", "pass_rate", "top_k"]}, ensure_ascii=False, indent=2))
    for item in results:
        print(
            f"{'PASS' if item['passed'] else 'FAIL'} {item['id']} "
            f"terms={item['hit_terms']} top={item['top_title']}"
        )
    print(f"Report: {DEFAULT_REPORT_PATH}")


if __name__ == "__main__":
    main()
