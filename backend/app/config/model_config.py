"""运行时模型配置持久化与热加载。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config.settings import settings


BACKEND_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = BACKEND_DIR / "data" / "model_profiles.json"


ThinkingMode = Literal["enabled", "disabled"]


class ModelEndpointConfig(BaseModel):
    """描述一个 OpenAI 兼容模型端点，供 LangChain 官方客户端直接消费。"""

    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7
    timeout_seconds: float = 60.0
    max_retries: int = 1
    thinking_mode: ThinkingMode = "disabled"


class ModelProfile(BaseModel):
    """一键切换的完整供应商方案，覆盖主模型、压缩模型与检索模型。"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    llm: ModelEndpointConfig
    summary: ModelEndpointConfig
    embedding: ModelEndpointConfig
    rerank: ModelEndpointConfig
    memory_summary_enabled: bool = True


class ModelConfigState(BaseModel):
    """前后端共享的配置文档结构。"""

    active_profile_id: str
    profiles: list[ModelProfile]


def _model_endpoint_from_settings(*, model: str, api_key: str, base_url: str | None, temperature: float, timeout: float, max_retries: int) -> ModelEndpointConfig:
    """从启动配置生成可编辑端点，保持 .env 仍是默认来源。"""
    return ModelEndpointConfig(
        model=model,
        api_key=api_key,
        base_url=base_url or "",
        temperature=temperature,
        timeout_seconds=timeout,
        max_retries=max_retries,
        thinking_mode=settings.llm_thinking_mode or "disabled",
    )


def _default_profile() -> ModelProfile:
    """把现有环境变量投影成第一个方案，避免迁移时丢失原配置。"""
    llm = _model_endpoint_from_settings(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    return ModelProfile(
        id="default",
        name="默认方案",
        llm=llm,
        summary=_model_endpoint_from_settings(
            model=settings.memory_summary_model or settings.llm_model,
            api_key=settings.memory_summary_api_key or settings.llm_api_key,
            base_url=settings.memory_summary_base_url or settings.llm_base_url,
            temperature=settings.memory_summary_temperature,
            timeout=settings.memory_summary_timeout_seconds,
            max_retries=settings.memory_summary_max_retries,
        ).model_copy(update={"thinking_mode": settings.memory_summary_thinking_mode or settings.llm_thinking_mode or "disabled"}),
        embedding=_model_endpoint_from_settings(
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            temperature=0,
            timeout=settings.embedding_timeout_seconds,
            max_retries=settings.embedding_max_retries,
        ),
        rerank=_model_endpoint_from_settings(
            model=settings.rerank_model,
            api_key=settings.rerank_api_key,
            base_url=settings.rerank_base_url,
            temperature=0,
            timeout=settings.rerank_timeout_seconds,
            max_retries=1,
        ),
        memory_summary_enabled=settings.memory_summary_enabled,
    )


def _default_state() -> ModelConfigState:
    profile = _default_profile()
    return ModelConfigState(active_profile_id=profile.id, profiles=[profile])


def _normalize_state(state: ModelConfigState) -> ModelConfigState:
    """修正空方案和失效 active id，让前端永远拿到可切换的配置。"""
    if not state.profiles:
        return _default_state()

    if any(profile.id == state.active_profile_id for profile in state.profiles):
        return state

    return state.model_copy(update={"active_profile_id": state.profiles[0].id})


def load_model_config_state() -> ModelConfigState:
    """读取配置文件；文件不存在时按环境变量生成默认方案。"""
    if not CONFIG_PATH.exists():
        return _default_state()

    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return _normalize_state(ModelConfigState.model_validate(payload))


def save_model_config_state(state: ModelConfigState) -> ModelConfigState:
    """持久化配置文件并返回规范化结果。"""
    normalized = _normalize_state(state)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        normalized.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return normalized


def get_active_profile(state: ModelConfigState | None = None) -> ModelProfile:
    """取当前方案；调用方无需重复处理 active id 查找。"""
    resolved = state or load_model_config_state()
    for profile in resolved.profiles:
        if profile.id == resolved.active_profile_id:
            return profile
    return resolved.profiles[0]


def apply_model_profile(profile: ModelProfile) -> None:
    """把方案热加载到全局 settings，下一次 LLM/检索客户端初始化立即生效。"""
    settings.llm_model = profile.llm.model
    settings.llm_api_key = profile.llm.api_key
    settings.llm_base_url = profile.llm.base_url or None
    settings.llm_temperature = profile.llm.temperature
    settings.llm_timeout_seconds = profile.llm.timeout_seconds
    settings.llm_max_retries = profile.llm.max_retries
    settings.llm_thinking_mode = profile.llm.thinking_mode

    settings.memory_summary_enabled = profile.memory_summary_enabled
    settings.memory_summary_model = profile.summary.model or profile.llm.model
    settings.memory_summary_api_key = profile.summary.api_key or profile.llm.api_key
    settings.memory_summary_base_url = profile.summary.base_url or profile.llm.base_url or None
    settings.memory_summary_thinking_mode = profile.summary.thinking_mode
    settings.memory_summary_temperature = profile.summary.temperature
    settings.memory_summary_timeout_seconds = profile.summary.timeout_seconds
    settings.memory_summary_max_retries = profile.summary.max_retries

    settings.embedding_model = profile.embedding.model
    settings.embedding_api_key = profile.embedding.api_key
    settings.embedding_base_url = profile.embedding.base_url or None
    settings.embedding_timeout_seconds = profile.embedding.timeout_seconds
    settings.embedding_max_retries = profile.embedding.max_retries

    settings.rerank_model = profile.rerank.model
    settings.rerank_api_key = profile.rerank.api_key
    settings.rerank_base_url = profile.rerank.base_url or None
    settings.rerank_timeout_seconds = profile.rerank.timeout_seconds


def load_and_apply_active_model_profile() -> ModelConfigState:
    """应用启动或配置变更后统一入口，避免各处手动拼字段。"""
    state = save_model_config_state(load_model_config_state())
    apply_model_profile(get_active_profile(state))
    return state
