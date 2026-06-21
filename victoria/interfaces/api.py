import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from victoria.core.conversation import ConversationManager


router = APIRouter(prefix="/v1", tags=["chat"])


def get_manager() -> ConversationManager:
    from victoria.main import manager
    return manager


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    user_id: str = "default"
    backend: Optional[str] = None  # "ollama" | "claude" | None (auto)


class ChatResponse(BaseModel):
    session_id: str
    response: str
    backend: str


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, mgr: ConversationManager = Depends(get_manager)):
    result = await mgr.chat(
        user_message=req.message,
        session_id=req.session_id,
        user_id=req.user_id,
        channel="api",
        force_backend=req.backend,
    )
    return result


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, mgr: ConversationManager = Depends(get_manager)):
    async def event_generator():
        async for event in mgr.stream_chat(
            user_message=req.message,
            session_id=req.session_id,
            user_id=req.user_id,
            channel="api",
            force_backend=req.backend,
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/sessions/{user_id}")
async def list_sessions(user_id: str, mgr: ConversationManager = Depends(get_manager)):
    return mgr.memory.list_sessions(user_id)


@router.get("/sessions/{user_id}/{session_id}/history")
async def get_history(
    user_id: str, session_id: str, mgr: ConversationManager = Depends(get_manager)
):
    return mgr.memory.get_history(session_id)
