"""Application settings placeholder."""

from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TRPG Agent Backend"
    debug: bool = True
    llm_provider: str = "openai"
    llm_model: str = Field(
        default="gpt-4o-mini",
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
    llm_timeout_seconds: float = Field(
        default=20.0,
        validation_alias=AliasChoices("TRPG_LLM_TIMEOUT_SECONDS", "OPENAI_TIMEOUT"),
    )

    model_config = SettingsConfigDict(env_prefix="TRPG_", env_file=".env", extra="ignore")


settings = Settings()

