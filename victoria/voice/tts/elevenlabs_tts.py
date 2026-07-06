"""ElevenLabs cloud TTS engine.

Required environment variables (set in .env or shell):
    ELEVENLABS_API_KEY   — your ElevenLabs secret key
    ELEVENLABS_VOICE_ID  — voice ID from your ElevenLabs account (default: "21m00Tcm4TlvDq8ikWAM")
    ELEVENLABS_MODEL     — model ID (default: "eleven_monolingual_v1")

Additional dependency:
    pip install soundfile   # for decoding the MP3 response bytes
"""

import asyncio
import io
import logging

import httpx

from victoria.config import settings
from victoria.voice.tts.base import TTSEngine

logger = logging.getLogger(__name__)


class ElevenLabsTTSEngine(TTSEngine):
    """TTS engine backed by the ElevenLabs cloud API."""

    def __init__(self) -> None:
        if not settings.elevenlabs_api_key:
            raise ValueError("ELEVENLABS_API_KEY not set — see .env.example")
        self._api_key = settings.elevenlabs_api_key
        self._voice_id = settings.elevenlabs_voice_id
        self._model = settings.elevenlabs_model

    async def _fetch_mp3(self, text: str) -> bytes:
        """Call the ElevenLabs API and return raw MP3 bytes."""
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}"
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        body = {
            "text": text,
            "model_id": self._model,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response.content

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Synthesize *text* to MP3 bytes (no playback — for the web UI)."""
        return await self._fetch_mp3(text), "audio/mpeg"

    async def speak(self, text: str) -> None:
        """Synthesize *text* via ElevenLabs API and play it through the default audio output."""
        mp3_bytes = await self._fetch_mp3(text)

        # Playback blocks on sd.wait() — run it in an executor so the event
        # loop stays responsive for the duration of the audio.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._play_mp3, mp3_bytes)

    def _play_mp3(self, mp3_bytes: bytes) -> None:
        """Decode MP3 bytes and play through sounddevice."""
        try:
            import soundfile as sf  # pip install soundfile
            import sounddevice as sd
        except ImportError as exc:
            raise ImportError(
                "soundfile is required to play ElevenLabs audio. "
                "Install it with: pip install soundfile"
            ) from exc

        buf = io.BytesIO(mp3_bytes)
        data, sample_rate = sf.read(buf, dtype="int16")
        sd.play(data, samplerate=sample_rate)
        sd.wait()
