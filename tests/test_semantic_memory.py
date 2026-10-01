"""Tests for victoria.core.semantic_memory.SemanticMemory.

Unit tests mock out chromadb entirely; the integration test uses a real
in-memory/temp-path ChromaDB and is skipped if the package isn't installed.
"""
import sys
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chromadb_mock(count_return=0):
    """Return a minimal chromadb stub whose collection.count() returns count_return."""
    mock_collection = MagicMock()
    mock_collection.count.return_value = count_return

    mock_client = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_collection

    mock_chromadb = MagicMock()
    mock_chromadb.PersistentClient.return_value = mock_client

    return mock_chromadb, mock_client, mock_collection


def _load_fresh_semantic_memory():
    """Force a fresh import of semantic_memory (removes cached module)."""
    for key in list(sys.modules.keys()):
        if "semantic_memory" in key:
            del sys.modules[key]
    from victoria.core import semantic_memory  # noqa: F401 — side-effect import
    return importlib.import_module("victoria.core.semantic_memory")


# ---------------------------------------------------------------------------
# 1. Unavailable when chromadb is missing
# ---------------------------------------------------------------------------

def test_semantic_memory_unavailable_when_chromadb_missing(tmp_path):
    """SemanticMemory degrades gracefully when chromadb cannot be imported."""
    # Remove chromadb from sys.modules so the import inside __init__ fails
    saved = sys.modules.pop("chromadb", None)
    try:
        # Patch builtins.__import__ is fragile across reimports; the cleaner
        # approach is to keep chromadb out of sys.modules and make the name
        # unresolvable by temporarily replacing it with a broken sentinel.
        broken = types.ModuleType("chromadb")

        def _raise(*a, **kw):
            raise ImportError("chromadb not available")

        broken.PersistentClient = _raise
        sys.modules["chromadb"] = broken

        # Reload the module so __init__ re-runs the import path
        mod = _load_fresh_semantic_memory()
        SemanticMemory = mod.SemanticMemory

        mem = SemanticMemory(db_path=str(tmp_path / "chroma"))

        assert mem.available is False
        assert mem.search("hello") == []
        mem.add("s1", "user", "hello")  # must not raise
    finally:
        # Restore original state
        if saved is not None:
            sys.modules["chromadb"] = saved
        else:
            sys.modules.pop("chromadb", None)
        # Reload with real chromadb present so later tests are unaffected
        _load_fresh_semantic_memory()


# ---------------------------------------------------------------------------
# 2. Integration test — real ChromaDB with temp directory
# ---------------------------------------------------------------------------

def test_semantic_memory_add_and_search(tmp_path):
    """Integration: add messages and search; requires chromadb installed."""
    pytest.importorskip("chromadb")

    from victoria.core.semantic_memory import SemanticMemory

    mem = SemanticMemory(db_path=str(tmp_path / "chroma"))

    # If chromadb initialised but no default embedder is available the
    # instance may still be marked available=False — that's acceptable.
    if not mem.available:
        pytest.skip("SemanticMemory initialised but not available (no embedder)")

    mem.add("s1", "user", "The capital of France is Paris")
    mem.add("s1", "assistant", "Correct, Paris is the capital of France")
    mem.add("s2", "user", "What is the weather like today?")

    # Search must not raise; results may be empty if embedding model absent
    results = mem.search("French capital city", n=3)
    assert isinstance(results, list)
    for r in results:
        assert "content" in r
        assert "role" in r
        assert "session_id" in r


# ---------------------------------------------------------------------------
# 3. search returns [] on empty collection
# ---------------------------------------------------------------------------

def test_search_returns_empty_on_empty_db(tmp_path):
    """search() returns [] when the collection has no documents."""
    mock_chromadb, _client, mock_collection = _make_chromadb_mock(count_return=0)

    with patch.dict(sys.modules, {"chromadb": mock_chromadb}):
        mod = _load_fresh_semantic_memory()
        SemanticMemory = mod.SemanticMemory

        mem = SemanticMemory(db_path=str(tmp_path / "chroma"))

    assert mem.search("anything") == []
    mock_collection.query.assert_not_called()


# ---------------------------------------------------------------------------
# 4. add() skips empty content
# ---------------------------------------------------------------------------

def test_add_skips_empty_content(tmp_path):
    """add() must not call collection.add when content is blank."""
    mock_chromadb, _client, mock_collection = _make_chromadb_mock(count_return=0)

    with patch.dict(sys.modules, {"chromadb": mock_chromadb}):
        mod = _load_fresh_semantic_memory()
        SemanticMemory = mod.SemanticMemory

        mem = SemanticMemory(db_path=str(tmp_path / "chroma"))

    mem.add("s1", "user", "")
    mem.add("s1", "user", "   ")
    mock_collection.add.assert_not_called()


# ---------------------------------------------------------------------------
# 5. count() returns 0 when unavailable
# ---------------------------------------------------------------------------

def test_count_returns_zero_when_unavailable(tmp_path):
    """count() must return 0 when SemanticMemory is not available."""
    saved = sys.modules.pop("chromadb", None)
    try:
        broken = types.ModuleType("chromadb")

        def _raise(*a, **kw):
            raise ImportError("chromadb not available")

        broken.PersistentClient = _raise
        sys.modules["chromadb"] = broken

        mod = _load_fresh_semantic_memory()
        SemanticMemory = mod.SemanticMemory

        mem = SemanticMemory(db_path=str(tmp_path / "chroma"))
        assert mem.count() == 0
    finally:
        if saved is not None:
            sys.modules["chromadb"] = saved
        else:
            sys.modules.pop("chromadb", None)
        _load_fresh_semantic_memory()


