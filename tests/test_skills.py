"""Tests for Skills: the store, the tools, and the draft-then-confirm flow."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from victoria.skills.store import Skill, SkillStore, slugify
from victoria.core.conversation import ConversationManager


# ---------------------------------------------------------------------------
# SkillStore
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return SkillStore(skills_dir=str(tmp_path))


def test_save_and_get_roundtrip(store):
    store.save("Standup Update", "Format a daily standup.", "1. Yesterday\n2. Today\n3. Blockers")
    got = store.get("standup-update")
    assert got is not None
    assert got.name == "Standup Update"
    assert got.description == "Format a daily standup."
    assert "Blockers" in got.instructions


def test_get_is_slug_insensitive(store):
    store.save("Email Drafter", "d", "do the thing")
    assert store.get("Email Drafter") is not None
    assert store.get("email-drafter") is not None
    assert store.get("EMAIL DRAFTER") is not None


def test_list_and_index(store):
    store.save("alpha", "First skill.", "x")
    store.save("beta", "Second skill.", "y")
    names = store.names()
    assert "alpha" in names and "beta" in names
    index = store.index()
    assert "alpha: First skill." in index
    assert "beta: Second skill." in index


def test_index_empty(store):
    assert store.index() == ""


def test_save_overwrites_same_name(store):
    store.save("note", "v1", "first")
    store.save("note", "v2", "second")
    assert len([s for s in store.list() if s.name == "note"]) == 1
    assert store.get("note").description == "v2"


def test_delete(store):
    store.save("temp", "d", "x")
    assert store.delete("temp") is True
    assert store.get("temp") is None
    assert store.delete("temp") is False


def test_parse_handles_missing_frontmatter(store, tmp_path):
    (tmp_path / "raw.md").write_text("just instructions, no frontmatter", encoding="utf-8")
    skill = store.get("raw")
    assert skill is not None
    assert skill.instructions == "just instructions, no frontmatter"


@pytest.mark.parametrize("name,slug", [
    ("Email Drafter", "email-drafter"),
    ("Stand-up  Update!", "stand-up-update"),
    ("   ", "skill"),
])
def test_slugify(name, slug):
    assert slugify(name) == slug


# ---------------------------------------------------------------------------
# Skills tools
# ---------------------------------------------------------------------------

def test_use_skill_tool_returns_instructions(monkeypatch):
    import victoria.tools.skills_tools as st
    fake = Skill(name="email-drafter", description="d", instructions="Follow these steps.")
    monkeypatch.setattr(st.skill_store, "get", lambda n: fake)
    out = st.use_skill("email-drafter")
    assert "Follow these steps." in out


def test_use_skill_tool_unknown(monkeypatch):
    import victoria.tools.skills_tools as st
    monkeypatch.setattr(st.skill_store, "get", lambda n: None)
    monkeypatch.setattr(st.skill_store, "names", lambda: ["a", "b"])
    out = st.use_skill("nope")
    assert "No skill named 'nope'" in out


def test_save_skill_tool_stages_not_saves():
    import victoria.tools.skills_tools as st
    st.pop_staged_skill()  # clear
    msg = st.save_skill("x", "d", "i")
    assert st.has_staged_skill() is True
    assert "not saved yet" in msg.lower()
    draft = st.pop_staged_skill()
    assert draft == {"name": "x", "description": "d", "instructions": "i"}
    assert st.has_staged_skill() is False


# ---------------------------------------------------------------------------
# Draft-then-confirm flow in ConversationManager
# ---------------------------------------------------------------------------

def _make_mgr():
    mem = MagicMock()
    mem.get_or_create_session.return_value = None
    mem.get_history.return_value = []
    router = MagicMock()
    router.chat = AsyncMock(return_value=("ok", "docker"))
    return ConversationManager(memory=mem, router=router)


async def test_confirming_staged_skill_saves_it(monkeypatch):
    import victoria.tools.skills_tools as st
    from victoria.skills import store as store_mod
    saved = {}
    monkeypatch.setattr(store_mod.skill_store, "save",
                        lambda **kw: saved.update(kw) or Skill(**kw))
    st.pop_staged_skill()
    st.stage_skill("standup", "daily standup", "steps here")

    mgr = _make_mgr()
    result = await mgr.chat("yes", session_id="s1")

    assert result["backend"] == "victoria"
    assert "standup" in result["response"]
    assert saved == {"name": "standup", "description": "daily standup", "instructions": "steps here"}
    assert st.has_staged_skill() is False
    mgr.router.chat.assert_not_called()   # model not invoked on the confirming turn


async def test_declining_staged_skill_discards_it(monkeypatch):
    import victoria.tools.skills_tools as st
    from victoria.skills import store as store_mod
    save_spy = MagicMock()
    monkeypatch.setattr(store_mod.skill_store, "save", save_spy)
    st.pop_staged_skill()
    st.stage_skill("temp", "d", "i")

    mgr = _make_mgr()
    result = await mgr.chat("no", session_id="s1")

    assert result["backend"] == "victoria"
    assert st.has_staged_skill() is False
    save_spy.assert_not_called()


async def test_ambiguous_reply_drops_draft_and_answers(monkeypatch):
    import victoria.tools.skills_tools as st
    from victoria.skills import store as store_mod
    save_spy = MagicMock()
    monkeypatch.setattr(store_mod.skill_store, "save", save_spy)
    st.pop_staged_skill()
    st.stage_skill("temp", "d", "i")

    mgr = _make_mgr()
    result = await mgr.chat("actually what's the weather?", session_id="s1")

    # draft dropped, treated as a normal question (local model answered)
    assert st.has_staged_skill() is False
    save_spy.assert_not_called()
    mgr.router.chat.assert_awaited()


def test_parse_skill_block():
    from victoria.core.conversation import _parse_skill_block
    text = ("Here's a draft:\n```skill\nname: standup\ndescription: daily standup\n"
            "instructions:\n  1. yesterday\n  2. today\n```\nShall I save?")
    d = _parse_skill_block(text)
    assert d["name"] == "standup"
    assert d["description"] == "daily standup"
    assert "1. yesterday" in d["instructions"] and "2. today" in d["instructions"]
    assert _parse_skill_block("no fenced block here") is None


def test_relevant_skills_matches_by_name(monkeypatch):
    import victoria.skills.store as sm
    monkeypatch.setattr(sm.skill_store, "list",
                        lambda: [Skill("email-drafter", "draft professional emails", "...")])
    rel = ConversationManager._relevant_skills("use the email-drafter skill please")
    assert any(s.name == "email-drafter" for s in rel)
    assert ConversationManager._relevant_skills("what time is it") == []


async def test_skill_request_stages_draft_from_block(monkeypatch):
    import victoria.tools.skills_tools as st
    st.pop_staged_skill()
    block = ("Sure!\n```skill\nname: standup\ndescription: daily standup\n"
             "instructions:\n  1. yesterday\n  2. today\n```\nlook ok?")
    mgr = _make_mgr()
    mgr.router.chat = AsyncMock(return_value=(block, "docker"))

    result = await mgr.chat("create a skill named standup", session_id="s1")

    assert st.has_staged_skill() is True
    assert "save it?" in result["response"].lower()
    d = st.peek_staged_skill()
    assert d["name"] == "standup"
    st.pop_staged_skill()


def test_skills_prompt_lists_saved_skills(monkeypatch):
    monkeypatch.setattr(ConversationManager, "_skills_prompt", staticmethod(
        ConversationManager._skills_prompt))  # keep real
    import victoria.skills.store as store_mod
    monkeypatch.setattr(store_mod.skill_store, "index", lambda: "- foo: does foo")
    prompt = ConversationManager._skills_prompt()
    assert "SKILLS" in prompt
    assert "foo: does foo" in prompt
    assert "```skill" in prompt   # creation uses a structured block
