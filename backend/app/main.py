"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.utils.asyncio_policy import configure_windows_selector_event_loop_policy

configure_windows_selector_event_loop_policy()

from app.api.chat import router as chat_router
from app.api.model_config import router as model_config_router
from app.api.sessions import router as sessions_router
from app.config.model_config import load_and_apply_active_model_profile
from app.services.chat_session_service import close_chat_session_service


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用退出时统一释放异步持久化资源。"""
    load_and_apply_active_model_profile()
    try:
        yield
    finally:
        await close_chat_session_service()


app = FastAPI(title="TRPG Agent Backend", lifespan=lifespan)
app.include_router(chat_router)
app.include_router(model_config_router)
app.include_router(sessions_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

