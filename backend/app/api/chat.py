"""Chat API router."""

from fastapi import APIRouter

from app.api.schemas import ChatRequest, ChatResponse
from app.graph.builder import build_graph

router = APIRouter(prefix="/api/chat", tags=["chat"])
graph = build_graph()


@router.post("", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    result = graph.invoke({"user_input": payload.message, "messages": []})
    return ChatResponse(reply=result.get("output", ""), plan=result.get("plan"))

