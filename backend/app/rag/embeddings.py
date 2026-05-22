from __future__ import annotations

from time import sleep
from typing import Iterable
from urllib.parse import urlsplit

import requests
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from app.config.settings import Settings


class DashScopeMultimodalTextEmbeddings(Embeddings):
    """DashScope 多模态 embedding 的文本适配器，用来兼容 qwen3-vl-embedding 原生接口。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None,
        timeout: float,
        max_retries: int,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.endpoint = f"{_dashscope_origin(base_url)}/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
        self.timeout = timeout
        self.max_retries = max_retries

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for batch in _batched(texts, 4):
            embeddings.extend(self._embed_batch(batch))
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = self._post_with_retry(texts)
        response.raise_for_status()
        payload = response.json()
        items = sorted(payload["output"]["embeddings"], key=lambda item: item["index"])
        return [item["embedding"] for item in items]

    def _post_with_retry(self, texts: list[str]) -> requests.Response:
        payload = {
            "model": self.model,
            "input": {"contents": [{"text": text} for text in texts]},
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code not in {429, 500, 502, 503, 504}:
                    return response
                last_error = requests.HTTPError(response.text, response=response)
            except requests.RequestException as exc:
                last_error = exc
            if attempt < self.max_retries:
                sleep(min(2 ** attempt, 8))
        if last_error is not None:
            raise last_error
        raise RuntimeError("DashScope embedding request failed without response.")


def build_embeddings(settings: Settings) -> Embeddings:
    """集中选择 embedding 后端，避免构建索引和运行检索使用不同协议。"""
    if settings.embedding_model in {
        "qwen3-vl-embedding",
        "tongyi-embedding-vision-plus",
        "tongyi-embedding-vision-flash",
    }:
        return DashScopeMultimodalTextEmbeddings(
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            timeout=settings.embedding_timeout_seconds,
            max_retries=settings.embedding_max_retries,
        )

    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        timeout=settings.embedding_timeout_seconds,
        max_retries=settings.embedding_max_retries,
        check_embedding_ctx_length=False,
        chunk_size=10,
    )


def _dashscope_origin(base_url: str | None) -> str:
    if not base_url:
        return "https://dashscope.aliyuncs.com"
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        return "https://dashscope.aliyuncs.com"
    return f"{parsed.scheme}://{parsed.netloc}"


def _batched(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
