"""Tests for LLMRouter backend selection."""
from unittest.mock import patch

from victoria.core.llm_router import LLMRouter, _claude_cli_env, _is_coding_task


def test_is_coding_task_classifier():
    for q in ["Write a Python function to reverse a list",
              "there's a bug in my JavaScript",
              "refactor this class",
              "```\nprint('hi')\n```",
              "help me with a SQL query",
              "fix the Dockerfile"]:
        assert _is_coding_task(q), q
    for q in ["What's the weather in Atlanta tomorrow?",
              "tell me a joke about cats",
              "what's the capital of France?",
              "remind me to call mum"]:
        assert not _is_coding_task(q), q


def test_pick_local_model_routes_coding_to_code_model():
    router = LLMRouter()
    with patch("victoria.core.llm_router.settings") as s:
        s.model_runner_model = "chat-model"
        s.model_runner_code_model = "code-model"
        assert router._pick_local_model([{"role": "user", "content": "debug my python function"}]) == "code-model"
        assert router._pick_local_model([{"role": "user", "content": "what's the weather tomorrow?"}]) == "chat-model"


def test_pick_local_model_defaults_when_no_code_model():
    router = LLMRouter()
    with patch("victoria.core.llm_router.settings") as s:
        s.model_runner_model = "chat-model"
        s.model_runner_code_model = ""   # routing disabled
        assert router._pick_local_model([{"role": "user", "content": "write a rust program"}]) == "chat-model"


LONG_MESSAGE = " ".join(["word"] * 300)


def test_claude_cli_env_scrubs_session_and_gateway_vars():
    """Regression: a session/gateway context inherited from the launch shell
    (e.g. Victoria started from inside another Claude Code session) must not
    reach the `claude -p` subprocess, or it hijacks auth and the CLI 401s."""
    polluted = {
        "PATH": "/usr/bin",
        "HOME": "/Users/x",
        "ANTHROPIC_BASE_URL": "https://gateway.example/",
        "ANTHROPIC_API_KEY": "sk-stray",
        "ANTHROPIC_AUTH_TOKEN": "tok",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "claude-sdk",
        "CLAUDE_CODE_OAUTH_TOKEN": "keep-me",  # supported headless token path
    }
    env = _claude_cli_env(polluted)
    # Hijacking vars are gone
    for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
              "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        assert k not in env
    # Ordinary env and the supported token are preserved
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/Users/x"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep-me"


def test_claude_cli_env_injects_configured_oauth_token():
    """A configured token (from `claude setup-token`) is injected so escalation
    authenticates regardless of the launch environment."""
    env = _claude_cli_env({"PATH": "/usr/bin"}, oauth_token="tok-123")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-123"
    # …and nothing is injected when no token is configured
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in _claude_cli_env({"PATH": "/usr/bin"})


def test_force_backend_wins():
    router = LLMRouter()
    assert router._pick_backend(LONG_MESSAGE, force="ollama") == "ollama"
    assert router._pick_backend("hi", force="claude") == "claude"


def test_default_claude_always_routes_to_claude():
    router = LLMRouter()
    with patch("victoria.core.llm_router.settings") as s:
        s.default_llm = "claude"
        assert router._pick_backend("hi") == "claude"


def test_short_query_stays_local():
    router = LLMRouter()
    with patch("victoria.core.llm_router.settings") as s:
        s.default_llm = "ollama"
        s.anthropic_api_key = "sk-test"
        s.complex_query_threshold = 200
        assert router._pick_backend("hi there") == "ollama"


def test_long_query_escalates_to_claude_when_key_present():
    router = LLMRouter()
    with patch("victoria.core.llm_router.settings") as s:
        s.default_llm = "ollama"
        s.anthropic_api_key = "sk-test"
        s.complex_query_threshold = 200
        assert router._pick_backend(LONG_MESSAGE) == "claude"


def test_long_query_stays_local_without_api_key():
    """Regression: long queries must not route to Claude when no key is set."""
    router = LLMRouter()
    with patch("victoria.core.llm_router.settings") as s:
        s.default_llm = "ollama"
        s.anthropic_api_key = ""
        s.complex_query_threshold = 200
        assert router._pick_backend(LONG_MESSAGE) == "ollama"

    with patch("victoria.core.llm_router.settings") as s:
        s.default_llm = "docker"
        s.anthropic_api_key = ""
        s.complex_query_threshold = 200
        assert router._pick_backend(LONG_MESSAGE) == "docker"
