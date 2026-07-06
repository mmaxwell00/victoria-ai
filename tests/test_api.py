import pytest
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from victoria.main import app
from victoria.interfaces.api import get_manager


@pytest.fixture
def mock_manager():
    mgr = MagicMock()
    mgr.chat = AsyncMock(return_value={
        "session_id": "test-session-123",
        "response": "Hello darling, how delightful!",
        "backend": "ollama",
    })

    async def _stream(*args, **kwargs):
        yield {"session_id": "test-session-123", "chunk": "Hello ", "backend": "ollama", "done": False}
        yield {"session_id": "test-session-123", "chunk": "darling!", "backend": "ollama", "done": False}
        yield {"session_id": "test-session-123", "chunk": "", "backend": "ollama", "done": True}

    mgr.stream_chat = _stream
    mgr.memory.list_sessions = MagicMock(return_value=[])
    mgr.memory.get_history = MagicMock(return_value=[])
    return mgr


@pytest.fixture
def client(mock_manager):
    app.dependency_overrides[get_manager] = lambda: mock_manager
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health(client):
    async with client as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_index_page(client):
    async with client as c:
        resp = await c.get("/")
    assert resp.status_code == 200
    assert "VICTORIA" in resp.text


@pytest.mark.asyncio
async def test_chat_endpoint(client, mock_manager):
    async with client as c:
        resp = await c.post("/v1/chat", json={"message": "Hello Victoria"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["response"] == "Hello darling, how delightful!"
    assert data["backend"] == "ollama"
    assert data["session_id"] == "test-session-123"
    mock_manager.chat.assert_called_once()
    call_kwargs = mock_manager.chat.call_args[1]
    assert call_kwargs["user_message"] == "Hello Victoria"
    assert call_kwargs["channel"] == "api"


@pytest.mark.asyncio
async def test_chat_with_forced_backend(client, mock_manager):
    async with client as c:
        resp = await c.post("/v1/chat", json={"message": "Hard question", "backend": "claude"})
    assert resp.status_code == 200
    call_kwargs = mock_manager.chat.call_args[1]
    assert call_kwargs["force_backend"] == "claude"


@pytest.mark.asyncio
async def test_stream_endpoint(client):
    async with client as c:
        resp = await c.post("/v1/chat/stream", json={"message": "Hello"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    lines = [l for l in resp.text.split("\n") if l.startswith("data: ")]
    assert len(lines) == 3  # two chunks + done


@pytest.mark.asyncio
async def test_sessions_endpoint(client):
    async with client as c:
        resp = await c.get("/v1/sessions/mark")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_history_endpoint_checks_session_ownership(client_and_manager=None):
    """Regression: /v1/sessions/{user}/{session}/history must 404 when the
    session does not belong to that user (previously user_id was ignored)."""
    import tempfile, os
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from victoria.core.memory import MemoryStore
    from victoria.core.conversation import ConversationManager
    from victoria.interfaces.api import router, get_manager

    with tempfile.TemporaryDirectory() as tmp:
        memory = MemoryStore(db_path=os.path.join(tmp, "t.db"))
        memory.get_or_create_session("sess-a", "alice")
        memory.add_message("sess-a", "user", "secret question")

        mgr = ConversationManager(memory=memory, router=MagicMock())
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_manager] = lambda: mgr
        client = TestClient(app)

        # Owner can read
        resp = client.get("/v1/sessions/alice/sess-a/history")
        assert resp.status_code == 200
        assert resp.json()[0]["content"] == "secret question"

        # Another user cannot
        resp = client.get("/v1/sessions/mallory/sess-a/history")
        assert resp.status_code == 404

        # Unknown session 404s
        resp = client.get("/v1/sessions/alice/no-such-session/history")
        assert resp.status_code == 404
