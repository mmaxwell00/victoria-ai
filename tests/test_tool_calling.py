"""Integration tests for Sprint 5: tool-calling loop and semantic memory wiring."""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

from victoria.core.llm_router import LLMRouter
from victoria.core.conversation import ConversationManager
from victoria.config import VICTORIA_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_memory():
    """Return a minimal MemoryStore stub."""
    mem = MagicMock()
    mem.get_or_create_session.return_value = None
    mem.get_history.return_value = []
    mem.add_message.return_value = None
    return mem


def make_router():
    """Return a LLMRouter with mocked internals."""
    return LLMRouter()


def make_tool_registry(n_tools: int = 2):
    """Return a mock ToolRegistry with n_tools."""
    reg = MagicMock()
    reg.__len__ = MagicMock(return_value=n_tools)
    reg.get_anthropic_tools.return_value = []
    reg.get_ollama_tools.return_value = []
    reg.execute = AsyncMock()
    return reg


def make_semantic_memory(available: bool = True, search_results=None):
    """Return a mock SemanticMemory."""
    sem = MagicMock()
    sem.available = available
    sem.search.return_value = search_results or []
    sem.add.return_value = None
    return sem


# ---------------------------------------------------------------------------
# ConversationManager backward-compat test
# ---------------------------------------------------------------------------

async def test_conversation_manager_without_tools_backward_compat():
    """ConversationManager(memory, router) with no tool_registry stays backward compatible."""
    memory = make_memory()
    router = make_router()
    router.chat = AsyncMock(return_value=("Hello!", "ollama"))

    manager = ConversationManager(memory=memory, router=router)

    result = await manager.chat("Hi there", session_id="sess-1")

    assert result["response"] == "Hello!"
    assert result["backend"] == "ollama"
    assert result["session_id"] == "sess-1"
    router.chat.assert_awaited_once()
    # chat_with_tools should NOT have been called
    assert not hasattr(router, "chat_with_tools") or not getattr(router.chat_with_tools, "called", False)


# ---------------------------------------------------------------------------
# ConversationManager with tools → calls chat_with_tools
# ---------------------------------------------------------------------------

async def test_conversation_manager_with_tools_calls_chat_with_tools():
    """When tool_registry has tools, chat() must call chat_with_tools instead of chat."""
    memory = make_memory()
    router = make_router()
    router.chat = AsyncMock(return_value=("Should not be called", "ollama"))
    router.chat_with_tools = AsyncMock(return_value=("Weather is nice.", "claude"))

    mock_registry = make_tool_registry(n_tools=2)
    manager = ConversationManager(memory=memory, router=router, tool_registry=mock_registry)

    result = await manager.chat("What's the weather?", session_id="sess-2")

    assert result["response"] == "Weather is nice."
    assert result["backend"] == "claude"
    router.chat_with_tools.assert_awaited_once()
    router.chat.assert_not_called()


# ---------------------------------------------------------------------------
# Semantic context injection
# ---------------------------------------------------------------------------

async def test_conversation_manager_injects_semantic_context():
    """_build_system_prompt() injects retrieved memories into the system prompt."""
    memory = make_memory()
    router = make_router()
    router.chat = AsyncMock(return_value=("Got it.", "ollama"))

    mock_sem = make_semantic_memory(
        available=True,
        search_results=[{"content": "user likes Python", "role": "user", "session_id": "old-session"}],
    )

    manager = ConversationManager(memory=memory, router=router, semantic_memory=mock_sem)
    prompt = manager._build_system_prompt("Tell me about programming", session_id="new-session")

    assert "user likes Python" in prompt
    assert VICTORIA_SYSTEM_PROMPT in prompt


# ---------------------------------------------------------------------------
# Semantic memory persistence after chat
# ---------------------------------------------------------------------------

async def test_conversation_manager_stores_in_semantic_memory():
    """After chat(), semantic_memory.add() is called for both user and assistant messages."""
    memory = make_memory()
    router = make_router()
    router.chat = AsyncMock(return_value=("Brilliant!", "ollama"))

    mock_sem = make_semantic_memory(available=True, search_results=[])

    manager = ConversationManager(memory=memory, router=router, semantic_memory=mock_sem)
    await manager.chat("Hello, Victoria!", session_id="sess-3")

    assert mock_sem.add.call_count == 2
    calls = mock_sem.add.call_args_list
    # First call: user message
    assert calls[0][0][1] == "user"
    assert calls[0][0][2] == "Hello, Victoria!"
    # Second call: assistant message
    assert calls[1][0][1] == "assistant"
    assert calls[1][0][2] == "Brilliant!"


