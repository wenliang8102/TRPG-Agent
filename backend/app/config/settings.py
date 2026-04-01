"""Application settings placeholder."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TRPG Agent Backend"
    debug: bool = True

    model_config = SettingsConfigDict(env_prefix="TRPG_", env_file=".env", extra="ignore")


settings = Settings()

