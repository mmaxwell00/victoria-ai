import pytest
import tempfile
import os
from victoria.core.memory import MemoryStore


@pytest.fixture
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield MemoryStore(db_path=path)
    os.unlink(path)


def test_create_session(store):
    session = store.get_or_create_session("sess-1", user_id="mark", channel="api")
    assert session["id"] == "sess-1"
    assert session["user_id"] == "mark"


def test_idempotent_session(store):
    store.get_or_create_session("sess-1", user_id="mark")
    store.get_or_create_session("sess-1", user_id="mark")
    sessions = store.list_sessions("mark")
    assert len(sessions) == 1


def test_add_and_retrieve_messages(store):
    store.get_or_create_session("sess-2", user_id="mark")
    store.add_message("sess-2", "user", "Hello Victoria")
    store.add_message("sess-2", "assistant", "Hello, darling!", llm_used="ollama")

    history = store.get_history("sess-2")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["content"] == "Hello, darling!"


def test_history_limit(store):
    store.get_or_create_session("sess-3", user_id="mark")
    for i in range(30):
        store.add_message("sess-3", "user", f"msg {i}")

    history = store.get_history("sess-3", limit=20)
    assert len(history) == 20
