import logging
import re
from time import perf_counter
from typing import List, Optional
import pickle
from pathlib import Path
from dataclasses import dataclass
import jieba
import requests

from langchain_core.documents import Document
from langchain_community.retrievers import BM25Retriever
from langchain_chroma import Chroma

# 引入 Rerank 组件
from app.config.settings import settings
from app.rag.embeddings import build_embeddings

BACKEND_DIR = Path(__file__).resolve().parents[2]
DB_PATH = Path(settings.rag_core_cn_db_dir)
if not DB_PATH.is_absolute():
    DB_PATH = BACKEND_DIR / DB_PATH
BM25_PATH = DB_PATH / "bm25_index.pkl"

logger = logging.getLogger(__name__)

QUERY_SYNONYMS = {
    "准备动作": ["预备"],
    "准备": ["预备"],
    "反制法术": ["法术反制"],
    "邪术师": ["术士"],
    "术士": ["邪术师"],
    "奥法恢复": ["奥术回想"],
    "掩护": ["半身掩护", "四分之三掩护", "全身掩护", "cover"],
    "树后": ["大树", "树干", "掩体"],
    "目盲": ["目盲Blinded", "目盲生物", "攻击检定", "优势", "劣势"],
}

BM25_QUERY_STOPWORDS = {
    "什么",
    "怎么",
    "怎样",
    "多少",
    "分别",
    "时候",
    "可以",
    "如何",
    "是否",
    "一个",
    "进行",
    "影响",
    "状态",
    "目标",
    "别人",
    "敌人",
    "给我",
}


def bm25_tokenize(text: str) -> list[str]:
    """BM25 只保留有检索价值的词，避免玩家口语助词压过规则名。"""
    tokens: list[str] = []
    for token in jieba.lcut(text or ""):
        normalized = token.strip().lower()
        if not normalized or normalized in BM25_QUERY_STOPWORDS:
            continue
        if len(normalized) == 1 and not re.match(r"[a-zA-Z]", normalized):
            continue
        tokens.append(normalized)
    return tokens


@dataclass(slots=True)
class RankedDocument:
    document: Document
    score: float


@dataclass(slots=True)
class RetrievalDiagnostics:
    candidate_count: int = 0
    bm25_candidate_count: int = 0
    vector_candidate_count: int = 0
    rerank_duration_ms: float = 0.0
    top_scores: list[float] | None = None
    failure_reason: str = ""


