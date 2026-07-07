"""Tests for on-demand GitHub skill import + review-before-add flow."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from victoria.skills import importer
from victoria.skills.importer import extract_url, repo_slug, _discover, _raw_url, _is_single_file
from victoria.core.conversation import ConversationManager


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("import from https://github.com/a/b please", "https://github.com/a/b"),
    ("grab https://github.com/a/b.git.", "https://github.com/a/b.git"),
    ("no url here", None),
])
def test_extract_url(text, expected):
    assert extract_url(text) == expected


def test_repo_slug():
    assert repo_slug("https://github.com/Foo/Bar-Skills") == "foo-bar-skills"
    assert repo_slug("https://github.com/o/r.git") == "o-r"


def test_single_file_detection_and_raw():
    assert _is_single_file("https://github.com/o/r/blob/main/s.md") is True
    assert _is_single_file("https://github.com/o/r") is False
    assert _raw_url("https://github.com/o/r/blob/main/skills/s.md") == \
        "https://raw.githubusercontent.com/o/r/main/skills/s.md"


# ---------------------------------------------------------------------------
# Discovery from a cloned tree
# ---------------------------------------------------------------------------

def test_discover_finds_skill_formats(tmp_path):
    # Anthropic-style folder skill
    (tmp_path / "email").mkdir()
    (tmp_path / "email" / "SKILL.md").write_text(
        "---\nname: email\ndescription: draft email\n---\nWrite an email.", encoding="utf-8")
    # skills/ dir flat file
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "standup.md").write_text(
        "---\nname: standup\ndescription: standup\n---\nDo standup.", encoding="utf-8")
    # top-level file with frontmatter
    (tmp_path / "note.md").write_text(
        "---\nname: note\ndescription: notes\n---\nTake notes.", encoding="utf-8")
    # a README that must be IGNORED (no frontmatter, not a skill)
    (tmp_path / "README.md").write_text("# Just a readme\nnothing to see", encoding="utf-8")

    found = _discover(tmp_path)
    names = {s["name"] for s in found}
    assert names == {"email", "standup", "note"}
    assert "readme" not in {n.lower() for n in names}


def test_discover_dedupes_by_name(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: dup\ndescription: x\n---\nfirst", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("---\nname: dup\ndescription: y\n---\nsecond", encoding="utf-8")
    found = _discover(tmp_path)
    assert len([s for s in found if s["name"] == "dup"]) == 1


async def test_fetch_rejects_non_https():
    with pytest.raises(importer.SkillImportError, match="https"):
        await importer.fetch_skills("git@github.com:o/r.git")


# ---------------------------------------------------------------------------
# Review-before-add flow in ConversationManager
# ---------------------------------------------------------------------------

def _make_mgr():
    mem = MagicMock()
    mem.get_or_create_session.return_value = None
    mem.get_history.return_value = []
    return ConversationManager(memory=mem, router=MagicMock())


async def test_import_stages_for_review_without_saving(monkeypatch):
    import victoria.tools.skills_tools as st
    from victoria.skills import importer as imp
    st.clear_imports()
    monkeypatch.setattr(imp, "fetch_skills", AsyncMock(return_value=[
        {"name": "alpha", "description": "A", "instructions": "do A", "source": "alpha.md"},
        {"name": "beta", "description": "B", "instructions": "do B", "source": "beta.md"},
    ]))
    save_spy = MagicMock()
    from victoria.skills import store as store_mod
    monkeypatch.setattr(store_mod.skill_store, "save", save_spy)

    mgr = _make_mgr()
    result = await mgr.chat("import skills from https://github.com/o/r", session_id="s1")

    assert result["backend"] == "victoria"
    assert "found 2 skill" in result["response"].lower()
    assert st.has_pending_imports() is True
    save_spy.assert_not_called()   # NOTHING saved during review


async def test_add_all_saves_pending_imports(monkeypatch):
    import victoria.tools.skills_tools as st
    from victoria.skills import store as store_mod
    st.clear_imports()
    st.stage_imports("https://github.com/o/r", [
        {"name": "alpha", "description": "A", "instructions": "do A", "source": "alpha.md"},
        {"name": "beta", "description": "B", "instructions": "do B", "source": "beta.md"},
    ])
    saved = []
    monkeypatch.setattr(store_mod.skill_store, "exists", lambda n: False)
    monkeypatch.setattr(store_mod.skill_store, "save",
                        lambda name, description, instructions, subdir=None: saved.append((name, subdir)))

    mgr = _make_mgr()
    result = await mgr.chat("add all", session_id="s1")

    assert {n for n, _ in saved} == {"alpha", "beta"}
    assert all(sub == "imported/o-r" for _, sub in saved)   # namespaced
    assert st.has_pending_imports() is False
    assert "added" in result["response"].lower()


async def test_add_specific_and_skip_collision(monkeypatch):
    import victoria.tools.skills_tools as st
    from victoria.skills import store as store_mod
    st.clear_imports()
    st.stage_imports("https://github.com/o/r", [
        {"name": "alpha", "description": "A", "instructions": "do A", "source": "a.md"},
        {"name": "beta", "description": "B", "instructions": "do B", "source": "b.md"},
    ])
    saved = []
    monkeypatch.setattr(store_mod.skill_store, "exists", lambda n: n == "beta")  # beta already exists
    monkeypatch.setattr(store_mod.skill_store, "save",
                        lambda name, description, instructions, subdir=None: saved.append(name))

    mgr = _make_mgr()
    # ask for both; beta should be skipped as a collision
    result = await mgr.chat("add alpha and beta", session_id="s1")
    assert saved == ["alpha"]
    assert "skipped" in result["response"].lower()
    assert st.has_pending_imports() is False


async def test_show_keeps_pending(monkeypatch):
    import victoria.tools.skills_tools as st
    st.clear_imports()
    st.stage_imports("https://github.com/o/r", [
        {"name": "alpha", "description": "A", "instructions": "STEP ONE DETAIL", "source": "a.md"},
    ])
    mgr = _make_mgr()
    result = await mgr.chat("show alpha", session_id="s1")
    assert "STEP ONE DETAIL" in result["response"]
    assert st.has_pending_imports() is True   # still awaiting decision
    st.clear_imports()


async def test_no_cancels_import(monkeypatch):
    import victoria.tools.skills_tools as st
    from victoria.skills import store as store_mod
    st.clear_imports()
    st.stage_imports("https://github.com/o/r", [
        {"name": "alpha", "description": "A", "instructions": "x", "source": "a.md"},
    ])
    save_spy = MagicMock()
    monkeypatch.setattr(store_mod.skill_store, "save", save_spy)
    mgr = _make_mgr()
    result = await mgr.chat("no", session_id="s1")
    assert st.has_pending_imports() is False
    save_spy.assert_not_called()
    assert "discarded" in result["response"].lower()


async def test_unrelated_message_drops_pending(monkeypatch):
    import victoria.tools.skills_tools as st
    st.clear_imports()
    st.stage_imports("https://github.com/o/r", [
        {"name": "alpha", "description": "A", "instructions": "x", "source": "a.md"},
    ])
    router = MagicMock()
    router.chat = AsyncMock(return_value=("Hello!", "docker"))
    mem = MagicMock(); mem.get_or_create_session.return_value = None; mem.get_history.return_value = []
    mgr = ConversationManager(memory=mem, router=router)

    result = await mgr.chat("what's the weather?", session_id="s1")
    # pending dropped, handled as a normal turn
    assert st.has_pending_imports() is False
    assert result["response"] == "Hello!"
