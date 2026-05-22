from pathlib import Path
from types import SimpleNamespace

from langchain_core.documents import Document

from app.rag import auto_rule_evidence
from app.rag.auto_rule_evidence import evaluate_auto_rule_evidence
from app.utils.agent_trace import load_trace_events, start_auto_rule_rag_trace


class _FakeRetriever:
    def __init__(self, docs: list[Document], top_scores: list[float]) -> None:
        self.docs = docs
        self.top_scores = top_scores

    def search_with_diagnostics(self, query: str, top_k: int = 6):
        diagnostics = SimpleNamespace(
            candidate_count=len(self.docs),
            bm25_candidate_count=len(self.docs),
            vector_candidate_count=0,
            rerank_duration_ms=12.0,
            top_scores=self.top_scores,
            failure_reason="",
        )
        return self.docs, diagnostics


def test_auto_rule_evidence_injects_when_rerank_score_is_high(tmp_path: Path, monkeypatch):
    doc = Document(
        page_content="半身掩护提供 AC 和敏捷豁免加值。",
        metadata={"source": "phb_cn", "category": "combat", "section": "掩护", "page_start": 196},
    )
    monkeypatch.setattr(auto_rule_evidence.settings, "agent_trace_dir", str(tmp_path))
    monkeypatch.setattr(auto_rule_evidence.settings, "agent_trace_enabled", True)
    monkeypatch.setattr(auto_rule_evidence.settings, "auto_rule_rag_top_k", 6)
    monkeypatch.setattr(auto_rule_evidence.settings, "auto_rule_rag_min_score", 0.5)
    monkeypatch.setattr(auto_rule_evidence, "_auto_retriever", _FakeRetriever([doc], [0.86]))
    monkeypatch.setattr(auto_rule_evidence, "_active_rag_profile", auto_rule_evidence.settings.rag_profile)
    invocation_id, started_at = start_auto_rule_rag_trace(
        "session-auto",
        query="半身掩护有什么效果？",
        top_k=6,
        timeout_ms=800,
        min_score=0.5,
        trace_dir=tmp_path,
    )

    evaluation = evaluate_auto_rule_evidence(
        "半身掩护有什么效果？",
        session_id="session-auto",
        invocation_id=invocation_id,
        started_at=started_at,
    )

    events = load_trace_events("session-auto", trace_dir=tmp_path)

    assert evaluation.injected is True
    assert evaluation.evidence is not None
    assert evaluation.evidence.source == "auto_rule_rag"
    assert "半身掩护提供 AC" in evaluation.evidence.content
    assert events[-1]["event_type"] == "rule_auto_rag_completed"
    assert events[-1]["payload"]["injected"] is True
    assert events[-1]["payload"]["top_scores"] == [0.86]


def test_auto_rule_evidence_fail_closed_without_rerank_score(tmp_path: Path, monkeypatch):
    doc = Document(
        page_content="目盲生物不能看见。",
        metadata={"source": "phb_cn", "category": "conditions", "section": "目盲"},
    )
    monkeypatch.setattr(auto_rule_evidence.settings, "agent_trace_dir", str(tmp_path))
    monkeypatch.setattr(auto_rule_evidence.settings, "agent_trace_enabled", True)
    monkeypatch.setattr(auto_rule_evidence.settings, "auto_rule_rag_top_k", 6)
    monkeypatch.setattr(auto_rule_evidence.settings, "auto_rule_rag_min_score", 0.5)
    monkeypatch.setattr(auto_rule_evidence, "_auto_retriever", _FakeRetriever([doc], []))
    monkeypatch.setattr(auto_rule_evidence, "_active_rag_profile", auto_rule_evidence.settings.rag_profile)
    invocation_id, started_at = start_auto_rule_rag_trace(
        "session-auto-low",
        query="目盲是什么？",
        top_k=6,
        timeout_ms=800,
        min_score=0.5,
        trace_dir=tmp_path,
    )

    evaluation = evaluate_auto_rule_evidence(
        "目盲是什么？",
        session_id="session-auto-low",
        invocation_id=invocation_id,
        started_at=started_at,
    )

    events = load_trace_events("session-auto-low", trace_dir=tmp_path)

    assert evaluation.injected is False
    assert evaluation.evidence is None
    assert evaluation.reason == "no_rerank_score"
    assert events[-1]["payload"]["injected"] is False
    assert events[-1]["payload"]["reason"] == "no_rerank_score"
