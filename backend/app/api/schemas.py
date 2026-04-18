"""API request/response schemas."""

from typing import Optional

from pydantic import BaseModel, Field

from app.graph.state import PlayerState

class ChatRequest(BaseModel):
    message: Optional[str] = Field(default=None, description="User input message")
    session_id: Optional[str] = Field(default=None, description="Conversation session id")
    resume_action: Optional[str] = Field(default=None, description="Resume action for interrupted graph (e.g. 'roll_dice')")


class ChatResponse(BaseModel):
    reply: str
    plan: Optional[str] = None
    session_id: str
    pending_action: Optional[dict] = Field(default=None, description="Action required from the user before continuing")
    player: Optional[PlayerState] = Field(default=None, description="Player state")
    combat: Optional[dict] = Field(default=None, description="Combat state")

