"""模型配置 API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.config.model_config import (
    ModelConfigState,
    apply_model_profile,
    get_active_profile,
    load_and_apply_active_model_profile,
    load_model_config_state,
    save_model_config_state,
)
from app.services.chat_session_service import reset_cached_adventure_director
from app.services.tools.rag_tools import reset_rules_retriever


router = APIRouter(prefix="/api/model-config", tags=["model-config"])


class ActiveProfileRequest(BaseModel):
    """切换方案只需要提交目标方案 id。"""

    active_profile_id: str


def _apply_state(state: ModelConfigState) -> ModelConfigState:
    """保存并热加载配置，同时清理持有旧客户端的缓存。"""
    saved = save_model_config_state(state)
    apply_model_profile(get_active_profile(saved))
    reset_cached_adventure_director()
    reset_rules_retriever()
    return saved


@router.get("", response_model=ModelConfigState)
async def get_model_config() -> ModelConfigState:
    """返回当前模型配置，并确保运行时已应用 active 方案。"""
    return load_and_apply_active_model_profile()


@router.put("", response_model=ModelConfigState)
async def put_model_config(payload: ModelConfigState) -> ModelConfigState:
    """整份保存模型配置，适合设置页表单直接提交。"""
    return _apply_state(payload)


@router.post("/active", response_model=ModelConfigState)
async def set_active_model_profile(payload: ActiveProfileRequest) -> ModelConfigState:
    """一键切换方案，下一次请求立即使用新的客户端参数。"""
    state = load_model_config_state()
    if not any(profile.id == payload.active_profile_id for profile in state.profiles):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model profile not found.",
        )
    return _apply_state(state.model_copy(update={"active_profile_id": payload.active_profile_id}))