# ---------------------------------------------------------------------------
# 6. Stale live data must never be recalled as a fact
#
# Regression: "What's the weather in London today?" was answered for weeks with
# an August 4 reading. Recall surfaced the old answer (undated), the local model
# repeated it instead of calling get_weather, and each repeat was stored and
# recalled again. Measured on the live store: the two other recall slots were
# the same question asked earlier (cosine distance 0.000 / 0.012).
# ---------------------------------------------------------------------------

STALE_LONDON = (
    "The weather in London today, August 4, 2026, is currently 21°C, with a high "
    "of 24°C and a low of 18°C. Expect mostly sunny conditions."
)


@pytest.mark.parametrize("text", [
    STALE_LONDON,
    "It's 77°F in Tokyo and clear.",
    "The forecast for Dallas is rain tomorrow.",
    "AAPL is trading at $231, and the NASDAQ is up 1.2%.",
    "Here are today's headlines from NBC News.",
    "Today's date is Thursday, 1st October 2026.",
    "The current time is 14:32 UTC.",
])
def test_time_sensitive_answers_are_detected(text):
    from victoria.core.semantic_memory import is_time_sensitive
    assert is_time_sensitive(text)


@pytest.mark.parametrize("text", [
    "Paris is the capital of France.",
    "You told me you prefer Python over Go.",
    "Your Obsidian vault has notes under Brain, Docker and Personal.",
    "",
])
def test_ordinary_answers_are_not_time_sensitive(text):
    from victoria.core.semantic_memory import is_time_sensitive
    assert not is_time_sensitive(text)


def _mock_memory(tmp_path, count_return=10):
    mock_chromadb, _client, mock_collection = _make_chromadb_mock(count_return=count_return)
    with patch.dict(sys.modules, {"chromadb": mock_chromadb}):
        mod = _load_fresh_semantic_memory()
        mem = mod.SemanticMemory(db_path=str(tmp_path / "chroma"))
    return mem, mock_collection


def test_add_skips_time_sensitive_answers_but_keeps_the_question(tmp_path):
    """The live answer is not stored (it would go stale); the question is."""
    mem, coll = _mock_memory(tmp_path)

    mem.add("s1", "user", "What's the weather in London today?")
    mem.add("s1", "assistant", STALE_LONDON)

    assert coll.add.call_count == 1
    assert coll.add.call_args.kwargs["documents"] == ["What's the weather in London today?"]


def test_add_timestamps_every_entry(tmp_path):
    mem, coll = _mock_memory(tmp_path)
    before = __import__("time").time()

    mem.add("s1", "assistant", "Paris is the capital of France.")

    meta = coll.add.call_args.kwargs["metadatas"][0]
    assert meta["role"] == "assistant" and meta["session_id"] == "s1"
    assert meta["ts"] >= before


def test_search_drops_stale_answers_and_echoes_but_fills_n(tmp_path):
    """Entries stored before this fix are filtered at READ time — no migration —
    and over-fetching means the filtered slots are refilled with useful hits."""
    mem, coll = _mock_memory(tmp_path, count_return=50)
    coll.query.return_value = {
        "documents": [[
            "What's the weather in London today?",   # echo of the query
            STALE_LONDON,                            # legacy live answer
            "Mark lives near Dallas and travels to London often.",
            "You asked me to track London on the dashboard.",
            "London is five hours ahead of Dallas.",
        ]],
        "metadatas": [[
            {"role": "user", "session_id": "a"},
            {"role": "assistant", "session_id": "b"},
            {"role": "user", "session_id": "c", "ts": 1785000000.0},
            {"role": "assistant", "session_id": "d", "ts": 1785100000.0},
            {"role": "assistant", "session_id": "e"},
        ]],
        "distances": [[0.0, 0.26, 0.40, 0.45, 0.50]],
    }

    results = mem.search("What's the weather in London today?", n=3)

    contents = [r["content"] for r in results]
    assert STALE_LONDON not in contents
    assert "What's the weather in London today?" not in contents
    assert len(results) == 3
    assert results[0]["ts"] == 1785000000.0
    assert results[2]["ts"] is None          # legacy entry, still usable
    assert coll.query.call_args.kwargs["n_results"] == 12   # over-fetched n*4


def test_search_tolerates_a_result_without_distances(tmp_path):
    mem, coll = _mock_memory(tmp_path)
    coll.query.return_value = {
        "documents": [["Paris is the capital of France."]],
        "metadatas": [[{"role": "assistant", "session_id": "x"}]],
    }
    assert mem.search("French capital", n=3)[0]["content"].startswith("Paris")


def test_stale_weather_answer_is_not_recalled_real_chromadb(tmp_path):
    """End to end on a real ChromaDB: replay the live store's London case."""
    pytest.importorskip("chromadb")
    from victoria.core.semantic_memory import SemanticMemory

    mem = SemanticMemory(db_path=str(tmp_path / "chroma"))
    if not mem.available:
        pytest.skip("SemanticMemory initialised but not available (no embedder)")

    # Written the way the old code wrote it: no timestamp, no filter.
    mem._collection.add(
        ids=["legacy-q", "legacy-a", "useful"],
        documents=[
            "What is the weather in London today?",
            STALE_LONDON,
            "Mark is flying to London next week for a customer meeting.",
        ],
        metadatas=[
            {"session_id": "old", "role": "user"},
            {"session_id": "old", "role": "assistant"},
            {"session_id": "older", "role": "user"},
        ],
    )

    results = mem.search("What's the weather in London today?", n=3, exclude_session="new")

    contents = [r["content"] for r in results]
    assert STALE_LONDON not in contents
    assert "What is the weather in London today?" not in contents
