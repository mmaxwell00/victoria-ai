"""Tests for the runtime local-model selector API."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport

from victoria.main import app
from victoria.interfaces.api import get_manager, _recommend_model, _persist_env


MODELS = [
    {"id": "ai/llama3.2:3B", "size_gib": 1.9, "params": "3.2B", "context": 4096},
    {"id": "ai/qwen2.5:32k", "size_gib": 4.4, "params": "7.6B", "context": 32768},
    {"id": "ai/qwen3-coder:latest", "size_gib": 16.5, "params": "30B", "context": 4096},
    {"id": "ai/qwen3-coder:32k", "size_gib": 16.5, "params": "30B", "context": 32768},
]


# ---------------------------------------------------------------------------
# Recommendation heuristic
# ---------------------------------------------------------------------------

def test_recommend_picks_biggest_that_fits_and_prefers_context():
    # 64 GB → budget ~35 GB → biggest is the 16.5 GB pair; tie broken by context (32k).
    assert _recommend_model(MODELS, 64) == "ai/qwen3-coder:32k"


def test_recommend_small_ram_picks_small_model():
    # 8 GB → budget ~4.4 GB → only the 1.9 and 4.4 fit; biggest fitting is qwen2.5:32k.
    assert _recommend_model(MODELS, 8) == "ai/qwen2.5:32k"


def test_recommend_tiny_ram_falls_back_to_smallest():
    # 2 GB → nothing fits the budget → smallest available.
    assert _recommend_model(MODELS, 2) == "ai/llama3.2:3B"


def test_recommend_empty():
    assert _recommend_model([], 64) is None


# ---------------------------------------------------------------------------
# .env persistence
# ---------------------------------------------------------------------------

def test_persist_env_upserts(tmp_path):
    p = tmp_path / ".env"
    p.write_text("DEFAULT_LLM=docker\nMODEL_RUNNER_MODEL=old\nOTHER=x\n")
    _persist_env("MODEL_RUNNER_MODEL", "new-model", str(p))
    text = p.read_text()
    assert "MODEL_RUNNER_MODEL=new-model" in text
    assert "old" not in text
    assert "DEFAULT_LLM=docker" in text and "OTHER=x" in text  # others preserved


def test_persist_env_appends_when_absent(tmp_path):
    p = tmp_path / ".env"
    p.write_text("DEFAULT_LLM=docker\n")
    _persist_env("MODEL_RUNNER_MODEL", "m", str(p))
    assert "MODEL_RUNNER_MODEL=m" in p.read_text()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    mgr = MagicMock()
    mgr.router.available_models = AsyncMock(return_value=MODELS)
    app.dependency_overrides[get_manager] = lambda: mgr
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_models(client):
    async with client as c:
        r = await c.get("/v1/models")
    body = r.json()
    assert r.status_code == 200
    assert [m["id"] for m in body["models"]] == [m["id"] for m in MODELS]
    assert "active" in body and "recommended" in body and "ram_gb" in body


@pytest.mark.asyncio
async def test_select_valid_model(client, monkeypatch, tmp_path):
    # Redirect .env persistence to a temp file.
    import victoria.interfaces.api as api_mod
    monkeypatch.setattr(api_mod, "_persist_env", lambda *a, **k: None)
    from victoria.config import settings
    # monkeypatch records the original and restores it at teardown, even though
    # the endpoint reassigns it — keeps this test from leaking global state.
    monkeypatch.setattr(settings, "model_runner_model", settings.model_runner_model)
    async with client as c:
        r = await c.post("/v1/models/select", json={"model": "ai/qwen3-coder:32k"})
    assert r.status_code == 200 and r.json()["active"] == "ai/qwen3-coder:32k"
    assert settings.model_runner_model == "ai/qwen3-coder:32k"


@pytest.mark.asyncio
async def test_select_rejects_unknown_model(client):
    async with client as c:
        r = await c.post("/v1/models/select", json={"model": "ai/not-pulled:latest"})
    assert r.status_code == 400
    assert "not available" in r.json()["detail"]
