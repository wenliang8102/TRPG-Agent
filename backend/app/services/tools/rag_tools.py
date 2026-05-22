"""RAG (Retrieval-Augmented Generation) Tools for D&D Rules."""

from __future__ import annotations

import re
from time import perf_counter
from pathlib import Path
from typing import Literal, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.rag.retriever import TRPGHybridRetriever
from app.utils.agent_trace import (
    fail_rule_rag_trace,
    finish_rule_rag_trace,
    start_rule_rag_trace,
)

# Initialize lazily to prevent delay at module import length
_hybrid_retriever = None
_active_rag_profile = None

BACKEND_DIR = Path(__file__).resolve().parents[3]

RuleCategory = Literal[
    "conditions",
    "adventuring",
    "combat",
    "species",
    "classes",
    "equipment",
    "spellcasting",
    "spells",
    "backgrounds",
    "character_creation",
    "character_options",
    "ability_checks",
    "magic_items",
    "treasure",
    "worldbuilding",
    "planes",
    "adventure_design",
    "npcs",
    "adventure_environments",
    "downtime",
    "running_game",
    "rules_options",
    "dmg_general",
    "monsters",
]

NOISE_MARKERS = [
    "关于翻译",
    "免责声明",
    "请勿用作商业用途",
    "整理校对",
    "翻译：",
]

HUD_MARKERS = [
    "当前玩家",
    "状态面板",
    "hud",
    "法术位",
]


def reset_rules_retriever() -> None:
    """模型配置热切换后，下一次规则检索重新创建 embedding/rerank 客户端。"""
    global _hybrid_retriever, _active_rag_profile
    _hybrid_retriever = None
    _active_rag_profile = None


def _resolve_backend_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else BACKEND_DIR / path


def _build_rules_retriever() -> TRPGHybridRetriever:
    """运行时规则检索只使用中文三宝书索引。"""
    return TRPGHybridRetriever(db_path=_resolve_backend_path(settings.rag_core_cn_db_dir))


