import asyncio
import logging
import numpy as np

logger = logging.getLogger(__name__)

CHUNK_DURATION = 0.08  # 80ms — openwakeword expects ~80ms chunks at 16kHz
SAMPLE_RATE = 16000
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION)


class WakeWordDetector:
    """Detects 'hello victoria' using openwakeword.

    Falls back to SimplePhraseDetector if openwakeword is not installed or
    the model can't be loaded — useful for development without a mic.
    """

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self._model = None
        self._available = False
        self._try_load()

    def _try_load(self):
        try:
            from openwakeword.model import Model
            self._model = Model(wakeword_models=["hello_victoria"], inference_framework="onnx")
            self._available = True
            logger.info("Wake word model loaded (hello_victoria)")
        except Exception as e:
            logger.warning("openwakeword unavailable (%s) — using keyboard fallback", e)
            self._available = False

    async def wait_for_wake_word(self) -> None:
        """Block until wake word is detected (or Enter pressed in fallback mode)."""
        if self._available:
            await self._listen_for_wake_word()
        else:
            await self._keyboard_fallback()

    async def _listen_for_wake_word(self) -> None:
        # Stream microphone audio in 80ms chunks, feed to openwakeword
        # When score > self.threshold, return
        import sounddevice as sd
        loop = asyncio.get_event_loop()

        def _blocking_listen():
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16',
                                blocksize=CHUNK_SAMPLES) as stream:
                while True:
                    chunk, _ = stream.read(CHUNK_SAMPLES)
                    chunk_flat = chunk.flatten()
                    prediction = self._model.predict(chunk_flat)
                    scores = list(prediction.values())
                    if scores and max(scores) > self.threshold:
                        return

        await loop.run_in_executor(None, _blocking_listen)

    async def _keyboard_fallback(self) -> None:
        print("\n[Wake word mic unavailable — press Enter to activate Victoria]")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input)


class SimplePhraseDetector:
    """Pure keyboard fallback — for testing without a microphone."""

    async def wait_for_wake_word(self) -> None:
        print("\n[Press Enter to speak to Victoria]")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input)
