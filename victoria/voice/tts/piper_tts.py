import asyncio
import io
import logging
import wave

from victoria.config import settings
from victoria.voice.tts.base import TTSEngine

logger = logging.getLogger(__name__)

# Lazy-loaded on first call to avoid breaking imports when piper-tts is absent.
PiperVoice = None


class PiperTTSEngine(TTSEngine):
    """TTS engine backed by local Piper (free, runs entirely on-device)."""

    def __init__(self) -> None:
        self._voice = None

    def _load_voice(self):
        """Import and instantiate PiperVoice, loading model from settings.

        The loaded voice is cached on the instance — loading the ONNX model
        from disk on every utterance adds seconds of latency.
        """
        if self._voice is not None:
            return self._voice

        global PiperVoice
        if PiperVoice is None:
            from piper.voice import PiperVoice as _PiperVoice  # noqa: PLC0415
            PiperVoice = _PiperVoice

        import os  # noqa: PLC0415
        model_path = settings.piper_model_path
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Piper model not found: {model_path}")

        self._voice = PiperVoice.load(model_path)
        return self._voice

    def _synthesize_wav_bytes(self, text: str) -> bytes:
        """Synchronous synthesis → WAV bytes. Called in an executor thread."""
        voice = self._load_voice()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            # synthesize_wav writes a proper WAV header (channels/rate/width);
            # the older synthesize(text, wav_file) no longer does in piper 1.x.
            voice.synthesize_wav(text, wav_file)
        return buf.getvalue()

    def _synthesize_and_play(self, text: str) -> None:
        """Synchronous synthesis + playback — called in an executor thread."""
        try:
            wav_bytes = self._synthesize_wav_bytes(text)
        except FileNotFoundError as exc:
            logger.error(
                "Piper model missing — skipping TTS playback. %s\n"
                "Download a model from https://github.com/rhasspy/piper/releases "
                "and set PIPER_MODEL_PATH in .env.",
                exc,
            )
            return

        import numpy as np  # noqa: PLC0415  # lazy — avoids hard dep at import time
        import sounddevice as sd  # noqa: PLC0415

        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())

        audio = np.frombuffer(frames, dtype=np.int16)
        sd.play(audio, samplerate=sample_rate)
        sd.wait()

    async def speak(self, text: str) -> None:
        """Synthesize *text* and play it through the default audio output."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._synthesize_and_play, text)

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Synthesize *text* to WAV bytes (no playback — for the web UI)."""
        loop = asyncio.get_running_loop()
        wav_bytes = await loop.run_in_executor(None, self._synthesize_wav_bytes, text)
        return wav_bytes, "audio/wav"