# ---------------------------------------------------------------------------
# LLMRouter: Claude tool-calling loop
# ---------------------------------------------------------------------------

async def test_llm_router_chat_with_tools_claude_loop():
    """Claude tool loop: tool_use → execute → end_turn returns final text."""
    router = make_router()

    # Build mock blocks
    tool_use_block = SimpleNamespace(
        type="tool_use",
        id="tu_1",
        name="calculate",
        input={"expression": "2+2"},
    )
    # First response: tool_use
    first_response = SimpleNamespace(
        stop_reason="tool_use",
        content=[tool_use_block],
    )

    text_block = SimpleNamespace(
        type="text",
        text="The answer is 4.",
    )
    # Second response: end_turn
    second_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[text_block],
    )

    mock_create = AsyncMock(side_effect=[first_response, second_response])

    mock_registry = make_tool_registry()
    mock_registry.get_anthropic_tools.return_value = [
        {"name": "calculate", "description": "Evaluate a math expression", "input_schema": {}}
    ]
    mock_registry.execute = AsyncMock(return_value="2+2 = 4")

    # Patch the anthropic client
    mock_anthropic = MagicMock()
    mock_anthropic.messages.create = mock_create
    router._anthropic = mock_anthropic

    # Force Claude backend
    text, backend = await router.chat_with_tools(
        messages=[{"role": "user", "content": "What is 2+2?"}],
        tool_registry=mock_registry,
        system_prompt=VICTORIA_SYSTEM_PROMPT,
        force_backend="claude",
    )

    assert text == "The answer is 4."
    assert backend == "claude"
    mock_registry.execute.assert_awaited_once_with("calculate", expression="2+2")


# ---------------------------------------------------------------------------
# LLMRouter: Ollama tool-calling loop
# ---------------------------------------------------------------------------

async def test_llm_router_chat_with_tools_ollama_loop():
    """Ollama tool loop: tool_calls → execute → no tool_calls returns final text."""
    router = make_router()

    # First response has tool_calls
    first_response_data = {
        "message": {
            "tool_calls": [
                {"function": {"name": "get_datetime", "arguments": {"timezone": "UTC"}}}
            ],
            "content": "",
        }
    }
    # Second response has no tool_calls
    second_response_data = {
        "message": {
            "content": "It's Monday.",
            "tool_calls": [],
        }
    }

    mock_registry = make_tool_registry()
    mock_registry.get_ollama_tools.return_value = [
        {"type": "function", "function": {"name": "get_datetime", "description": "Get date/time", "parameters": {}}}
    ]
    mock_registry.execute = AsyncMock(return_value="Monday, 01 January 2026 at 12:00 PM UTC")

    call_count = 0

    class MockResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    class MockAsyncClient:
        is_closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockResponse(first_response_data)
            return MockResponse(second_response_data)

    with patch("victoria.core.llm_router.httpx.AsyncClient", return_value=MockAsyncClient()):
        text, backend = await router.chat_with_tools(
            messages=[{"role": "user", "content": "What day is it?"}],
            tool_registry=mock_registry,
            system_prompt=VICTORIA_SYSTEM_PROMPT,
            force_backend="ollama",
        )

    assert text == "It's Monday."
    assert backend == "ollama"
    mock_registry.execute.assert_awaited_once_with("get_datetime", timezone="UTC")


# ---------------------------------------------------------------------------
# Refusal detection + forced-tool retry (docker backend)
# ---------------------------------------------------------------------------

def test_looks_like_tool_refusal_matches_real_refusals():
    from victoria.core.llm_router import _looks_like_tool_refusal
    assert _looks_like_tool_refusal(
        "I'm currently unable to fetch real-time weather data or access external weather APIs directly."
    )
    assert _looks_like_tool_refusal(
        "I can't provide real-time weather data or access external APIs to fetch the current temperature."
    )
    assert _looks_like_tool_refusal("I don't have access to real-time information.")


def test_looks_like_tool_refusal_ignores_normal_answers():
    from victoria.core.llm_router import _looks_like_tool_refusal
    # A real tool-backed answer must NOT look like a refusal.
    assert not _looks_like_tool_refusal(
        "The current temperature in Dallas is 57°F with partly cloudy skies."
    )
    assert not _looks_like_tool_refusal("The capital of France is Paris.")
    assert not _looks_like_tool_refusal("I can fetch that for you right now.")
    assert not _looks_like_tool_refusal("")


