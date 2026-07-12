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
    model: Optional[str] = None  # which local model answered (docker/ollama turns)


# Backends that run a local Docker Model Runner / Ollama model — the only ones
# for which reporting the specific model id is meaningful.
_LOCAL_BACKENDS = {"docker", "ollama"}


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, mgr: ConversationManager = Depends(get_manager)):
    result = await mgr.chat(
        user_message=req.message,
        session_id=req.session_id,
        user_id=req.user_id,
        channel="api",
        force_backend=req.backend,
    )
    if result.get("backend") in _LOCAL_BACKENDS:
        result["model"] = getattr(mgr.router, "_last_local_model", "") or None
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
            if event.get("done") and event.get("backend") in _LOCAL_BACKENDS:
                event["model"] = getattr(mgr.router, "_last_local_model", "") or None
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
        "preferred_address": profile.preferred_address,
        "communication_style": profile.communication_style,
        "preferences": profile.preferences,
        "topics_of_interest": profile.topics_of_interest,
        "explicit_memories": profile.explicit_memories,
        "onboarded": profile.onboarded,
        "updated_at": profile.updated_at,
        "available": True,
    }


class OnboardRequest(BaseModel):
    name: str = ""
    preferred_address: str = ""


@router.post("/profile/{user_id}/onboard")
async def onboard(
    user_id: str, req: OnboardRequest, mgr: ConversationManager = Depends(get_manager)
):
    """Record the first-run identity (name + how Victoria should address them)."""
    if not mgr.profile_store:
        raise HTTPException(status_code=503, detail="Profile store unavailable")
    profile = mgr.profile_store.onboard(user_id, req.name, req.preferred_address)
    return {
        "user_id": profile.user_id,
        "name": profile.name,
        "preferred_address": profile.preferred_address,
        "onboarded": profile.onboarded,
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
        logger.info("Transcribed %d bytes (%s) → %r", len(data), audio.content_type, text)
    except Exception as exc:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return {"text": text}


class SecretRequest(BaseModel):
    name: str
    value: str


@router.get("/vault")
async def vault_list():
    """List stored secret NAMES only — values are never returned."""
    from victoria.vault.store import get_vault
    return {"names": get_vault().names()}


@router.post("/vault")
async def vault_set(req: SecretRequest):
    """Store (insert/replace) a secret. The value is written straight to the
    encrypted vault and never echoed back or logged."""
    from victoria.vault.store import get_vault
    name = req.name.strip()
    if not name or not req.value:
        raise HTTPException(status_code=400, detail="name and value are required")
    try:
        get_vault().set(name, req.value)
    except Exception as exc:
        logger.exception("Vault store failed")
        raise HTTPException(status_code=500, detail=f"Could not store secret: {exc}")
    return {"ok": True, "name": name, "names": get_vault().names()}


@router.delete("/vault/{name}")
async def vault_delete(name: str):
    from victoria.vault.store import get_vault
    removed = get_vault().delete(name)
    return {"ok": removed, "names": get_vault().names()}


class ModelSelectRequest(BaseModel):
    model: str


def _total_ram_gb() -> float:
    """Total physical RAM in GB (macOS). 0.0 if it can't be read."""
    import subprocess
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                             capture_output=True, text=True, timeout=5)
        return round(int(out.stdout.strip()) / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _recommend_model(models: list, ram_gb: float):
    """Biggest model whose weights fit ~55% of RAM; else the smallest available."""
    sized = [m for m in models if m.get("size_gib")]
    if not sized:
        return models[0]["id"] if models else None
    # Prefer the biggest that fits the budget; on a size tie, the larger context.
    def rank(m):
        return (m["size_gib"], m.get("context") or 0)
    if ram_gb:
        budget = ram_gb * 0.55
        fits = [m for m in sized if m["size_gib"] <= budget]
        if fits:
            return max(fits, key=rank)["id"]
    return min(sized, key=lambda m: m["size_gib"])["id"]


def _persist_env(key: str, value: str, path: str = ".env") -> None:
    """Upsert KEY=value in the .env file without disturbing other lines."""
    p = os.path.abspath(path)
    lines = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    out, found = [], False
    for ln in lines:
        if ln.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


@router.get("/models")
async def list_models(mgr: ConversationManager = Depends(get_manager)):
    """Local models the Model Runner has pulled + the active one + a RAM-based rec."""
    from victoria.config import settings
    models = await mgr.router.available_models()
    ram = _total_ram_gb()
    return {
        "models": models,
        "active": settings.model_runner_model,
        "recommended": _recommend_model(models, ram),
        "ram_gb": ram,
    }


@router.post("/models/select")
async def select_model(req: ModelSelectRequest, mgr: ConversationManager = Depends(get_manager)):
    """Switch the active local model at runtime (and persist to .env)."""
    from victoria.config import settings
    models = await mgr.router.available_models()
    ids = {m["id"] for m in models}
    if req.model not in ids:
        raise HTTPException(status_code=400,
                            detail=f"Model '{req.model}' is not available. Pull it with `docker model pull`.")
    settings.model_runner_model = req.model      # takes effect on the next message
    _persist_env("MODEL_RUNNER_MODEL", req.model)
    logger.info("Local model switched to %s", req.model)
    return {"ok": True, "active": req.model}


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
