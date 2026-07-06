from abc import ABC, abstractmethod


class TTSEngine(ABC):
    """Abstract TTS engine. Swap engines by changing TTS_ENGINE in config."""

    @abstractmethod
    async def speak(self, text: str) -> None:
        """Synthesize text and play it through the default audio output."""
        ...

    @abstractmethod
    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Synthesize *text* and return (audio_bytes, mime_type).

        Unlike speak(), this returns the audio instead of playing it — used to
        stream speech to the web UI, so it must not touch the local speakers.
        """
        ...

    async def close(self) -> None:
        """Release resources (override if needed)."""
        pass