async def test_docker_with_tools_forces_tool_on_refusal():
    """A refusal with no tool call → retry once with tool_choice=required, then
    the forced tool runs and the final answer returns."""
    router = make_router()

    refusal = {"choices": [{"message": {
        "content": "I'm currently unable to fetch real-time weather data or access external APIs.",
        "tool_calls": [],
    }}]}
    forced_call = {"choices": [{"message": {
        "content": "",
        "tool_calls": [{"id": "tc1", "function": {"name": "get_weather", "arguments": {"location": "Dallas"}}}],
    }}]}
    final = {"choices": [{"message": {"content": "It's 57°F in Dallas.", "tool_calls": []}}]}

    mock_registry = make_tool_registry()
    mock_registry.get_ollama_tools.return_value = [
        {"type": "function", "function": {"name": "get_weather", "description": "weather", "parameters": {}}}
    ]
    mock_registry.execute = AsyncMock(return_value="Dallas: 57°F")

    payloads = []

    class MockResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    class MockAsyncClient:
        is_closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, **kwargs):
            payloads.append(json)
            n = len(payloads)
            return MockResponse(refusal if n == 1 else forced_call if n == 2 else final)

    with patch("victoria.core.llm_router.httpx.AsyncClient", return_value=MockAsyncClient()):
        text, backend = await router.chat_with_tools(
            messages=[{"role": "user", "content": "temperature in Dallas today?"}],
            tool_registry=mock_registry,
            system_prompt=VICTORIA_SYSTEM_PROMPT,
            force_backend="docker",
        )

    assert text == "It's 57°F in Dallas."
    assert backend == "docker"
    mock_registry.execute.assert_awaited_once_with("get_weather", location="Dallas")
    # Only the retry (2nd call) forces a tool; first and final passes don't.
    assert len(payloads) == 3
    assert "tool_choice" not in payloads[0]
    assert payloads[1].get("tool_choice") == "required"
    assert "tool_choice" not in payloads[2]


async def test_docker_with_tools_no_retry_when_answer_is_fine():
    """A normal (non-refusal) direct answer with no tool call returns as-is —
    no forced retry, single request."""
    router = make_router()

    ok = {"choices": [{"message": {"content": "The capital of France is Paris.", "tool_calls": []}}]}

    mock_registry = make_tool_registry()
    mock_registry.get_ollama_tools.return_value = [
        {"type": "function", "function": {"name": "get_weather", "description": "weather", "parameters": {}}}
    ]

    payloads = []

    class MockResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    class MockAsyncClient:
        is_closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, **kwargs):
            payloads.append(json)
            return MockResponse(ok)

    with patch("victoria.core.llm_router.httpx.AsyncClient", return_value=MockAsyncClient()):
        text, backend = await router.chat_with_tools(
            messages=[{"role": "user", "content": "capital of France?"}],
            tool_registry=mock_registry,
            system_prompt=VICTORIA_SYSTEM_PROMPT,
            force_backend="docker",
        )

    assert text == "The capital of France is Paris."
    assert len(payloads) == 1  # no forced retry
    assert "tool_choice" not in payloads[0]


# ---------------------------------------------------------------------------
# stream_chat passes system_prompt with semantic context
# ---------------------------------------------------------------------------

async def test_stream_chat_uses_system_prompt():
    """stream_chat() passes the enriched system_prompt (with semantic context)
    to the local model. The streaming local path routes through _local_answer
    (so the model keeps its TOOLS); with no tool_registry that lands on
    router.chat()."""
    memory = make_memory()
    router = make_router()

    captured_kwargs = {}

    async def fake_chat(messages, force_backend=None, system_prompt=None):
        captured_kwargs["system_prompt"] = system_prompt
        return "It's Monday.", "ollama"

    router.chat = fake_chat

    mock_sem = make_semantic_memory(
        available=True,
        search_results=[{"content": "user is based in London", "role": "user", "session_id": "past"}],
    )

    manager = ConversationManager(memory=memory, router=router, semantic_memory=mock_sem)

    chunks = []
    async for chunk in manager.stream_chat("What time is it?", session_id="sess-4"):
        chunks.append(chunk)

    assert "system_prompt" in captured_kwargs
    assert captured_kwargs["system_prompt"] is not None
    assert "user is based in London" in captured_kwargs["system_prompt"]
    assert VICTORIA_SYSTEM_PROMPT in captured_kwargs["system_prompt"]
