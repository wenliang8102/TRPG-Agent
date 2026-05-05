"""LLM service with LangChain ChatOpenAI and native tool-calling support."""

from typing import Any, Literal

from openai import APITimeoutError, APIConnectionError, BadRequestError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config.settings import settings


LLMMode = Literal["narrative", "combat"]


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

        self._client = self._build_client(
            client_kwargs,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

        summary_model = (settings.memory_summary_model or "").strip() or settings.llm_model
        self._summary_client = self._build_client(
            client_kwargs,
            model=summary_model,
            temperature=settings.memory_summary_temperature,
            timeout=settings.memory_summary_timeout_seconds,
            max_retries=settings.memory_summary_max_retries,
        )

    def _build_client(
        self,
        client_kwargs: dict[str, str],
        *,
        model: str,
        temperature: float,
        timeout: float,
        max_retries: int,
    ) -> ChatOpenAI:
        build_kwargs: dict[str, Any] = {
            **client_kwargs,
            "model": model,
            "temperature": temperature,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        thinking_extra_body = self._thinking_extra_body_for_model(model)
        if thinking_extra_body:
            build_kwargs["extra_body"] = thinking_extra_body

        return ChatOpenAI(
            **build_kwargs,
        )

    # 中文注释：项目更看重工具链路效率与稳定性，默认关闭各家 OpenAI 兼容模型的 thinking。
    def _thinking_extra_body_for_model(self, model: str) -> dict[str, dict[str, str]] | None:
        if settings.llm_thinking_mode == "enabled":
            return None
        return {"thinking": {"type": "disabled"}}

    # 中文注释：先保留单客户端实现，把 mode 作为稳定接口，后续可无痛分模型。
    def _get_client_for_mode(self, mode: LLMMode) -> ChatOpenAI:
        if mode not in {"narrative", "combat"}:
            raise ValueError(f"Unsupported LLM mode: {mode}")
        return self._client

    def invoke_with_tools(
        self,
        messages: list[BaseMessage],
        tools: list,
        system_prompt: str,
        mode: LLMMode = "narrative",
    ) -> AIMessage:
        try:
            prompt_messages = [SystemMessage(content=system_prompt), *messages]
            client = self._get_client_for_mode(mode)

            # 中文注释：LangGraph ToolNode 会并行执行同一条 AIMessage 里的多个工具调用；
            # 空间、战斗等共享状态依赖上一步工具结果，因此要求模型一次只规划一个工具。
            if tools:
                runnable = client.bind_tools(tools, parallel_tool_calls=False)
                response = runnable.invoke(prompt_messages)
            else:
                response = client.invoke(prompt_messages)

            if isinstance(response, AIMessage):
                return response
            return AIMessage(content=str(getattr(response, "content", "") or ""))
        except BadRequestError as exc:
            raise ValueError(f"LLM bad request: {exc}") from exc
        except APITimeoutError as exc:
            raise RuntimeError(
                f"LLM request timed out after {settings.llm_timeout_seconds}s. "
                "Please check OPENAI_BASE_URL/network/model service status."
            ) from exc
        except APIConnectionError as exc:
            raise RuntimeError(
                "LLM connection failed. Please verify OPENAI_BASE_URL and network connectivity."
            ) from exc

    # 中文注释：记忆摘要必须走无工具、低温度的独立调用，避免被主 agent 的工具规划噪声污染。
    def invoke_summary(self, summary_input: str, *, system_prompt: str) -> str:
        try:
            response = self._summary_client.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=summary_input)]
            )
            return self._message_content_to_text(getattr(response, "content", "")).strip()
        except BadRequestError as exc:
            raise ValueError(f"LLM summary bad request: {exc}") from exc
        except APITimeoutError as exc:
            raise RuntimeError(
                f"LLM summary request timed out after {settings.memory_summary_timeout_seconds}s. "
                "Please check OPENAI_BASE_URL/network/model service status."
            ) from exc
        except APIConnectionError as exc:
            raise RuntimeError(
                "LLM summary connection failed. Please verify OPENAI_BASE_URL and network connectivity."
            ) from exc

    def _message_content_to_text(self, content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)

