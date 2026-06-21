import asyncio
import logging
from functools import lru_cache
from victoria.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_model():
    from faster_whisper import WhisperModel
    logger.info("Loading Whisper model '%s'…", settings.whisper_model)
    return WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")


async def transcribe_audio(file_path: str) -> str:
    """Transcribe an audio file (OGG, WAV, MP3, etc.) and return the text."""
    loop = asyncio.get_event_loop()

    def _run():
        model = _load_model()
        segments, _ = model.transcribe(file_path, beam_size=5)
        return " ".join(seg.text for seg in segments).strip()

    return await loop.run_in_executor(None, _run)
