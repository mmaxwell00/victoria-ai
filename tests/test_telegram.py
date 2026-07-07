import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
import os


# ------------------------------------------------------------------ #
# Session ID helper                                                    #
# ------------------------------------------------------------------ #

def test_session_id_default():
    from victoria.interfaces.telegram_bot import _session_id
    ctx = MagicMock()
    ctx.user_data = {}
    assert _session_id("42", ctx) == "tg-42"


def test_session_id_custom():
    from victoria.interfaces.telegram_bot import _session_id
    ctx = MagicMock()
    ctx.user_data = {"session_id": "tg-42-1234567890"}
    assert _session_id("42", ctx) == "tg-42-1234567890"


# ------------------------------------------------------------------ #
# Command handlers                                                     #
# ------------------------------------------------------------------ #

@pytest.fixture
def bot():
    from victoria.interfaces.telegram_bot import VictoriaTelegramBot
    manager = MagicMock()
    return VictoriaTelegramBot(manager=manager)


def _make_update(text="hello", user_id=42, first_name="Alex"):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = first_name
    update.message.text = text
    update.message.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    return update


def _make_context(session_id=None, force_backend=None):
    ctx = MagicMock()
    ctx.user_data = {}
    if session_id:
        ctx.user_data["session_id"] = session_id
    if force_backend:
        ctx.user_data["force_backend"] = force_backend
    ctx.args = []
    return ctx


@pytest.mark.asyncio
async def test_cmd_start_greets_user(bot):
    update = _make_update()
    ctx = _make_context()
    await bot.cmd_start(update, ctx)
    update.message.reply_text.assert_called_once()
    call_text = update.message.reply_text.call_args[0][0]
    assert "Alex" in call_text
    assert "Victoria" in call_text


@pytest.mark.asyncio
async def test_cmd_new_creates_fresh_session(bot):
    update = _make_update()
    ctx = _make_context(session_id="tg-42-old", force_backend="claude")
    await bot.cmd_new(update, ctx)
    assert ctx.user_data["session_id"] != "tg-42-old"
    assert ctx.user_data["session_id"].startswith("tg-42-")
    assert "force_backend" not in ctx.user_data


@pytest.mark.asyncio
async def test_cmd_backend_valid(bot):
    update = _make_update()
    ctx = _make_context()
    ctx.args = ["claude"]
    await bot.cmd_backend(update, ctx)
    assert ctx.user_data["force_backend"] == "claude"


@pytest.mark.asyncio
async def test_cmd_backend_invalid(bot):
    update = _make_update()
    ctx = _make_context()
    ctx.args = ["gpt4"]
    await bot.cmd_backend(update, ctx)
    assert "force_backend" not in ctx.user_data


# ------------------------------------------------------------------ #
# Text handler                                                         #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_handle_text_calls_manager(bot):
    bot.manager.chat = AsyncMock(return_value={
        "session_id": "tg-42",
        "response": "Hello darling!",
        "backend": "ollama",
    })
    update = _make_update(text="Good morning")
    ctx = _make_context()
    await bot.handle_text(update, ctx)
    bot.manager.chat.assert_called_once()
    call_kwargs = bot.manager.chat.call_args[1]
    assert call_kwargs["user_message"] == "Good morning"
    assert call_kwargs["channel"] == "telegram"


@pytest.mark.asyncio
async def test_handle_text_uses_force_backend(bot):
    bot.manager.chat = AsyncMock(return_value={
        "session_id": "tg-42",
        "response": "Certainly!",
        "backend": "claude",
    })
    update = _make_update(text="Explain quantum entanglement")
    ctx = _make_context(force_backend="claude")
    await bot.handle_text(update, ctx)
    call_kwargs = bot.manager.chat.call_args[1]
    assert call_kwargs["force_backend"] == "claude"


# ------------------------------------------------------------------ #
# Voice handler — Markdown safety                                      #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_handle_voice_no_parse_mode_with_dynamic_text(bot):
    """Regression: transcriptions with Markdown metacharacters (_ * `) must
    not be sent with a parse_mode, or Telegram rejects the message."""
    tricky = "snake_case_name and *stars* and `ticks`"

    bot.manager.chat = AsyncMock(return_value={
        "session_id": "tg-42",
        "response": "Got it — some_response_with_underscores",
        "backend": "ollama",
    })

    update = _make_update()
    update.message.voice.file_id = "voice-file-1"
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=status_msg)

    ctx = _make_context()
    ctx.bot.get_file = AsyncMock(return_value=MagicMock(download_to_drive=AsyncMock()))

    with patch("victoria.core.transcription.transcribe_audio", new=AsyncMock(return_value=tricky)):
        await bot.handle_voice(update, ctx)

    # Every edit containing the transcription or model response must be plain text
    for call in status_msg.edit_text.call_args_list:
        text = call.args[0] if call.args else call.kwargs.get("text", "")
        if tricky in text or "some_response" in text:
            assert call.kwargs.get("parse_mode") is None, f"parse_mode used with dynamic text: {call!r}"
    # Final message contains both transcription and response
    final = status_msg.edit_text.call_args_list[-1]
    final_text = final.args[0] if final.args else final.kwargs.get("text", "")
    assert tricky in final_text and "some_response_with_underscores" in final_text


@pytest.mark.asyncio
async def test_cmd_remember_no_parse_mode(bot):
    update = _make_update()
    ctx = _make_context()
    ctx.args = ["I", "use", "snake_case_naming"]
    bot.manager.profile_store = MagicMock()
    await bot.cmd_remember(update, ctx)
    call = update.message.reply_text.call_args
    assert "snake_case_naming" in call.args[0]
    assert call.kwargs.get("parse_mode") is None
