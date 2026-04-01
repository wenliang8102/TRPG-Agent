"""API request/response schemas."""

from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., description="User input message")


class ChatResponse(BaseModel):
    reply: str
    plan: Optional[str] = None

