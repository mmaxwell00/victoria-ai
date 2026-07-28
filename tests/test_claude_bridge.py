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


# ── Host-bridge SCRIPT side (scripts/claude-bridge.py `_run_claude`) ─────────
# The bridge runs on the host and reaches the governed claude sandbox over either
# `sbx exec` (default) or `ssh`. These tests cover the transport argv + the
# security invariants (prompt on stdin, validated tokens only) without invoking
# sbx/ssh/claude.
import importlib.util
import os
import pathlib
import types

_BRIDGE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude-bridge.py"


def _load_bridge():
    spec = importlib.util.spec_from_file_location("claude_bridge", _BRIDGE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                         # stdlib-only; no side effects on import
    return mod


@pytest.fixture
def bridge(monkeypatch):
    """The loaded bridge module with subprocess.run stubbed to capture the argv
    and stdin instead of executing anything. `cap` holds the last call."""
    mod = _load_bridge()
    cap = {}

    def _fake_run(argv, input=None, capture_output=None, text=None, timeout=None):
        cap["argv"] = argv
        cap["input"] = input
        return types.SimpleNamespace(returncode=cap.get("rc", 0),
                                     stdout=cap.get("stdout", "  hi  "),
                                     stderr=cap.get("stderr", ""))

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    mod._cap = cap
    return mod


def test_bridge_exec_mode_argv_and_prompt_on_stdin(bridge, monkeypatch):
    monkeypatch.delenv("CLAUDE_BRIDGE_MODE", raising=False)   # default = exec
    monkeypatch.setenv("CLAUDE_SANDBOX", "victoria-claude")
    out = bridge._run_claude("SECRET-PROMPT", "be terse", "sonnet", "WebSearch,Bogus;rm")
    argv = bridge._cap["argv"]
    assert argv[:6] == ["sbx", "exec", "-i", "victoria-claude", "--", "claude"]
    assert argv[6:9] == ["-p", "--model", "sonnet"]
    assert "--append-system-prompt" in argv and "be terse" in argv
    assert "--allowedTools" in argv and "WebSearch" in argv
    assert "Bogus" not in argv and "Bogus;rm" not in argv    # invalid tool filtered
    # SECURITY: the untrusted prompt is fed on stdin, never as an argv token.
    assert bridge._cap["input"] == "SECRET-PROMPT"
    assert "SECRET-PROMPT" not in " ".join(argv)
    assert out == "hi"                                        # stdout trimmed


def test_bridge_ssh_mode_argv(bridge, monkeypatch):
    monkeypatch.setenv("CLAUDE_BRIDGE_MODE", "ssh")
    monkeypatch.setenv("CLAUDE_SANDBOX", "victoria-claude")
    monkeypatch.delenv("CLAUDE_SSH_HOST", raising=False)
    bridge._run_claude("hello", "", "sonnet", "")
    argv = bridge._cap["argv"]
    assert argv[0] == "ssh"
    assert "victoria-claude.sbx" in argv                      # default ssh host
    assert argv[-4:] == ["claude", "-p", "--model", "sonnet"] # remote command tail
    assert "sbx" not in argv                                  # ssh mode, not exec
    assert bridge._cap["input"] == "hello"                    # prompt still on stdin


def test_bridge_rejects_bad_model_falls_back_to_sonnet(bridge, monkeypatch):
    monkeypatch.delenv("CLAUDE_BRIDGE_MODE", raising=False)
    bridge._run_claude("x", "", "bad model!;rm -rf", "")      # invalid → sonnet
    argv = bridge._cap["argv"]
    assert "sonnet" in argv
    assert "bad model!;rm -rf" not in " ".join(argv)


def test_bridge_nonzero_exit_raises(bridge, monkeypatch):
    monkeypatch.delenv("CLAUDE_BRIDGE_MODE", raising=False)
    bridge._cap["rc"] = 1
    bridge._cap["stderr"] = "Invalid API key"
    with pytest.raises(RuntimeError, match="claude exited 1"):
        bridge._run_claude("x", "", "sonnet", "")


def test_gen_certs_pass_strict_openssl_verify(tmp_path):
    """Generated certs must pass STRICT path validation. Regression: a CA without
    basicConstraints=CA:TRUE + keyUsage=keyCertSign is accepted by lenient clients
    (curl) but rejected by Python's ssl / OpenSSL 3.x — which broke Victoria's mTLS
    to the bridge (handshake reset / "CA cert does not include key usage extension").
    """
    import shutil
    import subprocess
    if not shutil.which("openssl"):
        pytest.skip("openssl not available")
    mod = _load_bridge()
    d = str(tmp_path / "certs")
    mod._gen_certs(d)
    ca = os.path.join(d, "ca.crt")
    for leaf in ("server.crt", "client.crt"):
        r = subprocess.run(
            ["openssl", "verify", "-x509_strict", "-CAfile", ca, os.path.join(d, leaf)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"{leaf} failed strict verify: {r.stdout}{r.stderr}"
