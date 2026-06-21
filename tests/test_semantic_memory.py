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
