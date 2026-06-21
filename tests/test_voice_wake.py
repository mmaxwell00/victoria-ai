"""Tests for victoria/voice/audio.py and victoria/voice/wake_word.py."""
import asyncio
import sys
import types
from unittest.mock import MagicMock, patch, AsyncMock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Stub out sounddevice at module level so audio.py can be imported in CI
# environments that have no PortAudio library.  We replace with a minimal
# fake that exposes only what the production code calls.
# ---------------------------------------------------------------------------
_fake_sd = types.ModuleType("sounddevice")
_fake_sd.InputStream = MagicMock()   # will be replaced per-test as needed
sys.modules.setdefault("sounddevice", _fake_sd)


# ---------------------------------------------------------------------------
# WakeWordDetector tests
# ---------------------------------------------------------------------------

class TestWakeWordDetector:
    def test_wake_word_detector_instantiates(self):
        """WakeWordDetector should not raise even if openwakeword is missing."""
        from victoria.voice.wake_word import WakeWordDetector
        detector = WakeWordDetector()
        # Must have these attributes regardless of whether openwakeword loaded
        assert hasattr(detector, "_available")
        assert hasattr(detector, "threshold")

    def test_wake_word_falls_back_when_unavailable(self):
        """When openwakeword import raises, _available must be False."""
        # Remove openwakeword from sys.modules if present, then block it
        sys.modules.pop("openwakeword", None)
        sys.modules.pop("openwakeword.model", None)

        # Inject a fake openwakeword module whose Model raises ImportError
        fake_oww = types.ModuleType("openwakeword")
        fake_model_mod = types.ModuleType("openwakeword.model")

        def _bad_model(*args, **kwargs):
            raise ImportError("openwakeword not installed")

        fake_model_mod.Model = _bad_model
        fake_oww.model = fake_model_mod

        with patch.dict(sys.modules, {
            "openwakeword": fake_oww,
            "openwakeword.model": fake_model_mod,
        }):
            # Force re-import so _try_load runs with our fake module in place
            import importlib
            import victoria.voice.wake_word as ww_mod
            importlib.reload(ww_mod)
            detector = ww_mod.WakeWordDetector()

        assert detector._available is False

    async def test_simple_phrase_detector(self):
        """SimplePhraseDetector.wait_for_wake_word completes without blocking."""
        from victoria.voice.wake_word import SimplePhraseDetector
        detector = SimplePhraseDetector()

        # Patch builtins.input so it returns immediately instead of waiting
        with patch("builtins.input", return_value=""):
            await detector.wait_for_wake_word()
        # Reaching here means it completed successfully


# ---------------------------------------------------------------------------
# audio.py tests
# ---------------------------------------------------------------------------

def _make_stream_mock(chunks):
    """Return a mock sd.InputStream context manager that replays `chunks`.

    chunks: list of np.ndarray (int16, shape (N,) or (N,1))
    After all chunks are exhausted, returns silent zeros.
    """
    chunk_iter = iter(chunks)

    def _read(n):
        try:
            data = next(chunk_iter)
        except StopIteration:
            data = np.zeros(n, dtype=np.int16)
        # sounddevice returns shape (blocksize, channels)
        return data.reshape(-1, 1), False

    stream_instance = MagicMock()
    stream_instance.read.side_effect = _read
    stream_instance.__enter__ = MagicMock(return_value=stream_instance)
    stream_instance.__exit__ = MagicMock(return_value=False)

    stream_cls = MagicMock(return_value=stream_instance)
    return stream_cls


class TestRecordUntilSilence:
    async def test_record_until_silence_returns_none_for_silence(self):
        """All-zero input (silence) should return None."""
        from victoria.voice.audio import record_until_silence, SAMPLE_RATE, CHUNK_DURATION

        chunk_samples = int(SAMPLE_RATE * CHUNK_DURATION)
        # Provide enough silent chunks to fill a short max_duration
        max_dur = 2.0
        num_chunks = int(max_dur / CHUNK_DURATION)
        silent_chunks = [np.zeros(chunk_samples, dtype=np.int16) for _ in range(num_chunks)]

        stream_cls = _make_stream_mock(silent_chunks)

        # Patch sounddevice.InputStream on the already-stubbed module in sys.modules
        sys.modules["sounddevice"].InputStream = stream_cls
        result = await record_until_silence(max_duration=max_dur)

        assert result is None

    async def test_record_until_silence_returns_audio_for_speech(self):
        """Loud chunks followed by silence should return a numpy array."""
        from victoria.voice.audio import (
            record_until_silence,
            SAMPLE_RATE,
            CHUNK_DURATION,
            SILENCE_DURATION,
            MIN_SPEECH_DURATION,
        )

        chunk_samples = int(SAMPLE_RATE * CHUNK_DURATION)

        # Build chunk sequence: enough loud chunks to satisfy MIN_SPEECH_DURATION,
        # then enough silent chunks to satisfy SILENCE_DURATION
        loud_count = int(MIN_SPEECH_DURATION / CHUNK_DURATION) + 1   # e.g. 6
        silent_count = int(SILENCE_DURATION / CHUNK_DURATION) + 1    # e.g. 16

        loud_amplitude = 2000  # well above SILENCE_THRESHOLD (500)
        loud_chunk = np.full(chunk_samples, loud_amplitude, dtype=np.int16)
        silent_chunk = np.zeros(chunk_samples, dtype=np.int16)

        chunks = [loud_chunk.copy() for _ in range(loud_count)] + \
                 [silent_chunk.copy() for _ in range(silent_count)]

        stream_cls = _make_stream_mock(chunks)

        # Use a generous max_duration so we don't hit the hard cap
        max_dur = (loud_count + silent_count + 5) * CHUNK_DURATION

        # Patch sounddevice.InputStream on the already-stubbed module in sys.modules
        sys.modules["sounddevice"].InputStream = stream_cls
        result = await record_until_silence(max_duration=max_dur)

        assert result is not None
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.int16
        assert len(result) > 0
