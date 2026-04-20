"""Chat API router."""

import json
import logging
import sqlite3
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, Query
from fastapi import APIRouter
from fastapi import status
from fastapi.responses import StreamingResponse

from app.api.schemas import ChatRequest, ChatResponse
from app.services.chat_session_service import get_chat_session_service

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

CHAT_SESSION_SERVICE = None


async def _resolve_chat_session_service():
    """允许测试替换全局 service，同时保持生产环境延迟初始化。"""
    if CHAT_SESSION_SERVICE is not None:
        return CHAT_SESSION_SERVICE
    return await get_chat_session_service()


def _raise_chat_http_error(status_code: int, code: str, message: str, exc: Exception) -> None:
    """统一包装错误信息，确保前端拿到可读的失败原因与 request_id。"""
    request_id = str(uuid4())
    logger.exception("chat failed request_id=%s code=%s", request_id, code)
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
            "request_id": request_id,
        },
    ) from exc


@router.post("", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    try:
        service = await _resolve_chat_session_service()
        result = await service.process_turn(
            message=payload.message,
            session_id=payload.session_id,
            resume_action=payload.resume_action,
            reaction_response=payload.reaction_response,
        )

        return ChatResponse(
            reply=result.get("reply", ""),
            plan=result.get("plan"),
            session_id=result.get("session_id", ""),
            pending_action=result.get("pending_action"),
            player=result.get("player"),
            combat=result.get("combat"),
        )
    except sqlite3.Error as exc:
        _raise_chat_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="memory_unavailable",
            message="Conversation memory storage is unavailable.",
            exc=exc,
        )
    except ValueError as exc:
        _raise_chat_http_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_request",
            message=str(exc),
            exc=exc,
        )
    except RuntimeError as exc:
        _raise_chat_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="upstream_unavailable",
            message=str(exc),
            exc=exc,
        )
    except Exception as exc:
        _raise_chat_http_error(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="unexpected_error",
            message=f"Unexpected backend error: {type(exc).__name__}: {exc}",
            exc=exc,
        )


# ── SSE 流式端点 ──────────────────────────────────────────────


@router.post("/stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    """SSE 流式推送：每个 graph 节点执行结果作为独立事件发送。"""

    async def event_generator():
        try:
            service = await _resolve_chat_session_service()
            async for event in service.process_turn_stream(
                message=payload.message,
                session_id=payload.session_id,
                resume_action=payload.resume_action,
                reaction_response=payload.reaction_response,
            ):
                yield event
        except Exception as exc:
            logger.exception("SSE stream error: %s", exc)
            error_data = json.dumps({"message": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 历史消息恢复 ──────────────────────────────────────────────


@router.get("/history")
async def chat_history(
    session_id: str = Query(..., description="会话 ID"),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    """从 checkpointer 恢复最近的对话历史。"""
    service = await _resolve_chat_session_service()
    return await service.get_history(session_id, limit)
