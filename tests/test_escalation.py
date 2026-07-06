"""Tests for the local-first → ask → Claude Code CLI escalation flow."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from victoria.core.conversation import ConversationManager
from victoria.core.llm_router import LLMRouter
from victoria.config import ESCALATION_SENTINEL, ESCALATION_INSTRUCTION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_memory():
    mem = MagicMock()
    mem.get_or_create_session.return_value = None
    mem.get_history.return_value = []
    mem.add_message.return_value = None
    return mem


def make_manager(router):
    return ConversationManager(memory=make_memory(), router=router)


# ---------------------------------------------------------------------------
# ConversationManager.chat — non-streaming escalation flow
# ---------------------------------------------------------------------------

async def test_local_answer_returns_normally_without_escalation():
    router = MagicMock()
    router.chat = AsyncMock(return_value=("A brilliant local answer.", "docker"))
    mgr = make_manager(router)

    result = await mgr.chat("Hello", session_id="s1")

    assert result["response"] == "A brilliant local answer."
    assert result["backend"] == "docker"
    assert "s1" not in mgr._pending_escalation
    router.claude_cli.assert_not_called()


async def test_sentinel_triggers_escalation_offer():
    router = MagicMock()
    router.chat = AsyncMock(return_value=(ESCALATION_SENTINEL, "docker"))
    router.claude_cli = AsyncMock()
    mgr = make_manager(router)

    result = await mgr.chat("What's the FTSE 100 at right now?", session_id="s1")

    assert result["backend"] == "victoria"
    assert "?" in result["response"]  # it's asking permission
    assert mgr._pending_escalation["s1"] == "What's the FTSE 100 at right now?"
    router.claude_cli.assert_not_called()  # nothing escalated without a yes


async def test_empty_local_answer_triggers_escalation_offer():
    router = MagicMock()
    router.chat = AsyncMock(return_value=("   ", "docker"))
    mgr = make_manager(router)

    result = await mgr.chat("Anything?", session_id="s1")
    assert result["backend"] == "victoria"
    assert "s1" in mgr._pending_escalation


async def test_local_exception_triggers_escalation_offer():
    router = MagicMock()
    router.chat = AsyncMock(side_effect=RuntimeError("model down"))
    mgr = make_manager(router)

    result = await mgr.chat("Anything?", session_id="s1")
    assert result["backend"] == "victoria"
    assert "s1" in mgr._pending_escalation


async def test_yes_after_offer_escalates_to_claude():
    router = MagicMock()
    router.chat = AsyncMock(return_value=(ESCALATION_SENTINEL, "docker"))
    router.claude_cli = AsyncMock(return_value="Claude's proper answer.")
    mgr = make_manager(router)

    await mgr.chat("Hard question", session_id="s1")
    result = await mgr.chat("yes", session_id="s1")

    assert result["backend"] == "claude"
    assert result["response"] == "Claude's proper answer."
    assert "s1" not in mgr._pending_escalation
    # Claude is asked the ORIGINAL question, not the word "yes".
    router.claude_cli.assert_awaited_once()
    assert router.claude_cli.await_args.args[0] == "Hard question"


async def test_no_after_offer_falls_back_to_local_answer():
    """Declining escalation should still get a best-effort local answer, not a dead end."""
    router = MagicMock()
    # 1st call → sentinel (offer). 2nd call (after "no") → a real local answer.
    router.chat = AsyncMock(side_effect=[(ESCALATION_SENTINEL, "docker"),
                                          ("My best guess is Paris.", "docker")])
    router.claude_cli = AsyncMock()
    mgr = make_manager(router)

    await mgr.chat("Hard question", session_id="s1")
    result = await mgr.chat("no thanks", session_id="s1")

    assert result["backend"] == "docker"                 # answered locally
    assert result["response"] == "My best guess is Paris."
    assert "s1" not in mgr._pending_escalation
    router.claude_cli.assert_not_called()                # never went to Claude


async def test_no_fallback_strips_stray_sentinel():
    """If the best-effort local answer still emits a sentinel, it's stripped (no re-offer)."""
    router = MagicMock()
    router.chat = AsyncMock(side_effect=[(ESCALATION_SENTINEL, "docker"),
                                          ("[ESCALATE]", "docker")])
    mgr = make_manager(router)

    await mgr.chat("Hard question", session_id="s1")
    result = await mgr.chat("no", session_id="s1")

    assert result["backend"] == "docker"
    assert "ESCALATE" not in result["response"]          # stripped, not re-offered
    assert "s1" not in mgr._pending_escalation


async def test_other_reply_while_pending_is_treated_as_new_question():
    router = MagicMock()
    # First call: sentinel (offer). Second call: a normal local answer.
    router.chat = AsyncMock(side_effect=[(ESCALATION_SENTINEL, "docker"),
                                          ("Sure, here you go.", "docker")])
    router.claude_cli = AsyncMock()
    mgr = make_manager(router)

    await mgr.chat("Hard question", session_id="s1")
    result = await mgr.chat("actually, what's 2+2?", session_id="s1")

    assert result["backend"] == "docker"
    assert result["response"] == "Sure, here you go."
    assert "s1" not in mgr._pending_escalation
    router.claude_cli.assert_not_called()