class TRPGHybridRetriever:
    def __init__(
        self,
        db_path: Optional[Path] = None,
        bm25_path: Optional[Path] = None,
        bm25_documents: Optional[List[Document]] = None,
        candidate_k: int = 30,
    ):
        self.db_path = Path(db_path) if db_path else DB_PATH
        if not self.db_path.is_absolute():
            self.db_path = BACKEND_DIR / self.db_path
        self.bm25_path = Path(bm25_path) if bm25_path else self.db_path / "bm25_index.pkl"
        self.candidate_k = candidate_k

        # 反序列化底层文本并快速重建BM25；若索引缺失则降级到向量检索。
        self.bm25_retriever = None
        if bm25_documents is not None:
            self.bm25_retriever = BM25Retriever.from_documents(
                bm25_documents,
                preprocess_func=bm25_tokenize,
            )
            self.bm25_retriever.k = self.candidate_k
        elif self.bm25_path.exists():
            try:
                with open(self.bm25_path, "rb") as f:
                    bm25_chunks = pickle.load(f)

                self.bm25_retriever = BM25Retriever.from_documents(
                    bm25_chunks,
                    preprocess_func=bm25_tokenize
                )
                self.bm25_retriever.k = self.candidate_k
            except Exception as exc:
                logger.warning(
                    "BM25 retriever unavailable; continue without BM25. reason=%s",
                    exc,
                )
        else:
            logger.warning("BM25 index missing; continue without BM25. path=%s", self.bm25_path)

        # 只读取已经构建好的 Chroma 索引；BM25-only 目录不应触发无意义的 embedding 请求。
        self.embeddings = None
        self.vectorstore = None
        if self._has_vector_index(self.db_path):
            try:
                self.embeddings = build_embeddings(settings)
                self.vectorstore = Chroma(
                    persist_directory=str(self.db_path),
                    embedding_function=self.embeddings,
                )
            except Exception as exc:
                logger.warning(
                    "Vector retriever unavailable; fallback to BM25 only. reason=%s",
                    exc,
                )
        else:
            logger.warning("Vector index missing; fallback to BM25 only. path=%s", self.db_path)

        # Rerank 统一走云端接口，团队成员只需按供应商配置环境变量。
        self.rerank_url = self._build_rerank_url(settings.rerank_base_url)
        self.rerank_api_key = settings.rerank_api_key
        self.rerank_model = settings.rerank_model
        self.rerank_timeout_seconds = settings.rerank_timeout_seconds

    def _build_vector_retriever(self, filter_category: Optional[str], top_k: int = 30):
        if self.vectorstore is None:
            return None

        search_kwargs = {"k": top_k}
        if filter_category:
            search_kwargs["filter"] = {"category": filter_category}
        return self.vectorstore.as_retriever(search_kwargs=search_kwargs)

    @staticmethod
    def _build_rerank_url(base_url: Optional[str]) -> Optional[str]:
        if not base_url:
            return None
        if "compatible-api" in base_url:
            return f"{base_url.rstrip('/')}/reranks"
        return f"{base_url.rstrip('/')}/rerank"

    @staticmethod
    def _has_vector_index(db_path: Path) -> bool:
        return (
            (db_path / "VECTOR_READY").exists()
            and (db_path / "chroma.sqlite3").exists()
            and any(item.is_dir() for item in db_path.iterdir())
        )

    @staticmethod
    def _apply_category_filter(results: List[Document], filter_category: Optional[str]) -> List[Document]:
        if not filter_category:
            return results
        return [doc for doc in results if doc.metadata.get("category") == filter_category]

    @staticmethod
    def _document_key(doc: Document) -> tuple[str, str, str, str]:
        metadata = doc.metadata
        stable_id = str(
            metadata.get("parent_anchor_id")
            or metadata.get("anchor_id")
            or metadata.get("section_path")
            or metadata.get("section")
            or ""
        )
        chunk_index = str(metadata.get("chunk_index", ""))
        page_start = str(metadata.get("page_start", ""))
        content = re.sub(r"\s+", "", doc.page_content or "")[:120]
        return stable_id, chunk_index, page_start, content

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        text = query or ""
        terms: list[str] = []
        terms.extend(re.findall(r"[a-zA-Z][a-zA-Z ]{2,}", text.lower()))
        terms.extend(
            token
            for token in jieba.lcut(text)
            if len(token) >= 2 and re.search(r"[\u4e00-\u9fff]", token)
        )
        blocked = {"什么", "怎么", "多少", "分别", "时候", "可以", "如何", "是否", "一个", "进行"}
        deduped: list[str] = []
        seen: set[str] = set()
        for term in terms:
            normalized = term.lower().strip()
            if normalized in blocked or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(term)
        return deduped

    @staticmethod
    def _expanded_query_text(query: str) -> str:
        """补齐常见玩家译名，解决用户用语和书内译名不一致的问题。"""
        additions: list[str] = []
        for source, targets in QUERY_SYNONYMS.items():
            if source in query:
                additions.extend(targets)
        return " ".join([query, *additions]).strip()

    @staticmethod
    def _leading_query_term(query: str) -> str:
        match = re.match(r"([\u4e00-\u9fff]{2,10})(?:的|是|会|能|在|面对|给|把|触发|什么时候)", query or "")
        return match.group(1) if match else ""

    @staticmethod
    def _leading_query_relation(query: str) -> str:
        match = re.match(r"[\u4e00-\u9fff]{2,10}(的|是|会|能|在|面对|给|把|触发|什么时候)", query or "")
        return match.group(1) if match else ""

    @staticmethod
    def _title_zh(title: str) -> str:
        match = re.match(r"([\u4e00-\u9fff·（）]+)", (title or "").strip())
        return match.group(1) if match else ""

    def _metadata_boost(
        self,
        query: str,
        doc: Document,
        filter_category: Optional[str],
    ) -> int:
        title = str(doc.metadata.get("title") or doc.metadata.get("section") or "").lower()
        section_path = str(doc.metadata.get("section_path") or "").lower()
        content = (doc.page_content or "").lower()
        expanded_query = self._expanded_query_text(query)
        title_zh = self._title_zh(str(doc.metadata.get("title") or doc.metadata.get("section") or ""))
        leading_term = self._leading_query_term(expanded_query)
        score = 0
        if filter_category and doc.metadata.get("category") == filter_category:
            score += 20
        # 查询开头的书名/位面/怪物名常是上下文，后续出现的完整标题短语更接近用户真正要查的小节。
        leading_relation = self._leading_query_relation(expanded_query)
        leading_is_context = leading_relation == "的"
        if title_zh and title_zh in expanded_query:
            if len(title_zh) >= 3 and title_zh != leading_term and leading_is_context:
                score += 110
            else:
                score += 40 if len(title_zh) >= 3 else 12
        if leading_term and leading_term == title_zh and not leading_is_context:
            score += 30
        if leading_term and leading_term in title_zh and not leading_is_context:
            score += 25
        for term in self._query_terms(expanded_query):
            lowered = term.lower()
            if lowered in title:
                score += 12
            elif lowered in section_path:
                score += 4
            if lowered in content:
                score += 3
        return score

    def _dedupe_documents(self, docs: List[Document]) -> List[Document]:
        deduped: list[Document] = []
        seen: set[tuple[str, str, str, str]] = set()
        for doc in docs:
            key = self._document_key(doc)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(doc)
        return deduped

    def _candidate_documents(
        self,
        query: str,
        filter_category: Optional[str],
        diagnostics: Optional[RetrievalDiagnostics] = None,
    ) -> List[Document]:
        candidates: list[Document] = []
        expanded_query = self._expanded_query_text(query)

        if self.bm25_retriever is not None:
            self.bm25_retriever.k = self.candidate_k
            try:
                bm25_candidates = self.bm25_retriever.invoke(expanded_query)
                if diagnostics is not None:
                    diagnostics.bm25_candidate_count = len(bm25_candidates)
                candidates.extend(bm25_candidates)
            except Exception as exc:
                logger.warning("BM25 retrieval failed. reason=%s", exc)

        vector_retriever = self._build_vector_retriever(filter_category, top_k=self.candidate_k)
        if vector_retriever is not None:
            try:
                vector_candidates = vector_retriever.invoke(query)
                if diagnostics is not None:
                    diagnostics.vector_candidate_count = len(vector_candidates)
                candidates.extend(vector_candidates)
            except Exception as exc:
                logger.warning("Vector retrieval failed. reason=%s", exc)
                self.vectorstore = None

        if not candidates:
            raise RuntimeError("No available retriever backend (BM25 and vector are both unavailable or returned no candidates).")

        filtered = self._apply_category_filter(candidates, filter_category)
        deduped = self._dedupe_documents(filtered or candidates)
        if diagnostics is not None:
            diagnostics.candidate_count = len(deduped)
        return deduped

    def search_with_diagnostics(
        self,
        query: str,
        filter_category: Optional[str] = None,
        top_k: int = 3,
    ) -> tuple[List[Document], RetrievalDiagnostics]:
        """返回检索结果和观测数据；工具层用它写 trace，普通调用仍走 search。"""
        diagnostics = RetrievalDiagnostics(top_scores=[])
        try:
            candidates = self._candidate_documents(query, filter_category, diagnostics)
        except Exception as exc:
            logger.warning(
                "Hybrid retrieval failed. reason=%s",
                exc,
            )
            diagnostics.failure_reason = str(exc)
            return [], diagnostics
        diagnostics.candidate_count = len(candidates)

        rerank_top_n = min(max(top_k * 3, top_k), len(candidates))
        rerank_started = perf_counter()
        reranked_with_scores = self._rerank_with_scores(query, candidates, rerank_top_n)
        diagnostics.rerank_duration_ms = round((perf_counter() - rerank_started) * 1000, 3)
        diagnostics.top_scores = [round(item.score, 6) for item in reranked_with_scores[: min(top_k, len(reranked_with_scores))]]
        reranked = [item.document for item in reranked_with_scores[:rerank_top_n]] if reranked_with_scores else candidates[:rerank_top_n]
        metadata_hits = sorted(
            candidates,
            key=lambda doc: self._metadata_boost(query, doc, filter_category),
            reverse=True,
        )[: max(top_k * 2, top_k)]
        ranked_pool = self._dedupe_documents([*reranked, *metadata_hits])
        results = sorted(
            ranked_pool,
            key=lambda doc: self._metadata_boost(query, doc, filter_category),
            reverse=True,
        )[:top_k]
        return results, diagnostics

    def search(self, query: str, filter_category: Optional[str] = None, top_k: int = 3) -> List[Document]:
        results, _ = self.search_with_diagnostics(query, filter_category, top_k)
        return results

    def _rerank(self, query: str, docs: List[Document], top_k: int) -> List[Document]:
        ranked = self._rerank_with_scores(query, docs, top_k)
        if ranked:
            return [item.document for item in ranked[:top_k]]
        return docs[:top_k]

    def _rerank_with_scores(self, query: str, docs: List[Document], top_k: int) -> list[RankedDocument]:
        if not docs or not self.rerank_url or not self.rerank_api_key:
            return []

        try:
            response = requests.post(
                self.rerank_url,
                headers={
                    "Authorization": f"Bearer {self.rerank_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.rerank_model,
                    "query": query,
                    "documents": [doc.page_content for doc in docs],
                    "top_n": min(top_k, len(docs)),
                    "return_documents": False,
                },
                timeout=self.rerank_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            ranked_docs: list[RankedDocument] = []
            for item in payload.get("results", []):
                index = item.get("index", -1)
                if not 0 <= index < len(docs):
                    continue
                score = item.get("relevance_score", item.get("score", 0.0))
                ranked_docs.append(RankedDocument(document=docs[index], score=float(score)))
            return ranked_docs[:top_k]
        except Exception as exc:
            logger.warning("Cloud rerank unavailable; skip scored rerank. reason=%s", exc)
            self.rerank_url = None
            return []

if __name__ == "__main__":
    print("Initializing retriever")
    retriever = TRPGHybridRetriever()
    print("OK, execute search: '我能攻击躲在树后的人吗'")
    results = retriever.search("我能攻击躲在树后的人吗")
    for i, doc in enumerate(results):
        print(f"\n--- Result {i+1} ---")
        print(f"[{doc.metadata.get('category')} / {doc.metadata.get('sub_category')}]")
        print(doc.page_content)
