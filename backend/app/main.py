"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api.chat import router as chat_router

app = FastAPI(title="TRPG Agent Backend")
app.include_router(chat_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

