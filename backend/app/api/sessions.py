"""会话管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.schemas import CreateSessionRequest, SessionDeleteResponse, SessionListResponse, SessionResponse
from app.services.chat_session_service import delete_chat_session
from app.services.session_store import create_chat_session, list_chat_sessions


router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
async def list_sessions() -> SessionListResponse:
    """列出独立会话，供前端明确选择要恢复的 thread。"""
    return SessionListResponse(sessions=await list_chat_sessions())


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(payload: CreateSessionRequest | None = None) -> SessionResponse:
    """创建空白会话，避免前端复用浏览器里残留的旧 session_id。"""
    title = payload.title if payload is not None else None
    return SessionResponse(**await create_chat_session(title))


@router.delete("/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(session_id: str) -> SessionDeleteResponse:
    """删除会话相关的 checkpoint、派生记忆与 trace 文件。"""
    result = await delete_chat_session(session_id)
    return SessionDeleteResponse(session_id=session_id, **result)
