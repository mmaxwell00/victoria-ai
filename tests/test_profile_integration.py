import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from victoria.core.conversation import ConversationManager
from victoria.config import VICTORIA_SYSTEM_PROMPT


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _make_manager(
    profile_store=None,
    profile_extractor=None,
    router=None,
    semantic_memory=None,
):
    memory = MagicMock()
    memory.get_or_create_session = MagicMock()
    memory.get_history = MagicMock(return_value=[])
    memory.add_message = MagicMock()

    if router is None:
        router = MagicMock()
        router.chat = AsyncMock(return_value=("Sure thing!", "ollama"))

    return ConversationManager(
        memory=memory,
        router=router,
        semantic_memory=semantic_memory,
        profile_store=profile_store,
        profile_extractor=profile_extractor,
    )


def _make_profile(name="Alex", empty=False):
    profile = MagicMock()
    profile.name = name
    profile.is_empty = MagicMock(return_value=empty)
    if empty:
        profile.to_system_context = MagicMock(return_value="")
    else:
        profile.to_system_context = MagicMock(
            return_value=f"About this user:\nThe user's name is {name}."
        )
    return profile


# ------------------------------------------------------------------ #
# _build_system_prompt                                                 #
# ------------------------------------------------------------------ #

def test_build_system_prompt_includes_profile():
    profile = _make_profile(name="Alex")
    profile_store = MagicMock()
    profile_store.get = MagicMock(return_value=profile)

    manager = _make_manager(profile_store=profile_store)
    result = manager._build_system_prompt("hello", "sess-1", "alex")

    assert "Alex" in result


def test_build_system_prompt_empty_profile_unchanged():
    profile = _make_profile(empty=True)
    profile_store = MagicMock()
    profile_store.get = MagicMock(return_value=profile)

    manager = _make_manager(profile_store=profile_store)
    result = manager._build_system_prompt("hello", "sess-1", "alex")

    # Empty profile adds no profile context; the persona still leads the prompt.
    # (A skills section is appended unconditionally — see test_skills.py.)
    assert result.startswith(VICTORIA_SYSTEM_PROMPT)
    assert "Relevant context from past conversations" not in result


# ------------------------------------------------------------------ #
# chat() — explicit memory detection                                   #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_chat_detects_explicit_memory():
    user_message = "remember that I prefer bullet points"

    profile_extractor = MagicMock()
    profile_extractor.detect_explicit_memory = MagicMock(return_value="I prefer bullet points")
    profile_extractor.extract_from_turn = AsyncMock(return_value=_make_profile())

    profile_store = MagicMock()
    profile_store.get = MagicMock(return_value=_make_profile())
    profile_store.add_memory = MagicMock()
    profile_store.save = MagicMock()

    router = MagicMock()
    router.chat = AsyncMock(return_value=("Got it!", "ollama"))

    manager = _make_manager(
        profile_store=profile_store,
        profile_extractor=profile_extractor,
        router=router,
    )

    await manager.chat(user_message=user_message, user_id="default")

    profile_store.add_memory.assert_called_once_with("default", "I prefer bullet points")


# ------------------------------------------------------------------ #
# chat() — background profile update                                   #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_chat_fires_background_profile_update():
    profile = _make_profile()
    updated_profile = _make_profile(name="Alex Updated")
    updated_profile.is_empty = MagicMock(return_value=False)

    profile_extractor = MagicMock()
    profile_extractor.detect_explicit_memory = MagicMock(return_value=None)
    profile_extractor.extract_from_turn = AsyncMock(return_value=updated_profile)

    profile_store = MagicMock()
    profile_store.get = MagicMock(return_value=profile)
    profile_store.save = MagicMock()
    profile_store.add_memory = MagicMock()

    router = MagicMock()
    router.chat = AsyncMock(return_value=("Hello!", "ollama"))

    manager = _make_manager(
        profile_store=profile_store,
        profile_extractor=profile_extractor,
        router=router,
    )

    await manager.chat(user_message="Tell me something", user_id="default")
    # Let the event loop run the created task
    await asyncio.sleep(0)

    assert profile_extractor.extract_from_turn.called


