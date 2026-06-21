from abc import ABC, abstractmethod


class TTSEngine(ABC):
    """Abstract TTS engine. Swap engines by changing TTS_ENGINE in config."""

    @abstractmethod
    async def speak(self, text: str) -> None:
        """Synthesize text and play it through the default audio output."""
        ...

    async def close(self) -> None:
        """Release resources (override if needed)."""
        pass
