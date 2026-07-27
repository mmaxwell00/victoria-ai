"""Tests for Victoria's Claude host-bridge escalation path (llm_router).

Covers the routing decision (bridge vs. local CLI) and the bridge request/response
handling — without touching the network or the local `claude` binary.
"""
import json

import pytest

from victoria.config import settings
from victoria.core.llm_router import LLMRouter


class _FakeResp:
    def __init__(self, status, json_body=None, text=None):
        self.status_code = status
        self._json = json_body
        self.text = text if text is not None else (json.dumps(json_body) if json_body else "")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
    """Stand-in for httpx.AsyncClient — async context manager with .post()."""
    def __init__(self, resp, capture):
        self._resp = resp
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self._capture["url"] = url
        self._capture["json"] = json
        return self._resp


@pytest.mark.asyncio
async def test_bridge_success_sends_prompt_and_returns_answer(monkeypatch):
    monkeypatch.setattr(settings, "claude_bridge_url", "https://host.docker.internal:8787/ask")
    cap = {}
    resp = _FakeResp(200, {"answer": "  42  "})
    monkeypatch.setattr("victoria.core.llm_router.httpx.AsyncClient",
                        lambda **kw: _FakeClient(resp, cap))
    out = await LLMRouter().claude_cli("what is 6*7?", system_prompt="be terse")
    assert out == "42"                                  # trimmed
    assert cap["url"].endswith("/ask")
    assert cap["json"]["prompt"] == "what is 6*7?"      # prompt forwarded
    assert cap["json"]["system_prompt"] == "be terse"   # system prompt forwarded


@pytest.mark.asyncio
async def test_bridge_http_error_raises(monkeypatch):
    monkeypatch.setattr(settings, "claude_bridge_url", "https://x/ask")
    resp = _FakeResp(502, text="claude exited 1: Not logged in")
    monkeypatch.setattr("victoria.core.llm_router.httpx.AsyncClient",
                        lambda **kw: _FakeClient(resp, {}))
    with pytest.raises(RuntimeError, match="bridge error"):
        await LLMRouter().claude_cli("x")


@pytest.mark.asyncio
async def test_bridge_empty_answer_raises(monkeypatch):
    monkeypatch.setattr(settings, "claude_bridge_url", "https://x/ask")
    resp = _FakeResp(200, {"answer": "   "})
    monkeypatch.setattr("victoria.core.llm_router.httpx.AsyncClient",
                        lambda **kw: _FakeClient(resp, {}))
    with pytest.raises(RuntimeError, match="no answer"):
        await LLMRouter().claude_cli("x")


@pytest.mark.asyncio
async def test_no_bridge_url_takes_local_cli_path(monkeypatch):
    """With no bridge configured, claude_cli must NOT hit the bridge — it falls
    through to the local CLI (which we force to 'not found' to prove the branch)."""
    monkeypatch.setattr(settings, "claude_bridge_url", "")
    bridge_called = {"hit": False}

    async def _boom(self, *a, **k):
        bridge_called["hit"] = True
        return "should-not-happen"

    monkeypatch.setattr(LLMRouter, "_claude_via_bridge", _boom)

    async def _no_binary(*a, **k):
        raise FileNotFoundError("claude")

    monkeypatch.setattr("victoria.core.llm_router.asyncio.create_subprocess_exec", _no_binary)

    with pytest.raises(RuntimeError, match="not found on"):
        await LLMRouter().claude_cli("x")
    assert bridge_called["hit"] is False                # bridge was never used
