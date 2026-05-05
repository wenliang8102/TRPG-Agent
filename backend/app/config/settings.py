"""Application settings placeholder."""

from typing import Literal, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        default="qwen3.6-27b",
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
        validation_alias=AliasChoices("TRPG_EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL"),
    )
    embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TRPG_EMBEDDING_API_KEY",
            "OPENAI_EMBEDDING_API_KEY",
        ),
    )
    embedding_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "TRPG_EMBEDDING_BASE_URL",
            "OPENAI_EMBEDDING_BASE_URL",
        ),
    )
    rerank_model: str = Field(
        default="BAAI/bge-reranker-v2-m3",
        validation_alias=AliasChoices("TRPG_RERANK_MODEL", "OPENAI_RERANK_MODEL"),
    )
    rerank_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TRPG_RERANK_API_KEY",
            "OPENAI_RERANK_API_KEY",
            "TRPG_EMBEDDING_API_KEY",
            "OPENAI_EMBEDDING_API_KEY",
        ),
    )
    rerank_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "TRPG_RERANK_BASE_URL",
            "OPENAI_RERANK_BASE_URL",
            "TRPG_EMBEDDING_BASE_URL",
            "OPENAI_EMBEDDING_BASE_URL",
        ),
    )
    rag_db_dir: str = Field(
        default="data/rag_pdf_db",
        validation_alias=AliasChoices("TRPG_RAG_DB_DIR", "RAG_DB_DIR"),
    )
    rag_source_pdf_path: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("TRPG_RAG_SOURCE_PDF_PATH", "RAG_SOURCE_PDF_PATH"),
    )
    memory_db_path: str = Field(
        default="data/context_memory.sqlite3",
        validation_alias=AliasChoices("TRPG_MEMORY_DB_PATH", "MEMORY_DB_PATH"),
    )
    graph_recursion_limit: int = Field(
        default=80,
        validation_alias=AliasChoices("TRPG_GRAPH_RECURSION_LIMIT"),
    )

    model_config = SettingsConfigDict(env_prefix="TRPG_", env_file=".env", extra="ignore")


settings = Settings()