def _is_noisy_content(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return True
    return any(marker in text for marker in NOISE_MARKERS)


def _is_hud_content(content: str) -> bool:
    text = (content or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in HUD_MARKERS)


def _is_rule_like_doc(doc) -> bool:
    """按语料来源清洗证据，避免旧状态面板规则误伤 PHB 正文章节。"""
    text = (doc.page_content or "").strip()
    if len(text) < 30:
        return False
    if _is_noisy_content(text):
        return False
    if doc.metadata.get("source") in {"phb_cn", "dmg_cn", "mm_cn"}:
        return True
    return not _is_hud_content(text)


def _doc_sub_category(doc) -> str:
    return doc.metadata.get("sub_category") or doc.metadata.get("title", "unknown")


def _clean_rule_excerpt(content: str, limit: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", (content or "").strip())
    chars: list[str] = []
    for char in text:
        code = ord(char)
        if code < 32 and char not in "\n\t\r":
            break
        chars.append(char)
        if len(chars) >= limit:
            break
    return "".join(chars)


def _format_rule_evidence(query: str, effective_filter: Optional[RuleCategory], results: list) -> str:
    evidence_blocks = []
    for idx, doc in enumerate(results[:3], start=1):
        content = (doc.page_content or "").strip()
        if not content:
            continue

        source = doc.metadata.get("source", "Unknown")
        sub_category = _doc_sub_category(doc)
        chapter = doc.metadata.get("chapter") or doc.metadata.get("book", "unknown")
        section = doc.metadata.get("section") or doc.metadata.get("section_path") or doc.metadata.get("title", "unknown")
        page_start = doc.metadata.get("page_start", "unknown")
        page_end = doc.metadata.get("page_end", page_start)
        excerpt = _clean_rule_excerpt(content, 500)
        evidence_blocks.append(
            f"[{idx}] 来源={source} | 章节={chapter} | 小节={section} | "
            f"页码={page_start}-{page_end} | sub_category={sub_category}\n"
            f"原文片段:\n{excerpt}"
        )

    if not evidence_blocks:
        return "规则检索成功，但未返回可用的规则正文片段。"

    joined = "\n\n".join(evidence_blocks)
    return (
        "查询到以下规则册证据（请基于这些原文片段作答）：\n\n"
        f"query={query}\n"
        f"filter_category={effective_filter or 'none'}\n"
        f"命中条目数={len(evidence_blocks)}\n\n"
        f"{joined}"
    )


def _trace_fragment(doc) -> dict:
    metadata = doc.metadata or {}
    excerpt = _clean_rule_excerpt(doc.page_content or "", 220)
    return {
        "source": metadata.get("source", "Unknown"),
        "category": metadata.get("category"),
        "sub_category": _doc_sub_category(doc),
        "chapter": metadata.get("chapter") or metadata.get("book", "unknown"),
        "section": metadata.get("section") or metadata.get("section_path") or metadata.get("title", "unknown"),
        "page_start": metadata.get("page_start", "unknown"),
        "page_end": metadata.get("page_end", metadata.get("page_start", "unknown")),
        "excerpt": excerpt,
    }


def _trace_session_id(config: RunnableConfig | None) -> str:
    configurable = (config or {}).get("configurable", {}) if config else {}
    return str(configurable.get("thread_id") or "detached")


class ConsultRulesInput(BaseModel):
    query: str = Field(
        ...,
        description="需要查询的D&D 5E规则、机制或环境判定的自然语言问题。例如：'树后能提供什么掩护？'、'倒地状态如何影响攻击？'。",
    )
    filter_category: Optional[RuleCategory] = Field(
        default=None,
        description="可选强过滤分类。例如掩护/攻击用 combat，状态用 conditions，法术条目用 spells，职业特性用 classes。",
    )

@tool("consult_rules_handbook", args_schema=ConsultRulesInput)
def consult_rules_handbook(
    query: str,
    filter_category: Optional[RuleCategory] = None,
    config: RunnableConfig = None,
) -> str:
    """
    用于查询 D&D 5E 的基础规则、机制等。
    对于环境是否支持隐蔽、坠落规则、风味判定，必须且只能先调用此工具。
    参数示例：{"query": "树后能提供什么掩护？", "filter_category": "combat"}。
    """
    global _hybrid_retriever, _active_rag_profile

    top_k = 6
    session_id = _trace_session_id(config)
    invocation_id, started_at = start_rule_rag_trace(
        session_id,
        query=query,
        filter_category=filter_category,
        top_k=top_k,
    )
    started_perf = perf_counter()

    try:
        if _hybrid_retriever is None or _active_rag_profile != settings.rag_profile:
            _hybrid_retriever = _build_rules_retriever()
            _active_rag_profile = settings.rag_profile

        results, diagnostics = _hybrid_retriever.search_with_diagnostics(
            query,
            filter_category=filter_category,
            top_k=top_k,
        )
        if not results:
            failure_reason = diagnostics.failure_reason or "no_results"
            fail_rule_rag_trace(
                session_id,
                invocation_id=invocation_id,
                started_at=started_at,
                duration_ms=(perf_counter() - started_perf) * 1000,
                query=query,
                filter_category=filter_category,
                top_k=top_k,
                failure_reason=failure_reason,
            )
            return "未在规则手册中找到相关信息。"

        # 规则工具二次清洗：剔除噪声/HUD/过短片段，降低“有来源无正文”的概率。
        cleaned_results = [doc for doc in results if _is_rule_like_doc(doc)]
        if cleaned_results:
            results = cleaned_results

        final_results = results[:3]
        finish_rule_rag_trace(
            session_id,
            invocation_id=invocation_id,
            started_at=started_at,
            duration_ms=(perf_counter() - started_perf) * 1000,
            query=query,
            filter_category=filter_category,
            top_k=top_k,
            candidate_count=diagnostics.candidate_count,
            bm25_candidate_count=diagnostics.bm25_candidate_count,
            vector_candidate_count=diagnostics.vector_candidate_count,
            rerank_duration_ms=diagnostics.rerank_duration_ms,
            top_scores=diagnostics.top_scores or [],
            returned_fragments=[_trace_fragment(doc) for doc in final_results],
        )
        return _format_rule_evidence(query, filter_category, final_results)
    except Exception as e:
        fail_rule_rag_trace(
            session_id,
            invocation_id=invocation_id,
            started_at=started_at,
            duration_ms=(perf_counter() - started_perf) * 1000,
            query=query,
            filter_category=filter_category,
            top_k=top_k,
            failure_reason=str(e),
        )
        return f"查询规则册时发生错误: {str(e)}"

