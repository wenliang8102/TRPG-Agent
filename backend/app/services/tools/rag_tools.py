"""RAG (Retrieval-Augmented Generation) Tools for D&D Rules."""

from __future__ import annotations

import unicodedata
import re
from typing import Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.rag.retriever import TRPGHybridRetriever

# Initialize lazily to prevent delay at module import length
_hybrid_retriever = None

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
    "hp",
    "ac",
    "法术位",
]

OUT_OF_SCOPE_MARKERS = [
    "法术列表",
    "spell list",
    "spell lists",
    "魔法物品",
    "magic item",
    "magic items",
    "专长",
    "feat",
    "feats",
]

SPELL_DETAIL_HINTS_CN = [
    "伤害",
    "范围",
    "持续时间",
    "施法时间",
    "豁免",
    "命中",
    "效果",
    "具体",
    "数值",
    "几环",
    "环位",
    "多少",
]

SPELL_DETAIL_HINTS_EN = [
    "damage",
    "range",
    "duration",
    "casting time",
    "save",
    "effect",
    "slot",
    "level",
]

GENERAL_CONFIDENCE_THRESHOLD = 0.50
SPELL_DETAIL_CONFIDENCE_THRESHOLD = 0.75

OUT_OF_SCOPE_RESPONSE = "未在规则手册中找到相关信息。当前规则语料未收录法术详情、魔法物品和专长条目。"


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


def _is_rule_like_content(content: str) -> bool:
    text = (content or "").strip()
    if len(text) < 30:
        return False
    return not _is_noisy_content(text) and not _is_hud_content(text)


def _extract_query_tokens(query: str) -> set[str]:
    q = (query or "").lower()
    tokens: set[str] = set()
    pairs = [
        ("blinded", "目盲"),
        ("charmed", "魅惑"),
        ("prone", "倒地"),
        ("invisible", "隐形"),
        ("incapacitated", "失能"),
        ("falling", "坠落"),
        ("cover", "掩护"),
        ("cover", "掩体"),
        ("rest", "休息"),
    ]
    for en, zh in pairs:
        if en in q or zh in query:
            tokens.update([en, zh])

    # “躲在树后”通常在规则语义上指向掩护（cover），这里做轻量语义补全。
    if any(marker in query for marker in ["树后", "树干", "树木后"]):
        tokens.update(["cover", "掩护", "掩体", "half cover", "total cover"])

    return tokens


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").strip().lower()
    return re.sub(r"\s+", "", normalized)


def _extract_confidence_keywords(query: str, query_tokens: set[str]) -> list[str]:
    text = (query or "").strip()
    lowered = text.lower()

    candidates: list[str] = []
    candidates.extend(query_tokens)
    candidates.extend(re.findall(r"魔法[\u4e00-\u9fff]{1,8}", text))
    candidates.extend(re.findall(r"[\u4e00-\u9fff]{2,8}术", text))
    candidates.extend(re.findall(r"[a-z]{4,}(?:\s+[a-z]{4,})?", lowered))

    blocked = {
        "规则",
        "机制",
        "判定",
        "是什么",
        "效果",
        "告诉",
        "怎么",
        "查到",
        "query",
        "what",
        "tell",
        "about",
        "rule",
        "rules",
        "effect",
    }

    dedup: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_text(candidate)
        if len(normalized) < 2:
            continue
        if normalized in blocked:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        dedup.append(candidate)
    return dedup


def _is_spell_detail_intent(query: str) -> bool:
    text = (query or "").strip()
    lowered = text.lower()

    has_spell_context = (
        "法术" in text
        or "spell" in lowered
        or bool(re.search(r"[\u4e00-\u9fff]{2,8}术", text))
        or bool(re.search(r"魔法[\u4e00-\u9fff]{1,8}", text))
    )
    has_en_detail = any(marker in lowered for marker in SPELL_DETAIL_HINTS_EN)
    has_cn_detail = any(marker in text for marker in SPELL_DETAIL_HINTS_CN)

    return has_spell_context and (has_cn_detail or has_en_detail)


def _is_out_of_scope_query(query: str) -> bool:
    text = (query or "").strip()
    lowered = text.lower()

    if any(marker in text for marker in ["法术列表", "魔法物品", "专长"]):
        return True
    if any(marker in lowered for marker in OUT_OF_SCOPE_MARKERS):
        return True
    if re.search(r"\bfeats?\b", lowered):
        return True
    return False


def _compute_doc_confidence(
    query_keywords: list[str],
    query_tokens: set[str],
    content: str,
    sub_category: str,
    category: str,
    effective_filter: Optional[Literal["conditions", "adventuring", "combat"]],
) -> float:
    if not _is_rule_like_content(content):
        return 0.0

    normalized_content = _normalize_text(content)
    if not normalized_content:
        return 0.0

    score = 0.40

    if query_keywords:
        hits = 0
        for keyword in query_keywords:
            if _normalize_text(keyword) in normalized_content:
                hits += 1
        coverage = hits / len(query_keywords)
        score += 0.35 * coverage

    if query_tokens and _score_doc_for_query(content, sub_category, query_tokens) > 0:
        score += 0.15

    if (sub_category or "unknown") != "unknown":
        score += 0.10

    if effective_filter and category == effective_filter:
        score += 0.10

    return min(score, 1.0)


