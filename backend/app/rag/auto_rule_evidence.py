"""机会型规则书证据评估。"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from time import perf_counter

from app.config.settings import settings
from app.memory.context_assembler import OptionalContextBlock
from app.rag.retriever import TRPGHybridRetriever
from app.services.tools.rag_tools import (
    _format_rule_evidence,
    _is_rule_like_doc,
    _resolve_backend_path,
    _trace_fragment,
)
from app.utils.agent_trace import finish_auto_rule_rag_trace


_auto_retriever: TRPGHybridRetriever | None = None
_active_rag_profile: str | None = None
_timed_out_invocations: set[str] = set()
_timed_out_invocations_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class AutoRuleEvidenceEvaluation:
    """自动规则证据的裁定结果；只有 evidence 存在时才允许注入。"""

    evidence: OptionalContextBlock | None
    injected: bool
    reason: str


def reset_auto_rule_retriever() -> None:
    """测试或配置热切换后重建检索器，避免旧模型配置继续生效。"""
    global _auto_retriever, _active_rag_profile
    _auto_retriever = None
    _active_rag_profile = None


def mark_auto_rule_rag_timed_out(invocation_id: str) -> None:
    """后台线程无法强制中断；标记后完成分支不再重复写 completed trace。"""
    with _timed_out_invocations_lock:
        _timed_out_invocations.add(invocation_id)


def _was_auto_rule_rag_timed_out(invocation_id: str) -> bool:
    with _timed_out_invocations_lock:
        return invocation_id in _timed_out_invocations


def _get_auto_retriever() -> TRPGHybridRetriever:
    global _auto_retriever, _active_rag_profile
    if _auto_retriever is None or _active_rag_profile != settings.rag_profile:
        _auto_retriever = TRPGHybridRetriever(db_path=_resolve_backend_path(settings.rag_core_cn_db_dir))
        _active_rag_profile = settings.rag_profile
    return _auto_retriever


def evaluate_auto_rule_evidence(
    query: str,
    *,
    session_id: str,
    invocation_id: str,
    started_at: str,
) -> AutoRuleEvidenceEvaluation:
    """用语义检索和 rerank 分数决定是否生成本轮可丢弃规则证据。"""
    top_k = settings.auto_rule_rag_top_k
    min_score = settings.auto_rule_rag_min_score
    started_perf = perf_counter()

    try:
        results, diagnostics = _get_auto_retriever().search_with_diagnostics(query, top_k=top_k)
        top_scores = diagnostics.top_scores or []
        best_score = max(top_scores) if top_scores else None
        reason = "injected"
        evidence = None
        injected = False

        if not results:
            reason = diagnostics.failure_reason or "no_results"
        elif best_score is None:
            reason = "no_rerank_score"
        elif best_score < min_score:
            reason = "below_min_score"
        else:
            cleaned_results = [doc for doc in results if _is_rule_like_doc(doc)]
            final_results = (cleaned_results or results)[:3]
            evidence = OptionalContextBlock(
                title="规则证据候选",
                source="auto_rule_rag",
                content=_format_rule_evidence(query, None, final_results),
            )
            injected = True

        returned_fragments = [_trace_fragment(doc) for doc in results[:3]] if results else []
        if not _was_auto_rule_rag_timed_out(invocation_id):
            finish_auto_rule_rag_trace(
                session_id,
                invocation_id=invocation_id,
                started_at=started_at,
                duration_ms=(perf_counter() - started_perf) * 1000,
                query=query,
                top_k=top_k,
                candidate_count=diagnostics.candidate_count,
                bm25_candidate_count=diagnostics.bm25_candidate_count,
                vector_candidate_count=diagnostics.vector_candidate_count,
                rerank_duration_ms=diagnostics.rerank_duration_ms,
                top_scores=top_scores,
                injected=injected,
                reason=reason,
                returned_fragments=returned_fragments,
            )
        return AutoRuleEvidenceEvaluation(evidence=evidence, injected=injected, reason=reason)
    except Exception as exc:
        if not _was_auto_rule_rag_timed_out(invocation_id):
            finish_auto_rule_rag_trace(
                session_id,
                invocation_id=invocation_id,
                started_at=started_at,
                duration_ms=(perf_counter() - started_perf) * 1000,
                query=query,
                top_k=top_k,
                candidate_count=0,
                bm25_candidate_count=0,
                vector_candidate_count=0,
                rerank_duration_ms=0.0,
                top_scores=[],
                injected=False,
                reason=f"error:{exc}",
                returned_fragments=[],
            )
        return AutoRuleEvidenceEvaluation(evidence=None, injected=False, reason=f"error:{exc}")
