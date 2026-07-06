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

    def _synthesize_and_play(self, text: str) -> None:
        """Synchronous synthesis + playback — called in an executor thread."""
        try:
            voice = self._load_voice()
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

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize(text, wav_file)
        buf.seek(0)

        with wave.open(buf, "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())

        audio = np.frombuffer(frames, dtype=np.int16)
        sd.play(audio, samplerate=sample_rate)
        sd.wait()

    async def speak(self, text: str) -> None:
        """Synthesize *text* and play it through the default audio output."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._synthesize_and_play, text)
