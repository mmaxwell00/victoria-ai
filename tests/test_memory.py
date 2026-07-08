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
    session = store.get_or_create_session("sess-1", user_id="alex", channel="api")
    assert session["id"] == "sess-1"
    assert session["user_id"] == "alex"


def test_idempotent_session(store):
    store.get_or_create_session("sess-1", user_id="alex")
    store.get_or_create_session("sess-1", user_id="alex")
    # A session only appears in the list once it has at least one message.
    store.add_message("sess-1", "user", "hi")
    sessions = store.list_sessions("alex")
    assert len(sessions) == 1


def test_list_sessions_omits_empty_and_carries_title(store):
    """list_sessions skips message-less sessions and titles each from its
    first user message, with a message count."""
    store.get_or_create_session("empty", user_id="alex")          # no messages
    store.get_or_create_session("chat", user_id="alex")
    store.add_message("chat", "user", "Fix the Model Runner port please")
    store.add_message("chat", "assistant", "Right away.", llm_used="docker")

    sessions = store.list_sessions("alex")
    ids = {s["id"] for s in sessions}
    assert "empty" not in ids and "chat" in ids
    chat = next(s for s in sessions if s["id"] == "chat")
    assert chat["title"] == "Fix the Model Runner port please"
    assert chat["message_count"] == 2


def test_session_title_set_once_from_first_user_message(store):
    store.get_or_create_session("s", user_id="alex")
    store.add_message("s", "user", "First question")
    store.add_message("s", "assistant", "answer")
    store.add_message("s", "user", "Second question")
    title = store.list_sessions("alex")[0]["title"]
    assert title == "First question"  # not overwritten by the later message


def test_long_title_is_truncated(store):
    store.get_or_create_session("s", user_id="alex")
    store.add_message("s", "user", "x" * 100)
    title = store.list_sessions("alex")[0]["title"]
    assert title.endswith("…") and len(title) <= 49


def test_add_and_retrieve_messages(store):
    store.get_or_create_session("sess-2", user_id="alex")
    store.add_message("sess-2", "user", "Hello Victoria")
    store.add_message("sess-2", "assistant", "Hello, darling!", llm_used="ollama")

    history = store.get_history("sess-2")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["content"] == "Hello, darling!"


def test_history_limit(store):
    store.get_or_create_session("sess-3", user_id="alex")
    for i in range(30):
        store.add_message("sess-3", "user", f"msg {i}")

    history = store.get_history("sess-3", limit=20)
    assert len(history) == 20


def test_get_history_window_starts_on_user_message(tmp_path):
    """Regression: when the limit slices mid-conversation, leading assistant
    messages must be dropped (Anthropic requires the first message be 'user')."""
    store = MemoryStore(db_path=str(tmp_path / "trim.db"))
    store.get_or_create_session("s1", "u1")
    # 6 turns = 12 messages; limit=5 would start on an assistant message
    for i in range(6):
        store.add_message("s1", "user", f"question {i}")
        store.add_message("s1", "assistant", f"answer {i}")

    history = store.get_history("s1", limit=5)
    assert history, "history should not be empty"
    assert history[0]["role"] == "user"
    # Last message is still the most recent one
    assert history[-1]["content"] == "answer 5"


def test_get_history_all_assistant_returns_empty(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "trim2.db"))
    store.get_or_create_session("s2", "u1")
    store.add_message("s2", "assistant", "unprompted remark")
    assert store.get_history("s2") == []


def test_wal_mode_and_concurrent_writes(tmp_path):
    """Both stores share one DB file; concurrent writes must not raise
    'database is locked'."""
    import threading
    from victoria.core.user_profile import ProfileStore

    db = str(tmp_path / "shared.db")
    mem = MemoryStore(db_path=db)
    prof = ProfileStore(db_path=db)

    assert mem.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    mem.get_or_create_session("s1", "u1")
    errors = []

    def write_messages():
        try:
            for i in range(50):
                mem.add_message("s1", "user", f"m{i}")
        except Exception as e:
            errors.append(e)

    def write_profiles():
        try:
            for i in range(50):
                prof.add_memory("u1", f"memory {i}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write_messages), threading.Thread(target=write_profiles)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert not errors, f"concurrent writes failed: {errors}"
    assert len(prof.get("u1").explicit_memories) == 50
