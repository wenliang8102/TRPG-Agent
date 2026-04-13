"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.calculation import router as calc_router
from app.services.chat_session_service import close_chat_session_service


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用退出时统一释放异步持久化资源。"""
    try:
        yield
    finally:
        await close_chat_session_service()


app = FastAPI(title="TRPG Agent Backend", lifespan=lifespan)
app.include_router(chat_router)
app.include_router(calc_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

