"""API request/response schemas."""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., description="User input message")


class ChatResponse(BaseModel):
    reply: str
    plan: str | None = None

