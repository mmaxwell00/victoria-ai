"""Tests for the TTS abstraction layer."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from victoria.voice.tts.base import TTSEngine
from victoria.voice.tts.factory import get_tts_engine
from victoria.voice.tts.piper_tts import PiperTTSEngine
from victoria.voice.tts.elevenlabs_tts import ElevenLabsTTSEngine


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


def test_factory_returns_piper_by_default():
    """When tts_engine is 'piper', the factory returns a PiperTTSEngine."""
    with patch("victoria.voice.tts.factory.settings") as mock_settings:
        mock_settings.tts_engine = "piper"
        engine = get_tts_engine()
    assert isinstance(engine, PiperTTSEngine)


def test_factory_returns_elevenlabs():
    """When tts_engine is 'elevenlabs' and API key is set, factory returns ElevenLabsTTSEngine."""
    with (
        patch("victoria.voice.tts.factory.settings") as mock_factory_settings,
        patch("victoria.voice.tts.elevenlabs_tts.settings") as mock_el_settings,
    ):
        mock_factory_settings.tts_engine = "elevenlabs"
        mock_el_settings.elevenlabs_api_key = "test-key"
        mock_el_settings.elevenlabs_voice_id = "21m00Tcm4TlvDq8ikWAM"
        mock_el_settings.elevenlabs_model = "eleven_monolingual_v1"
        engine = get_tts_engine()
    assert isinstance(engine, ElevenLabsTTSEngine)


# ---------------------------------------------------------------------------
# ElevenLabs unit tests
# ---------------------------------------------------------------------------


def test_elevenlabs_raises_without_key():
    """ElevenLabsTTSEngine.__init__ raises ValueError when API key is empty."""
    with patch("victoria.voice.tts.elevenlabs_tts.settings") as mock_settings:
        mock_settings.elevenlabs_api_key = ""
        with pytest.raises(ValueError, match="ELEVENLABS_API_KEY not set"):
            ElevenLabsTTSEngine()


# ---------------------------------------------------------------------------
# Piper unit tests
# ---------------------------------------------------------------------------


async def test_piper_speak_skips_if_no_model():
    """PiperTTSEngine.speak() logs an error and returns cleanly when the model file is absent."""
    with (
        patch("victoria.voice.tts.piper_tts.settings") as mock_settings,
        patch("victoria.voice.tts.piper_tts.PiperVoice") as mock_piper_voice_global,
    ):
        mock_settings.piper_model_path = "/nonexistent/model.onnx"

        # Simulate _load_voice raising FileNotFoundError (model absent).
        # We patch _load_voice directly to isolate the "skip on missing model" logic
        # without needing piper-tts installed.
        engine = PiperTTSEngine()
        with patch.object(
            engine,
            "_load_voice",
            side_effect=FileNotFoundError("/nonexistent/model.onnx"),
        ):
            # Must not raise — should log and return silently.
            await engine.speak("hello")


def test_piper_caches_loaded_voice(tmp_path, monkeypatch):
    """Regression: the Piper ONNX model must be loaded once, not per utterance."""
    from unittest.mock import MagicMock, patch
    import victoria.voice.tts.piper_tts as piper_mod
    from victoria.voice.tts.piper_tts import PiperTTSEngine

    model_file = tmp_path / "voice.onnx"
    model_file.write_bytes(b"fake")
    monkeypatch.setattr(piper_mod.settings, "piper_model_path", str(model_file))

    fake_voice_cls = MagicMock()
    fake_voice_cls.load.return_value = MagicMock(name="voice")
    with patch.object(piper_mod, "PiperVoice", fake_voice_cls):
        engine = PiperTTSEngine()
        v1 = engine._load_voice()
        v2 = engine._load_voice()

    assert v1 is v2
    fake_voice_cls.load.assert_called_once_with(str(model_file))


async def test_elevenlabs_playback_runs_off_event_loop(monkeypatch):
    """Regression: blocking MP3 playback must not run directly on the loop."""
    import asyncio
    import threading
    from unittest.mock import MagicMock, patch
    import victoria.voice.tts.elevenlabs_tts as el_mod
    from victoria.voice.tts.elevenlabs_tts import ElevenLabsTTSEngine

    monkeypatch.setattr(el_mod.settings, "elevenlabs_api_key", "test-key")
    engine = ElevenLabsTTSEngine()

    loop_thread = threading.current_thread()
    played_on = []

    def fake_play(mp3_bytes):
        played_on.append(threading.current_thread())

    engine._play_mp3 = fake_play

    fake_resp = MagicMock()
    fake_resp.content = b"mp3"
    fake_resp.raise_for_status = MagicMock()

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return fake_resp

    with patch.object(el_mod.httpx, "AsyncClient", FakeClient):
        await engine.speak("hello")

    assert played_on, "playback was never invoked"
    assert played_on[0] is not loop_thread, "playback ran on the event loop thread"