async def test_force_backend_claude_goes_straight_to_cli():
    router = MagicMock()
    router.chat = AsyncMock(return_value=("local", "docker"))
    router.claude_cli = AsyncMock(return_value="Direct Claude answer.")
    mgr = make_manager(router)

    result = await mgr.chat("Explain relativity", session_id="s1", force_backend="claude")

    assert result["backend"] == "claude"
    assert result["response"] == "Direct Claude answer."
    router.chat.assert_not_called()  # never bothered the local model
    assert "s1" not in mgr._pending_escalation


async def test_claude_cli_failure_is_reported_gracefully():
    router = MagicMock()
    router.chat = AsyncMock(return_value=(ESCALATION_SENTINEL, "docker"))
    router.claude_cli = AsyncMock(side_effect=RuntimeError("CLI not found"))
    mgr = make_manager(router)

    await mgr.chat("Hard question", session_id="s1")
    result = await mgr.chat("yes", session_id="s1")

    assert result["backend"] == "victoria"
    assert "CLI not found" in result["response"]
    assert "s1" not in mgr._pending_escalation


async def test_local_backend_gets_escalation_instruction():
    """The local model must be told how to signal it can't answer."""
    router = MagicMock()
    captured = {}

    async def fake_chat(history, force_backend=None, system_prompt=None):
        captured["system_prompt"] = system_prompt
        captured["force_backend"] = force_backend
        return ("fine", "docker")

    router.chat = fake_chat
    mgr = make_manager(router)

    await mgr.chat("hi", session_id="s1")
    assert ESCALATION_INSTRUCTION in captured["system_prompt"]
    assert captured["force_backend"] in ("docker", "ollama")


# ---------------------------------------------------------------------------
# Reply classification
# ---------------------------------------------------------------------------

# Includes the punctuated/casing forms Whisper produces from spoken replies.
@pytest.mark.parametrize("msg", ["yes", "Yes", "Yes.", "Yes!", "Yeah.", "Yep!",
                                  "y", "sure", "Sure.", "ok", "Okay.", "go ahead",
                                  "Go ahead.", "claude", "yes please", "Yes, please.",
                                  "Do it!", "Absolutely."])
def test_classify_affirmative(msg):
    assert ConversationManager._classify_reply(msg) == "yes"


@pytest.mark.parametrize("msg", ["no", "No", "No.", "nope", "Nope!", "cancel",
                                  "no thanks", "No, thanks.", "forget it"])
def test_classify_negative(msg):
    assert ConversationManager._classify_reply(msg) == "no"


@pytest.mark.parametrize("msg", ["what's 2+2?", "tell me about London", "maybe later", ""])
def test_classify_other(msg):
    assert ConversationManager._classify_reply(msg) == "other"


# The sentinel must be recognised even when the model drops brackets / adds punctuation.
@pytest.mark.parametrize("text", ["[ESCALATE]", "ESCALATE", "escalate", "escalate.",
                                   " [escalate] ", "**[ESCALATE]**", "[ESCALATE"])
def test_escalation_signal_matches_loosely(text):
    assert ConversationManager._is_escalation_signal(text) is True
    assert ConversationManager._needs_escalation(text) is True


@pytest.mark.parametrize("text", ["I could escalate this to Claude if you like",
                                   "The answer is 42.", "Escalate the ticket to tier 2 first."])
def test_escalation_signal_ignores_real_answers(text):
    assert ConversationManager._is_escalation_signal(text) is False


async def test_bracketless_sentinel_triggers_offer_not_leak():
    """Regression: a bare 'ESCALATE' (no brackets) must offer escalation, not leak."""
    router = MagicMock()
    router.chat = AsyncMock(return_value=("ESCALATE", "docker"))
    mgr = make_manager(router)

    result = await mgr.chat("current gold price?", session_id="s1")
    assert result["backend"] == "victoria"          # offered escalation
    assert "ESCALATE" not in result["response"]       # sentinel not shown
    assert "s1" in mgr._pending_escalation


async def test_prose_plus_sentinel_triggers_offer_not_leak():
    """Regression: chatter with [ESCALATE] appended must offer, not leak the token."""
    router = MagicMock()
    router.chat = AsyncMock(return_value=(
        "A dramatic pause! I've reached the end of my rope. Shall I escalate? [ESCALATE]",
        "docker",
    ))
    mgr = make_manager(router)

    result = await mgr.chat("gold vs silver?", session_id="s1")
    assert result["backend"] == "victoria"
    assert "ESCALATE" not in result["response"]
    assert "end of my rope" not in result["response"]   # chatter suppressed too
    assert mgr._pending_escalation["s1"] == "gold vs silver?"


@pytest.mark.parametrize("text", [
    "Some waffle here. [ESCALATE]",
    "[escalate]",
    "I really can't say. [ ESCALATE ]",
])
def test_needs_escalation_detects_embedded_token(text):
    assert ConversationManager._needs_escalation(text) is True


