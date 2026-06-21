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


def _make_update(text="hello", user_id=42, first_name="Mark"):
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
    assert "Mark" in call_text
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
