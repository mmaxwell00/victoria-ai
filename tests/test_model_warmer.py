"""The keep-alive that stops the local model being evicted mid-conversation.

Why this exists: Docker Model Runner drops an idle model after ~5 min, and the
next question pays the reload — measured 5.3s cold vs 0.36s warm. The warmer must
be genuinely unobtrusive: cheap pings, opt-out-able, and above all it must never
take the app down or spam the log when the Model Runner is unreachable.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from victoria.config import settings
from victoria.core import model_warmer

# `patch.object(model_warmer.asyncio, "sleep", ...)` replaces the GLOBAL asyncio.sleep
# (same module object), which would also neuter the yields these tests rely on. Keep a
# reference to the real one so the tests can still hand control to the warmer task.
_real_sleep = asyncio.sleep


async def _yield():
    await _real_sleep(0)


async def test_disabled_returns_immediately(monkeypatch):
    """0 means off — and off must not leave a task spinning."""
    monkeypatch.setattr(settings, "model_keepalive_seconds", 0)
    with patch.object(model_warmer, "_ping", new=AsyncMock()) as ping:
        await asyncio.wait_for(model_warmer.keep_model_warm(), timeout=1)
    ping.assert_not_called()


async def test_pings_on_the_interval(monkeypatch):
    """It should actually touch the model, repeatedly."""
    monkeypatch.setattr(settings, "model_keepalive_seconds", 1)
    calls = 0

    async def fake_ping(_client):
        nonlocal calls
        calls += 1
        return True

    async def no_wait(_):  # collapse the warmer's wait, but still yield
        await _real_sleep(0)

    with patch.object(model_warmer, "_ping", new=fake_ping), \
         patch.object(model_warmer.asyncio, "sleep", new=no_wait):
        task = asyncio.create_task(model_warmer.keep_model_warm())
        await _yield()
        for _ in range(50):
            if calls >= 3:
                break
            await _yield()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls >= 3


async def test_survives_a_dead_model_runner(monkeypatch):
    """A failing ping must NOT escape — the app has to keep serving."""
    monkeypatch.setattr(settings, "model_keepalive_seconds", 1)
    calls = 0

    async def boom(_client):
        nonlocal calls
        calls += 1
        raise ConnectionError("model runner is down")

    async def no_wait(_):
        await _real_sleep(0)

    with patch.object(model_warmer, "_ping", new=boom), \
         patch.object(model_warmer.asyncio, "sleep", new=no_wait):
        task = asyncio.create_task(model_warmer.keep_model_warm())
        for _ in range(50):
            if calls >= 3:
                break
            await _yield()
        assert not task.done(), "the warmer died on a failed ping"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls >= 3, "it stopped retrying after a failure"


async def test_failure_is_logged_once_not_every_cycle(monkeypatch, caplog):
    """A persistent outage should say so once, not once per interval."""
    monkeypatch.setattr(settings, "model_keepalive_seconds", 1)
    calls = 0

    async def boom(_client):
        nonlocal calls
        calls += 1
        raise ConnectionError("still down")

    async def no_wait(_):
        await _real_sleep(0)

    with caplog.at_level("WARNING", logger="victoria.core.model_warmer"), \
         patch.object(model_warmer, "_ping", new=boom), \
         patch.object(model_warmer.asyncio, "sleep", new=no_wait):
        task = asyncio.create_task(model_warmer.keep_model_warm())
        for _ in range(60):
            if calls >= 5:
                break
            await _yield()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    warnings = [r for r in caplog.records if "keep-alive ping failed" in r.message]
    assert len(warnings) == 1, f"logged {len(warnings)} times for one outage"


async def test_ping_carries_the_real_prefix_and_generates_one_token():
    """The ping must WARM Victoria's actual prefix, not just poke the model.

    The first version sent a bare "ok" with no system prompt. That kept the model
    resident while EVICTING the cached prompt prefix — llama.cpp caches by prefix, so
    a different prompt throws the useful one away. Measured: real question 2.5s, then a
    bare-"ok" ping, then the next question 5.1s. Carrying the real system prompt (and
    tools, which are ~2,250 tokens of the prefill) keeps both model and cache warm.
    Generation stays at one token — this is about the prompt, not the answer.
    """
    from victoria.config import VICTORIA_SYSTEM_PROMPT

    client = AsyncMock()
    client.post.return_value.raise_for_status = lambda: None
    await model_warmer._ping(client)
    body = client.post.call_args.kwargs["json"]

    assert body["max_tokens"] == 1, "the ping should not generate a real answer"
    assert body["stream"] is False
    system = [m for m in body["messages"] if m["role"] == "system"]
    assert system, "no system message — the ping would evict the cached prefix"
    assert VICTORIA_SYSTEM_PROMPT in system[0]["content"]
