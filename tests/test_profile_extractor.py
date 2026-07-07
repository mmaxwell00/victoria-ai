"""Tests for ProfileExtractor — regex detection and async LLM extraction."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from victoria.core.profile_extractor import EXTRACT_EVERY_N_TURNS, ProfileExtractor
from victoria.core.user_profile import UserProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_extractor(router=None):
    if router is None:
        router = MagicMock()
    return ProfileExtractor(router=router)


def _blank_profile(user_id: str = "u1") -> UserProfile:
    return UserProfile(user_id=user_id)


# ---------------------------------------------------------------------------
# detect_explicit_memory — regex, synchronous
# ---------------------------------------------------------------------------

def test_detect_explicit_memory_remember():
    extractor = _make_extractor()
    result = extractor.detect_explicit_memory("remember that I prefer bullet points")
    assert result == "I prefer bullet points"


def test_detect_explicit_memory_dont_forget():
    extractor = _make_extractor()
    result = extractor.detect_explicit_memory("don't forget that I'm using metric units")
    assert result == "I'm using metric units"


def test_detect_explicit_memory_note_that():
    extractor = _make_extractor()
    result = extractor.detect_explicit_memory("note that I hate long responses")
    assert result == "I hate long responses"


def test_detect_explicit_memory_fyi():
    extractor = _make_extractor()
    result = extractor.detect_explicit_memory("fyi, I'm a backend engineer")
    assert result == "I'm a backend engineer"


def test_detect_explicit_memory_no_match():
    extractor = _make_extractor()
    result = extractor.detect_explicit_memory("what's the weather in London?")
    assert result is None


def test_detect_explicit_memory_no_match_general():
    extractor = _make_extractor()
    result = extractor.detect_explicit_memory("I think Python is great")
    assert result is None


# ---------------------------------------------------------------------------
# should_extract — turn counter
# ---------------------------------------------------------------------------

def test_should_extract_every_n_turns():
    extractor = _make_extractor()
    results = [extractor.should_extract("u1") for _ in range(EXTRACT_EVERY_N_TURNS)]
    # Only the Nth call (last) should return True
    assert results[-1] is True
    assert all(r is False for r in results[:-1])


# ---------------------------------------------------------------------------
# extract_from_turn — async LLM path
# ---------------------------------------------------------------------------

async def test_extract_from_turn_not_due():
    """When it's not time to extract, the router is never called."""
    router = MagicMock()
    router.chat = AsyncMock()
    extractor = _make_extractor(router)
    profile = _blank_profile()

    # First call is turn 1 — not due (due on turn N)
    result = await extractor.extract_from_turn(profile, "hello", "hi", user_id="u1")

    assert result is profile  # same object returned unchanged
    router.chat.assert_not_called()


async def test_extract_from_turn_adds_preference():
    """On the Nth turn the router is called and new preferences are merged."""
    router = MagicMock()
    router.chat = AsyncMock(
        return_value=(
            '{"new_preferences": ["prefers short answers"], "new_topics": [], "style_note": null}',
            "ollama",
        )
    )
    extractor = _make_extractor(router)
    profile = _blank_profile()

    updated = profile
    for _ in range(EXTRACT_EVERY_N_TURNS):
        updated = await extractor.extract_from_turn(
            updated, "keep it brief please", "Sure!", user_id="u1"
        )

    assert "prefers short answers" in updated.preferences
    # Router called exactly once (only on the Nth turn)
    router.chat.assert_called_once()


async def test_extract_from_turn_router_error():
    """If the router raises, the original profile is returned without propagating."""
    router = MagicMock()
    router.chat = AsyncMock(side_effect=Exception("network timeout"))
    extractor = _make_extractor(router)
    profile = _blank_profile()

    # Burn through N-1 turns first so next call is the Nth
    for _ in range(EXTRACT_EVERY_N_TURNS - 1):
        extractor.should_extract("u1")

    # Nth call — router raises — should NOT propagate
    result = await extractor.extract_from_turn(
        profile, "hello", "hi", user_id="u1"
    )
    assert result is profile


async def test_extract_from_turn_bad_json():
    """If the router returns invalid JSON, the original profile is returned."""
    router = MagicMock()
    router.chat = AsyncMock(return_value=("not valid json", "ollama"))
    extractor = _make_extractor(router)
    profile = _blank_profile()

    # Burn through N-1 turns first
    for _ in range(EXTRACT_EVERY_N_TURNS - 1):
        extractor.should_extract("u1")

    result = await extractor.extract_from_turn(
        profile, "hello", "hi", user_id="u1"
    )
    assert result is profile


async def test_extract_from_turn_pins_backend_and_neutral_prompt():
    """Regression: extraction must not auto-escalate to Claude and must not
    use the Victoria persona system prompt (it breaks JSON-only output)."""
    router = MagicMock()
    router.chat = AsyncMock(
        return_value=('{"new_preferences": [], "new_topics": [], "style_note": null}', "ollama")
    )
    extractor = _make_extractor(router)
    profile = _blank_profile()

    for _ in range(EXTRACT_EVERY_N_TURNS):
        await extractor.extract_from_turn(profile, "hello", "hi", user_id="u1")

    router.chat.assert_called_once()
    kwargs = router.chat.call_args.kwargs
    from victoria.config import settings, VICTORIA_SYSTEM_PROMPT
    assert kwargs.get("force_backend") == settings.default_llm
    system_prompt = kwargs.get("system_prompt")
    assert system_prompt and system_prompt != VICTORIA_SYSTEM_PROMPT
    assert "JSON" in system_prompt


def test_detect_explicit_memory_mid_sentence():
    """Regression: 'Hey Victoria, remember that…' must be detected (search, not match)."""
    router = MagicMock()
    extractor = _make_extractor(router)
    assert (
        extractor.detect_explicit_memory("Hey Victoria, remember that I'm vegetarian")
        == "I'm vegetarian"
    )
    assert extractor.detect_explicit_memory("what's the weather?") is None
