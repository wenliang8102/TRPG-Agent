"""API request/response schemas."""

from typing import Optional

from pydantic import BaseModel, Field

from app.adventures.models import AdventureState
from app.graph.state import PlayerState

class ChatRequest(BaseModel):
    message: Optional[str] = Field(default=None, description="User input message")
    session_id: Optional[str] = Field(default=None, description="Conversation session id")
    resume_action: Optional[str] = Field(default=None, description="Resume action for interrupted graph (e.g. 'roll_dice')")
    reaction_response: Optional[dict] = Field(default=None, description="Structured reaction choice payload for pending combat reactions")


class EndCombatTurnRequest(BaseModel):
    session_id: str = Field(description="Conversation session id")
    actor_id: str = Field(description="Current player-controlled combat actor id")


class ChatResponse(BaseModel):
    reply: str
    plan: Optional[str] = None
    session_id: str
    pending_action: Optional[dict] = Field(default=None, description="Action required from the user before continuing")
    player: Optional[PlayerState] = Field(default=None, description="Player state")
    combat: Optional[dict] = Field(default=None, description="Combat state")
    space: Optional[dict] = Field(default=None, description="Planar space state")
    scene_units: Optional[dict] = Field(default=None, description="Current scene unit pool")
    dead_units: Optional[dict] = Field(default=None, description="Dead unit archive")
    departed_units: Optional[dict] = Field(default=None, description="Departed non-dead unit archive")
    adventure: Optional[AdventureState] = Field(default=None, description="Adventure module progress")


class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(default=None, description="Optional session title")


class SessionResponse(BaseModel):
    id: str
    title: str
    preview: str = ""
    messageCount: int
    createdAt: int
    lastMessageAt: int


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]


class SessionDeleteResponse(BaseModel):
    session_id: str
    deletedRows: int
    deletedTraceFiles: int
