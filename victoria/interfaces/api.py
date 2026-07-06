import json
import logging
import os
import tempfile
from functools import lru_cache

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from typing import Optional

from victoria.core.conversation import ConversationManager

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/v1", tags=["chat"])


def get_manager() -> ConversationManager:
    from victoria.main import manager
    return manager


@lru_cache(maxsize=1)
def _tts_engine():
    """Build the configured TTS engine once and reuse it (Piper loads a model)."""
    from victoria.voice.tts.factory import get_tts_engine
    return get_tts_engine()


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
    session = mgr.memory.get_session(session_id)
    if not session or session["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return mgr.memory.get_history(session_id)


@router.get("/profile/{user_id}")
async def get_profile(user_id: str, mgr: ConversationManager = Depends(get_manager)):
    if not mgr.profile_store:
        return {"user_id": user_id, "available": False}
    profile = mgr.profile_store.get(user_id)
    return {
        "user_id": profile.user_id,
        "name": profile.name,
        "communication_style": profile.communication_style,
        "preferences": profile.preferences,
        "topics_of_interest": profile.topics_of_interest,
        "explicit_memories": profile.explicit_memories,
        "updated_at": profile.updated_at,
        "available": True,
    }


# --------------------------------------------------------------------------- #
# Voice: speech-to-text and text-to-speech for the web UI                     #
# --------------------------------------------------------------------------- #

class TTSRequest(BaseModel):
    text: str


@router.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """Transcribe an uploaded audio clip (from the browser mic) to text."""
    from victoria.core.transcription import transcribe_audio

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio upload")

    # Whisper reads from a file path; keep the original extension so ffmpeg can
    # sniff the container (browsers send webm/ogg/mp4 depending on the platform).
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        text = await transcribe_audio(tmp_path)
    except Exception as exc:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return {"text": text}


@router.post("/tts")
async def tts(req: TTSRequest):
    """Synthesize speech for *text* and return the audio bytes."""
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text to speak")
    try:
        audio_bytes, mime = await _tts_engine().synthesize(text)
    except FileNotFoundError as exc:
        # Piper model missing — actionable message rather than a bare 500.
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("TTS failed")
        raise HTTPException(status_code=500, detail=f"TTS failed: {exc}")
    return Response(content=audio_bytes, media_type=mime)