async def test_stream_bracketless_sentinel_is_swallowed():
    router = MagicMock()
    router.stream_chat = _stream_of("ESCA", "LATE")   # arrives in pieces, no brackets
    mgr = make_manager(router)

    events = [e async for e in mgr.stream_chat("gold price?", session_id="s1")]
    text = "".join(e["chunk"] for e in events)
    assert "ESCA" not in text                          # never leaked to the user
    assert "?" in text                                 # asked permission instead
    assert mgr._pending_escalation["s1"] == "gold price?"


async def test_stream_prose_plus_sentinel_is_swallowed():
    """Streaming chatter that ends in [ESCALATE] must offer, never leak either part."""
    router = MagicMock()
    router.stream_chat = _stream_of("Well now, ", "let me think… ", "[ESCALATE]")
    mgr = make_manager(router)

    events = [e async for e in mgr.stream_chat("gold vs silver?", session_id="s1")]
    text = "".join(e["chunk"] for e in events)
    assert "ESCALATE" not in text
    assert "let me think" not in text                  # prose held back (full-buffer)
    assert mgr._pending_escalation["s1"] == "gold vs silver?"
    assert events[-1]["backend"] == "victoria"


# ---------------------------------------------------------------------------
# Streaming escalation flow
# ---------------------------------------------------------------------------

def _stream_of(*chunks, backend="docker"):
    async def gen(history, force_backend=None, system_prompt=None):
        for c in chunks:
            yield c, backend
    return gen


async def test_stream_normal_answer_flushes_and_completes():
    router = MagicMock()
    router.stream_chat = _stream_of("Hello ", "there.")
    mgr = make_manager(router)

    events = [e async for e in mgr.stream_chat("hi", session_id="s1")]
    text = "".join(e["chunk"] for e in events)
    assert "Hello there." in text
    assert events[-1]["done"] is True
    assert "s1" not in mgr._pending_escalation


async def test_stream_sentinel_offers_escalation():
    router = MagicMock()
    router.stream_chat = _stream_of(ESCALATION_SENTINEL)
    mgr = make_manager(router)

    events = [e async for e in mgr.stream_chat("hard one", session_id="s1")]
    text = "".join(e["chunk"] for e in events)

    assert ESCALATION_SENTINEL not in text          # sentinel is swallowed
    assert "?" in text                               # asks permission instead
    assert mgr._pending_escalation["s1"] == "hard one"
    assert events[-1]["backend"] == "victoria"


async def test_stream_yes_after_offer_escalates_to_claude():
    router = MagicMock()
    router.stream_chat = _stream_of(ESCALATION_SENTINEL)
    router.claude_cli = AsyncMock(return_value="Streamed Claude answer.")
    mgr = make_manager(router)

    [e async for e in mgr.stream_chat("hard one", session_id="s1")]
    events = [e async for e in mgr.stream_chat("yes", session_id="s1")]
    text = "".join(e["chunk"] for e in events)

    assert "Streamed Claude answer." in text
    assert events[-1]["backend"] == "claude"
    assert "s1" not in mgr._pending_escalation
    router.claude_cli.assert_awaited_once()
    assert router.claude_cli.await_args.args[0] == "hard one"


# ---------------------------------------------------------------------------
# LLMRouter.claude_cli — subprocess handling
# ---------------------------------------------------------------------------

async def test_claude_cli_returns_stdout():
    router = LLMRouter()

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"  PONG  ", b""))

    with patch("victoria.core.llm_router.asyncio.create_subprocess_exec",
               AsyncMock(return_value=proc)):
        out = await router.claude_cli("ping")
    assert out == "PONG"


async def test_claude_cli_passes_model_and_allowed_tools():
    router = LLMRouter()
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"ok", b""))
    spy = AsyncMock(return_value=proc)

    with patch("victoria.core.llm_router.asyncio.create_subprocess_exec", spy):
        await router.claude_cli("q", system_prompt="sys")

    args = list(spy.await_args.args)
    assert "-p" in args and "q" in args
    assert "--model" in args
    assert "--append-system-prompt" in args and "sys" in args
    assert "--allowedTools" in args
    assert "WebSearch" in args and "WebFetch" in args


async def test_claude_cli_nonzero_exit_raises():
    router = LLMRouter()

    proc = MagicMock()
    proc.returncode = 1
    proc.communicate = AsyncMock(return_value=(b"", b"boom"))

    with patch("victoria.core.llm_router.asyncio.create_subprocess_exec",
               AsyncMock(return_value=proc)):
        with pytest.raises(RuntimeError, match="boom"):
            await router.claude_cli("ping")


async def test_claude_cli_missing_binary_raises_helpful_error():
    router = LLMRouter()
    with patch("victoria.core.llm_router.asyncio.create_subprocess_exec",
               AsyncMock(side_effect=FileNotFoundError())):
        with pytest.raises(RuntimeError, match="not found on PATH"):
            await router.claude_cli("ping")
