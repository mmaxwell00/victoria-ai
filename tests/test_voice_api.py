"""Tests for the web voice endpoints (/v1/transcribe, /v1/tts) and the
byte-returning synthesize() methods that back them."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from victoria.main import app
from victoria.config import settings


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# /v1/transcribe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transcribe_returns_text(client):
    with patch("victoria.core.transcription.transcribe_audio",
               AsyncMock(return_value="hello there")):
        async with client as c:
            resp = await c.post(
                "/v1/transcribe",
                files={"audio": ("speech.webm", b"FAKEAUDIOBYTES", "audio/webm")},
            )
    assert resp.status_code == 200
    assert resp.json()["text"] == "hello there"


@pytest.mark.asyncio
async def test_transcribe_rejects_empty_upload(client):
    async with client as c:
        resp = await c.post(
            "/v1/transcribe",
            files={"audio": ("speech.webm", b"", "audio/webm")},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_transcribe_reports_failure(client):
    with patch("victoria.core.transcription.transcribe_audio",
               AsyncMock(side_effect=RuntimeError("ffmpeg missing"))):
        async with client as c:
            resp = await c.post(
                "/v1/transcribe",
                files={"audio": ("speech.webm", b"xx", "audio/webm")},
            )
    assert resp.status_code == 500
    assert "ffmpeg missing" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /v1/tts
# ---------------------------------------------------------------------------

def _fake_engine(audio=b"RIFFfakeaudio", mime="audio/wav", exc=None):
    eng = MagicMock()
    eng.synthesize = AsyncMock(return_value=(audio, mime)) if exc is None \
        else AsyncMock(side_effect=exc)
    return eng


@pytest.mark.asyncio
async def test_tts_returns_audio(client):
    with patch("victoria.interfaces.api._tts_engine", lambda: _fake_engine()):
        async with client as c:
            resp = await c.post("/v1/tts", json={"text": "hello"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/wav")
    assert resp.content == b"RIFFfakeaudio"


@pytest.mark.asyncio
async def test_tts_rejects_empty_text(client):
    async with client as c:
        resp = await c.post("/v1/tts", json={"text": "   "})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_tts_missing_model_returns_503(client):
    exc = FileNotFoundError("Piper model not found: models/x.onnx")
    with patch("victoria.interfaces.api._tts_engine", lambda: _fake_engine(exc=exc)):
        async with client as c:
            resp = await c.post("/v1/tts", json={"text": "hello"})
    assert resp.status_code == 503
    assert "model" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# synthesize() byte methods
# ---------------------------------------------------------------------------

async def test_piper_synthesize_uses_synthesize_wav():
    """Piper must call synthesize_wav (writes a real header) and return WAV bytes."""
    import wave, io
    from victoria.voice.tts.piper_tts import PiperTTSEngine

    def fake_synth_wav(text, wav_file):
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\x00\x00" * 100)

    fake_voice = MagicMock()
    fake_voice.synthesize_wav.side_effect = fake_synth_wav

    engine = PiperTTSEngine()
    with patch.object(engine, "_load_voice", return_value=fake_voice):
        audio, mime = await engine.synthesize("hello")

    assert mime == "audio/wav"
    assert audio[:4] == b"RIFF"
    fake_voice.synthesize_wav.assert_called_once()
    # Round-trips as a valid WAV.
    with wave.open(io.BytesIO(audio), "rb") as w:
        assert w.getframerate() == 22050


@pytest.mark.skipif(
    not os.path.exists(settings.piper_model_path),
    reason="Piper model not downloaded",
)
async def test_piper_synthesize_real_model_produces_wav():
    from victoria.voice.tts.piper_tts import PiperTTSEngine
    audio, mime = await PiperTTSEngine().synthesize("Hello Mark.")
    assert mime == "audio/wav"
    assert audio[:4] == b"RIFF"
    assert len(audio) > 1000


async def test_elevenlabs_synthesize_returns_mp3(monkeypatch):
    import victoria.voice.tts.elevenlabs_tts as el_mod
    from victoria.voice.tts.elevenlabs_tts import ElevenLabsTTSEngine

    monkeypatch.setattr(el_mod.settings, "elevenlabs_api_key", "test-key")
    engine = ElevenLabsTTSEngine()

    fake_resp = MagicMock()
    fake_resp.content = b"ID3mp3bytes"
    fake_resp.raise_for_status = MagicMock()

    class FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return fake_resp

    with patch.object(el_mod.httpx, "AsyncClient", FakeClient):
        audio, mime = await engine.synthesize("hello")

    assert mime == "audio/mpeg"
    assert audio == b"ID3mp3bytes"
