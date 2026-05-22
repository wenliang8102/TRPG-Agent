from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from langchain_core.documents import Document

from app.rag.retriever import TRPGHybridRetriever
from app.services.tools import rag_tools
from app.services.tools.rag_tools import consult_rules_handbook
from app.utils.agent_trace import load_trace_events


class _FakeRetriever:
    def __init__(self, docs: list[Document], *, failure_reason: str = "") -> None:
        self.docs = docs
        self.failure_reason = failure_reason

    def search_with_diagnostics(self, query: str, filter_category: str | None = None, top_k: int = 6):
        diagnostics = SimpleNamespace(
            candidate_count=9 if self.docs else 0,
            bm25_candidate_count=6 if self.docs else 0,
            vector_candidate_count=3 if self.docs else 0,
            rerank_duration_ms=12.5,
            top_scores=[0.88, 0.77] if self.docs else [],
            failure_reason=self.failure_reason,
        )
        return self.docs, diagnostics


def test_consult_rules_records_success_trace(tmp_path: Path, monkeypatch):
    doc = Document(
        page_content="目盲生物不能看见，并且任何需要视觉的能力检定自动失败。",
        metadata={
            "source": "phb_cn",
            "category": "conditions",
            "sub_category": "conditions_blinded",
            "chapter": "附录A",
            "section": "目盲",
            "page_start": 290,
        },
    )
    monkeypatch.setattr(rag_tools.settings, "agent_trace_dir", str(tmp_path))
    monkeypatch.setattr(rag_tools.settings, "agent_trace_enabled", True)
    monkeypatch.setattr(rag_tools, "_hybrid_retriever", _FakeRetriever([doc]))
    monkeypatch.setattr(rag_tools, "_active_rag_profile", rag_tools.settings.rag_profile)

    result = consult_rules_handbook.invoke(
        {"query": "目盲是什么效果？", "filter_category": "conditions"},
        config={"configurable": {"thread_id": "session-rag-tool"}},
    )

    events = load_trace_events("session-rag-tool", trace_dir=tmp_path)

    assert "目盲生物不能看见" in result
    assert [event["event_type"] for event in events] == ["rule_rag_started", "rule_rag_completed"]
    assert events[0]["payload"]["query"] == "目盲是什么效果？"
    assert events[0]["payload"]["filter_category"] == "conditions"
    assert events[1]["payload"]["candidate_count"] == 9
    assert events[1]["payload"]["bm25_candidate_count"] == 6
    assert events[1]["payload"]["vector_candidate_count"] == 3
    assert events[1]["payload"]["rerank_duration_ms"] == 12.5
    assert events[1]["payload"]["top_scores"] == [0.88, 0.77]
    assert events[1]["payload"]["returned_fragments"][0]["source"] == "phb_cn"
    assert events[1]["payload"]["returned_fragments"][0]["sub_category"] == "conditions_blinded"


def test_consult_rules_records_no_result_failure_trace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rag_tools.settings, "agent_trace_dir", str(tmp_path))
    monkeypatch.setattr(rag_tools.settings, "agent_trace_enabled", True)
    monkeypatch.setattr(rag_tools, "_hybrid_retriever", _FakeRetriever([], failure_reason="No candidates"))
    monkeypatch.setattr(rag_tools, "_active_rag_profile", rag_tools.settings.rag_profile)

    result = consult_rules_handbook.invoke(
        {"query": "不存在的规则？"},
        config={"configurable": {"thread_id": "session-rag-empty"}},
    )

    events = load_trace_events("session-rag-empty", trace_dir=tmp_path)

    assert result == "未在规则手册中找到相关信息。"
    assert [event["event_type"] for event in events] == ["rule_rag_started", "rule_rag_failed"]
    assert events[1]["payload"]["failure_reason"] == "No candidates"


def test_retriever_search_with_diagnostics_preserves_search_results():
    docs = [
        Document(
            page_content="掩护规则：半掩护提供 AC +2。",
            metadata={"category": "combat", "title": "掩护"},
        )
    ]
    retriever = TRPGHybridRetriever(bm25_documents=docs)

    with mock.patch.object(retriever, "_candidate_documents", return_value=docs), mock.patch.object(
        retriever,
        "_rerank_with_scores",
        return_value=[],
    ):
        results, diagnostics = retriever.search_with_diagnostics("树后有掩护吗", filter_category="combat", top_k=3)
        legacy_results = retriever.search("树后有掩护吗", filter_category="combat", top_k=3)

    assert results == docs
    assert legacy_results == docs
    assert diagnostics.candidate_count == 1
    assert diagnostics.top_scores == []
    assert diagnostics.rerank_duration_ms >= 0
