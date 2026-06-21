from victoria.config import settings
from victoria.voice.tts.base import TTSEngine


def get_tts_engine() -> TTSEngine:
    if settings.tts_engine == "elevenlabs":
        from victoria.voice.tts.elevenlabs_tts import ElevenLabsTTSEngine
        return ElevenLabsTTSEngine()
    from victoria.voice.tts.piper_tts import PiperTTSEngine
    return PiperTTSEngine()
