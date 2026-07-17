"""Tests for the Obsidian knowledge bases (victoria/knowledge/vaults.py)."""
from pathlib import Path

import pytest

from victoria.knowledge.vaults import KnowledgeBase, Vault


@pytest.fixture
def kb(tmp_path: Path) -> KnowledgeBase:
    """Two vaults: 'ai' (writable) and 'docker' (read-only), each with notes
    plus an .obsidian config dir that must stay invisible."""
    ai = tmp_path / "ai"
    docker = tmp_path / "docker"
    (ai / "Projects").mkdir(parents=True)
    (ai / ".obsidian").mkdir(parents=True)
    docker.mkdir()

    (ai / "welcome.md").write_text("# Welcome\nVictoria's own brain lives here.\n")
    (ai / "Projects" / "victoria.md").write_text(
        "# Victoria\nThe dashboard polls Yahoo Finance for stock prices.\n"
    )
    (ai / ".obsidian" / "app.json").write_text('{"theme":"obsidian"}')
    (docker / "compose.md").write_text("# Staging\nUse docker compose up for staging.\n")

    return KnowledgeBase(
        vaults={
            "ai": Vault(name="ai", root=ai, writable=True),
            "docker": Vault(name="docker", root=docker, writable=False),
            "personal": Vault(name="personal", root=tmp_path / "missing", writable=True),
        },
        max_note_chars=1000,
    )


# -- registry ---------------------------------------------------------------
def test_only_existing_vaults_are_enabled(kb: KnowledgeBase):
    # 'personal' points at a missing folder → not enabled.
    assert set(kb.names()) == {"ai", "docker"}


def test_note_count_excludes_obsidian_config(kb: KnowledgeBase):
    assert kb.note_count(kb.get("ai")) == 2  # welcome + Projects/victoria, NOT .obsidian


# -- listing ----------------------------------------------------------------
def test_list_notes_sorted_and_relative(kb: KnowledgeBase):
    assert kb.list_notes("ai") == ["Projects/victoria.md", "welcome.md"]


def test_list_notes_folder_filter(kb: KnowledgeBase):
    assert kb.list_notes("ai", folder="Projects") == ["Projects/victoria.md"]


def test_list_notes_missing_vault_is_empty(kb: KnowledgeBase):
    assert kb.list_notes("personal") == []
    assert kb.list_notes("nope") == []


# -- reading ----------------------------------------------------------------
def test_read_note_adds_md_extension(kb: KnowledgeBase):
    assert "Victoria's own brain" in kb.read_note("ai", "welcome")


def test_read_missing_note_returns_none(kb: KnowledgeBase):
    assert kb.read_note("ai", "does-not-exist") is None


def test_read_truncates_to_cap(tmp_path: Path):
    root = tmp_path / "ai"
    root.mkdir()
    (root / "big.md").write_text("x" * 5000)
    kb = KnowledgeBase(vaults={"ai": Vault("ai", root, True)}, max_note_chars=100)
    out = kb.read_note("ai", "big.md")
    assert out.endswith("…[truncated]…") and len(out) < 500


# -- search -----------------------------------------------------------------
def test_search_all_vaults_and_terms_are_anded(kb: KnowledgeBase):
    hits = kb.search("stock prices")
    assert len(hits) == 1
    assert hits[0]["vault"] == "ai"
    assert hits[0]["path"] == "Projects/victoria.md"
    assert "Yahoo" in hits[0]["snippet"]


def test_search_scoped_to_one_vault(kb: KnowledgeBase):
    assert kb.search("staging", vault_name="docker")
    assert kb.search("staging", vault_name="ai") == []


def test_search_matches_title(kb: KnowledgeBase):
    # "victoria" only appears in the filename stem of one note body-less match
    hits = kb.search("welcome", vault_name="ai")
    assert any(h["title"] == "welcome" for h in hits)


# -- writing ----------------------------------------------------------------
def test_write_creates_note_with_subdirs(kb: KnowledgeBase):
    ok, msg = kb.write_note("ai", "Ideas/new-feature", "# Idea\nRAG over vaults.")
    assert ok and "ai" in msg
    assert kb.read_note("ai", "Ideas/new-feature.md").startswith("# Idea")


def test_write_append(kb: KnowledgeBase):
    kb.write_note("ai", "log", "first")
    ok, _ = kb.write_note("ai", "log", "second", append=True)
    body = kb.read_note("ai", "log")
    assert ok and "first" in body and "second" in body


def test_write_refused_on_readonly_vault(kb: KnowledgeBase):
    ok, msg = kb.write_note("docker", "x", "y")
    assert not ok and "read-only" in msg
    assert not (kb.get("docker").root / "x.md").exists()


def test_write_unknown_vault(kb: KnowledgeBase):
    ok, msg = kb.write_note("nope", "x", "y")
    assert not ok and "don't have a vault" in msg


# -- path traversal (security) ---------------------------------------------
@pytest.mark.parametrize("evil", ["../escape", "../../etc/passwd", ".obsidian/app", "sub/../../out"])
def test_write_rejects_path_traversal(kb: KnowledgeBase, evil: str):
    ok, msg = kb.write_note("ai", evil, "pwned")
    assert not ok


def test_read_rejects_path_traversal(kb: KnowledgeBase):
    assert kb.read_note("ai", "../../etc/passwd") is None
