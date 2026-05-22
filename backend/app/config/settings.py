"""Application settings placeholder."""

from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "TRPG Agent Backend"
    debug: bool = True
    agent_trace_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("TRPG_AGENT_TRACE_ENABLED"),
    )
    agent_trace_dir: str = Field(
        default="logs/agent_traces",
        validation_alias=AliasChoices("TRPG_AGENT_TRACE_DIR"),
    )
    llm_provider: str = "openai"
    llm_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices("TRPG_LLM_MODEL", "OPENAI_MODEL"),
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("TRPG_LLM_API_KEY", "OPENAI_API_KEY"),
    )
    llm_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("TRPG_LLM_BASE_URL", "OPENAI_BASE_URL"),
    )
    llm_temperature: float = 0.7
    llm_thinking_mode: Optional[Literal["enabled", "disabled"]] = Field(
        default=None,
        validation_alias=AliasChoices("TRPG_LLM_THINKING_MODE", "OPENAI_THINKING_MODE"),
    )
    llm_timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices("TRPG_LLM_TIMEOUT_SECONDS", "OPENAI_TIMEOUT"),
    )
    llm_max_retries: int = Field(
        default=1,
        validation_alias=AliasChoices("TRPG_LLM_MAX_RETRIES", "OPENAI_MAX_RETRIES"),
    )
    memory_summary_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("TRPG_MEMORY_SUMMARY_ENABLED"),
    )
    memory_summary_model: Optional[str] = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices("TRPG_MEMORY_SUMMARY_MODEL"),
    )
    memory_summary_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TRPG_MEMORY_SUMMARY_API_KEY",
            "TRPG_LLM_API_KEY",
            "OPENAI_API_KEY",
        ),
    )
    memory_summary_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "TRPG_MEMORY_SUMMARY_BASE_URL",
            "TRPG_LLM_BASE_URL",
            "OPENAI_BASE_URL",
        ),
    )
    memory_summary_thinking_mode: Optional[Literal["enabled", "disabled"]] = Field(
        default=None,
        validation_alias=AliasChoices(
            "TRPG_MEMORY_SUMMARY_THINKING_MODE",
            "TRPG_LLM_THINKING_MODE",
            "OPENAI_THINKING_MODE",
        ),
    )
    memory_summary_temperature: float = 0.2
    memory_summary_timeout_seconds: float = Field(
        default=20.0,
        validation_alias=AliasChoices("TRPG_MEMORY_SUMMARY_TIMEOUT_SECONDS"),
    )
    memory_summary_max_retries: int = Field(
        default=1,
        validation_alias=AliasChoices("TRPG_MEMORY_SUMMARY_MAX_RETRIES"),
    )
    embedding_model: str = Field(
        default="text-embedding-v3",
        validation_alias=AliasChoices("TRPG_EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL", "EMBEDDING_MODEL"),
    )
    embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TRPG_EMBEDDING_API_KEY",
            "OPENAI_EMBEDDING_API_KEY",
            "EMBEDDING_API_KEY",
        ),
    )
    embedding_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "TRPG_EMBEDDING_BASE_URL",
            "OPENAI_EMBEDDING_BASE_URL",
            "EMBEDDING_BASE_URL",
        ),
    )
    embedding_timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices("TRPG_EMBEDDING_TIMEOUT_SECONDS"),
    )
    embedding_max_retries: int = Field(
        default=1,
        validation_alias=AliasChoices("TRPG_EMBEDDING_MAX_RETRIES"),
    )
    rerank_model: str = Field(
        default="BAAI/bge-reranker-v2-m3",
        validation_alias=AliasChoices("TRPG_RERANK_MODEL", "OPENAI_RERANK_MODEL", "RERANK_MODEL"),
    )
    rerank_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TRPG_RERANK_API_KEY",
            "OPENAI_RERANK_API_KEY",
            "RERANK_API_KEY",
            "TRPG_EMBEDDING_API_KEY",
            "OPENAI_EMBEDDING_API_KEY",
            "EMBEDDING_API_KEY",
        ),
    )
    rerank_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "TRPG_RERANK_BASE_URL",
            "OPENAI_RERANK_BASE_URL",
            "RERANK_BASE_URL",
            "TRPG_EMBEDDING_BASE_URL",
            "OPENAI_EMBEDDING_BASE_URL",
            "EMBEDDING_BASE_URL",
        ),
    )
    rerank_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices("TRPG_RERANK_TIMEOUT_SECONDS"),
    )
    rag_profile: Literal["core_cn"] = Field(
        default="core_cn",
        validation_alias=AliasChoices("TRPG_RAG_PROFILE", "RAG_PROFILE"),
    )
    rag_core_cn_db_dir: str = Field(
        default="data/rag_core_cn_db",
        validation_alias=AliasChoices("TRPG_RAG_CORE_CN_DB_DIR", "RAG_CORE_CN_DB_DIR"),
    )
    auto_rule_rag_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("TRPG_AUTO_RULE_RAG_ENABLED", "AUTO_RULE_RAG_ENABLED"),
    )
    auto_rule_rag_timeout_ms: int = Field(
        default=800,
        validation_alias=AliasChoices("TRPG_AUTO_RULE_RAG_TIMEOUT_MS", "AUTO_RULE_RAG_TIMEOUT_MS"),
    )
    auto_rule_rag_min_score: float = Field(
        default=0.5,
        validation_alias=AliasChoices("TRPG_AUTO_RULE_RAG_MIN_SCORE", "AUTO_RULE_RAG_MIN_SCORE"),
    )
    auto_rule_rag_top_k: int = Field(
        default=6,
        validation_alias=AliasChoices("TRPG_AUTO_RULE_RAG_TOP_K", "AUTO_RULE_RAG_TOP_K"),
    )
    # 本地和正式运行统一使用 PostgreSQL；SQLite 仅作为旧归档读取和显式 fallback。
    database_backend: Literal["sqlite", "postgres"] = Field(
        default="postgres",
        validation_alias=AliasChoices("TRPG_DATABASE_BACKEND", "DATABASE_BACKEND"),
    )
    database_url: Optional[str] = Field(
        default="postgresql://trpg:trpg@localhost:5432/trpg_agent",
        validation_alias=AliasChoices("TRPG_DATABASE_URL", "DATABASE_URL"),
    )
    memory_db_path: str = Field(
        default="data/context_memory.sqlite3",
        validation_alias=AliasChoices("TRPG_MEMORY_DB_PATH", "MEMORY_DB_PATH"),
    )
    graph_recursion_limit: int = Field(
        default=80,
        validation_alias=AliasChoices("TRPG_GRAPH_RECURSION_LIMIT"),
    )

    model_config = SettingsConfigDict(env_prefix="TRPG_", env_file=(ROOT_DIR / ".env", ".env"), extra="ignore")


settings = Settings()

