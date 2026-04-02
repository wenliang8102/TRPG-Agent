"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.calculation import router as calc_router

app = FastAPI(title="TRPG Agent Backend")
app.include_router(chat_router)
app.include_router(calc_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

