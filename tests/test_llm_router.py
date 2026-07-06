"""Tests for LLMRouter backend selection."""
from unittest.mock import patch

from victoria.core.llm_router import LLMRouter


LONG_MESSAGE = " ".join(["word"] * 300)


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