# ------------------------------------------------------------------ #
# stream_chat() — background profile update                            #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_stream_chat_fires_background_profile_update():
    profile = _make_profile()
    updated_profile = _make_profile(name="Alex Updated")
    updated_profile.is_empty = MagicMock(return_value=False)

    profile_extractor = MagicMock()
    profile_extractor.detect_explicit_memory = MagicMock(return_value=None)
    profile_extractor.extract_from_turn = AsyncMock(return_value=updated_profile)

    profile_store = MagicMock()
    profile_store.get = MagicMock(return_value=profile)
    profile_store.save = MagicMock()
    profile_store.add_memory = MagicMock()

    # The streaming local path routes through _local_answer (buffered tool
    # loop), which with no tool_registry lands on router.chat().
    router = MagicMock()
    router.chat = AsyncMock(return_value=("Hello world", "ollama"))

    manager = _make_manager(
        profile_store=profile_store,
        profile_extractor=profile_extractor,
        router=router,
    )

    # Drain the async generator fully
    chunks = []
    async for chunk in manager.stream_chat(user_message="Hello", user_id="default"):
        chunks.append(chunk)

    # Let the event loop run the created task
    await asyncio.sleep(0)

    assert profile_extractor.extract_from_turn.called


# ------------------------------------------------------------------ #
# Telegram command handlers                                            #
# ------------------------------------------------------------------ #

def _make_update(text="hello", user_id=42, first_name="Alex"):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = first_name
    update.message.text = text
    update.message.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    return update


def _make_context(args=None):
    ctx = MagicMock()
    ctx.user_data = {}
    ctx.args = args or []
    return ctx


def _make_bot(profile_store=None):
    from victoria.interfaces.telegram_bot import VictoriaTelegramBot
    manager = MagicMock()
    manager.profile_store = profile_store
    return VictoriaTelegramBot(manager=manager)


@pytest.mark.asyncio
async def test_telegram_cmd_remember_stores_memory():
    profile_store = MagicMock()
    profile_store.add_memory = MagicMock()

    bot = _make_bot(profile_store=profile_store)
    update = _make_update(user_id=99)
    ctx = _make_context(args=["I", "prefer", "dark", "mode"])

    await bot.cmd_remember(update, ctx)

    profile_store.add_memory.assert_called_once_with("99", "I prefer dark mode")


@pytest.mark.asyncio
async def test_telegram_cmd_forget_found():
    profile_store = MagicMock()
    profile_store.forget_memory = MagicMock(return_value=True)

    bot = _make_bot(profile_store=profile_store)
    update = _make_update(user_id=99)
    ctx = _make_context(args=["I", "prefer", "dark", "mode"])

    await bot.cmd_forget(update, ctx)

    call_text = update.message.reply_text.call_args[0][0]
    assert "forgotten" in call_text


@pytest.mark.asyncio
async def test_telegram_cmd_forget_not_found():
    profile_store = MagicMock()
    profile_store.forget_memory = MagicMock(return_value=False)

    bot = _make_bot(profile_store=profile_store)
    update = _make_update(user_id=99)
    ctx = _make_context(args=["something", "I", "never", "said"])

    await bot.cmd_forget(update, ctx)

    call_text = update.message.reply_text.call_args[0][0]
    assert "don't seem to have" in call_text


@pytest.mark.asyncio
async def test_telegram_cmd_profile_with_data():
    profile = MagicMock()
    profile.to_system_context = MagicMock(
        return_value="About this user:\nThe user's name is Alex."
    )

    profile_store = MagicMock()
    profile_store.get = MagicMock(return_value=profile)

    bot = _make_bot(profile_store=profile_store)
    update = _make_update(user_id=99)
    ctx = _make_context()

    await bot.cmd_profile(update, ctx)

    call_text = update.message.reply_text.call_args[0][0]
    assert "Alex" in call_text


@pytest.mark.asyncio
async def test_telegram_cmd_profile_empty():
    profile = MagicMock()
    profile.to_system_context = MagicMock(return_value="")

    profile_store = MagicMock()
    profile_store.get = MagicMock(return_value=profile)

    bot = _make_bot(profile_store=profile_store)
    update = _make_update(user_id=99)
    ctx = _make_context()

    await bot.cmd_profile(update, ctx)

    call_text = update.message.reply_text.call_args[0][0]
    assert "don't know much" in call_text


async def test_background_profile_task_is_referenced():
    """Regression: fire-and-forget profile tasks must be held in a strong
    reference set until done, so they can't be garbage-collected mid-run."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from victoria.core.conversation import ConversationManager

    manager = ConversationManager(
        memory=MagicMock(),
        router=MagicMock(),
        profile_store=MagicMock(),
        profile_extractor=MagicMock(),
    )

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_update(*a, **kw):
        started.set()
        await release.wait()

    manager._update_profile_async = slow_update
    manager._spawn_profile_update("u1", "msg", "resp")

    await started.wait()
    assert len(manager._background_tasks) == 1

    release.set()
    await asyncio.gather(*manager._background_tasks)
    await asyncio.sleep(0)  # let done-callbacks run
    assert len(manager._background_tasks) == 0
