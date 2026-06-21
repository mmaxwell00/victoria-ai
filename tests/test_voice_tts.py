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
