"""Chat API router."""

import logging
import sqlite3
from uuid import uuid4

from fastapi import HTTPException
from fastapi import APIRouter
from fastapi import status

from app.api.schemas import ChatRequest, ChatResponse
from app.services.chat_session_service import get_chat_session_service

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

CHAT_SESSION_SERVICE = get_chat_session_service()


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
        result = CHAT_SESSION_SERVICE.process_turn(
            message=payload.message,
            session_id=payload.session_id,
            resume_action=payload.resume_action,
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