def _looks_like_spell_entry(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    has_casting = "施法时间" in text or "casting time" in text.lower()
    has_range = "施法距离" in text or "range" in text.lower()
    has_components = "法术成分" in text or "components" in text.lower()
    return has_casting and has_range and has_components


def _infer_effective_filter_category(
    query: str,
    requested_filter: Optional[Literal["conditions", "adventuring", "combat"]],
) -> Optional[Literal["conditions", "adventuring", "combat"]]:
    if requested_filter is not None:
        return requested_filter

    q = (query or "").lower()
    # 掩护类问题默认走 combat 域，避免被“躲藏/遮蔽”噪声片段带偏。
    if (
        "cover" in q
        or "掩护" in query
        or "掩体" in query
        or any(marker in query for marker in ["树后", "树干", "树木后"])
    ):
        return "combat"

    return None


def _score_doc_for_query(content: str, sub_category: str, query_tokens: set[str]) -> int:
    score = 0
    text = (content or "").lower()
    sub = (sub_category or "").lower()

    for token in query_tokens:
        token_lower = token.lower()
        if token_lower in text:
            score += 2
        if token_lower in sub:
            score += 3

    return score

class ConsultRulesInput(BaseModel):
    query: str = Field(
        ...,
        description="需要查询的D&D 5E规则、机制或环境判定的自然语言问题。",
    )
    filter_category: Optional[Literal["conditions", "adventuring", "combat"]] = Field(
        default=None,
        description="可选的强过滤分类，仅允许 conditions、adventuring 或 combat。",
    )

@tool("consult_rules_handbook", args_schema=ConsultRulesInput)
def consult_rules_handbook(
    query: str,
    filter_category: Optional[Literal["conditions", "adventuring", "combat"]] = None,
) -> str:
    """
    用于查询 D&D 5E 的基础规则、机制等。
    对于环境是否支持隐蔽、坠落规则、风味判定，必须且只能先调用此工具。
    """
    global _hybrid_retriever

    try:
        # 越界问题直接拒答，避免返回看似相关但实际错误的证据片段。
        if _is_out_of_scope_query(query):
            return OUT_OF_SCOPE_RESPONSE

        if _hybrid_retriever is None:
            _hybrid_retriever = TRPGHybridRetriever()

        effective_filter = _infer_effective_filter_category(query, filter_category)
        query_tokens = _extract_query_tokens(query)
        query_keywords = _extract_confidence_keywords(query, query_tokens)
        spell_detail_intent = _is_spell_detail_intent(query)

        retrieval_query = query
        # 只要能抽到中文规则词，就优先按中文词检索，降低长句噪声与英文改写带来的偏移。
        zh_tokens = sorted(token for token in query_tokens if re.search(r"[\u4e00-\u9fff]", token))
        if zh_tokens:
            retrieval_query = " ".join(zh_tokens)

        results = _hybrid_retriever.search(
            retrieval_query,
            filter_category=effective_filter,
            top_k=6,
        )
        if not results:
            if spell_detail_intent:
                return OUT_OF_SCOPE_RESPONSE
            return "未在规则手册中找到相关信息。"

        # 规则工具二次清洗：剔除噪声/HUD/过短片段，降低“有来源无正文”的概率。
        cleaned_results = [doc for doc in results if _is_rule_like_content(doc.page_content)]
        if cleaned_results:
            results = cleaned_results

        scored_docs: list[tuple[float, object]] = []
        for doc in results:
            content = (doc.page_content or "").strip()
            if not content:
                continue
            sub_category = doc.metadata.get("sub_category", "unknown")
            category = doc.metadata.get("category", "")
            confidence = _compute_doc_confidence(
                query_keywords=query_keywords,
                query_tokens=query_tokens,
                content=content,
                sub_category=sub_category,
                category=category,
                effective_filter=effective_filter,
            )
            if confidence <= 0:
                continue
            scored_docs.append((confidence, doc))

        if not scored_docs:
            if spell_detail_intent:
                return OUT_OF_SCOPE_RESPONSE
            return "未在规则手册中找到相关信息。"

        scored_docs.sort(key=lambda item: item[0], reverse=True)
        best_confidence = scored_docs[0][0]

        if spell_detail_intent:
            # 法术细节属于当前语料边界外：即便检索到了含术语的文本，也要求有结构化高置信证据。
            has_structured_evidence = any(
                conf >= SPELL_DETAIL_CONFIDENCE_THRESHOLD
                and _looks_like_spell_entry(doc.page_content)
                for conf, doc in scored_docs
            )
            if not has_structured_evidence:
                return OUT_OF_SCOPE_RESPONSE
        elif best_confidence < GENERAL_CONFIDENCE_THRESHOLD:
            return "未在规则手册中找到相关信息。"

        confidence_cutoff = max(
            GENERAL_CONFIDENCE_THRESHOLD,
            best_confidence - 0.15,
        )
        results = [doc for conf, doc in scored_docs if conf >= confidence_cutoff][:3]

        if query_tokens:
            scored_results = [
                (
                    _score_doc_for_query(
                        d.page_content,
                        d.metadata.get("sub_category", "unknown"),
                        query_tokens,
                    ),
                    d,
                )
                for d in results
            ]
            positive_results = [doc for score, doc in scored_results if score > 0]
            if positive_results:
                results = positive_results
            results = sorted(
                results,
                key=lambda d: _score_doc_for_query(
                    d.page_content,
                    d.metadata.get("sub_category", "unknown"),
                    query_tokens,
                ),
                reverse=True,
            )

        evidence_blocks = []
        for idx, doc in enumerate(results[:3], start=1):
            content = (doc.page_content or "").strip()
            if not content:
                continue

            source = doc.metadata.get("source", "Unknown")
            sub_category = doc.metadata.get("sub_category", "unknown")
            excerpt = re.sub(r"\n{3,}", "\n\n", content[:500])
            evidence_blocks.append(
                f"[{idx}] 来源={source} | sub_category={sub_category}\n"
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
    except Exception as e:
        return f"查询规则册时发生错误: {str(e)}"
