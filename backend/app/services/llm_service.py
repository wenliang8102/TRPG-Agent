"""LLM service using OpenAI-compatible chat completion API."""

from openai import APITimeoutError, APIConnectionError
from openai import OpenAI

from app.config.settings import settings


class LLMService:
    def __init__(self) -> None:
        provider = settings.llm_provider.strip().lower()
        if provider != "openai":
            raise ValueError(f"Unsupported llm provider: {settings.llm_provider}")

        api_key = settings.llm_api_key.strip()
        if not api_key:
            raise ValueError("Missing LLM API key. Set TRPG_LLM_API_KEY in environment or .env")

        client_kwargs: dict[str, str] = {"api_key": api_key}
        base_url = settings.llm_base_url
        if base_url and base_url.strip():
            client_kwargs["base_url"] = base_url.strip()

        self._client = OpenAI(
            **client_kwargs,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )

    def generate(self, prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=settings.llm_model,
                temperature=settings.llm_temperature,
                messages=[
                    {"role": "system", "content": "You are a helpful TRPG assistant."},
                    {"role": "user", "content": prompt},
                ],
            )
            content = response.choices[0].message.content or ""
            return content.strip()
        except APITimeoutError as exc:
            raise RuntimeError(
                f"LLM request timed out after {settings.llm_timeout_seconds}s. "
                "Please check OPENAI_BASE_URL/network/model service status."
            ) from exc
        except APIConnectionError as exc:
            raise RuntimeError(
                "LLM connection failed. Please verify OPENAI_BASE_URL and network connectivity."
            ) from exc

