import pytest
from victoria.core.user_profile import UserProfile, ProfileStore


# ---------------------------------------------------------------------------
# UserProfile dataclass tests
# ---------------------------------------------------------------------------

def test_profile_default_values():
    profile = UserProfile("mark")
    assert profile.user_id == "mark"
    assert profile.name == ""
    assert profile.communication_style == ""
    assert profile.preferences == []
    assert profile.topics_of_interest == []
    assert profile.explicit_memories == []


def test_to_system_context_empty():
    assert UserProfile("mark").to_system_context() == ""


def test_to_system_context_partial():
    profile = UserProfile("mark", name="Mark")
    result = profile.to_system_context()
    assert "Mark" in result


def test_to_system_context_full():
    profile = UserProfile(
        user_id="mark",
        name="Mark",
        communication_style="direct and brief",
        preferences=["bullet points", "no filler"],
        topics_of_interest=["Python", "AI"],
        explicit_memories=["based in Alabama"],
    )
    result = profile.to_system_context()
    assert "Mark" in result
    assert "direct and brief" in result
    assert "bullet points" in result
    assert "no filler" in result
    assert "Python" in result
    assert "AI" in result
    assert "based in Alabama" in result


def test_is_empty_true():
    assert UserProfile("mark").is_empty() is True


def test_is_empty_false():
    profile = UserProfile("mark")
    profile.explicit_memories.append("likes coffee")
    assert profile.is_empty() is False


# ---------------------------------------------------------------------------
# ProfileStore tests
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    db_file = str(tmp_path / "test_victoria.db")
    return ProfileStore(db_path=db_file)


def test_store_get_creates_default(store):
    profile = store.get("unknown")
    assert isinstance(profile, UserProfile)
    assert profile.user_id == "unknown"
    assert profile.name == ""
    assert profile.communication_style == ""
    assert profile.preferences == []
    assert profile.topics_of_interest == []
    assert profile.explicit_memories == []


def test_store_save_and_get_roundtrip(store):
    original = UserProfile(
        user_id="u1",
        name="Alice",
        communication_style="concise",
        preferences=["no fluff"],
        topics_of_interest=["ML", "Rust"],
        explicit_memories=["lives in NYC"],
    )
    store.save(original)
    retrieved = store.get("u1")

    assert retrieved.user_id == "u1"
    assert retrieved.name == "Alice"
    assert retrieved.communication_style == "concise"
    assert retrieved.preferences == ["no fluff"]
    assert retrieved.topics_of_interest == ["ML", "Rust"]
    assert retrieved.explicit_memories == ["lives in NYC"]
    assert retrieved.updated_at != ""


def test_store_add_memory(store):
    store.add_memory("u1", "likes coffee")
    assert store.get("u1").explicit_memories == ["likes coffee"]


def test_store_add_memory_dedup(store):
    store.add_memory("u1", "likes coffee")
    store.add_memory("u1", "likes coffee")
    assert store.get("u1").explicit_memories == ["likes coffee"]


def test_store_forget_memory_found(store):
    store.add_memory("u1", "likes coffee")
    result = store.forget_memory("u1", "likes coffee")
    assert result is True
    assert store.get("u1").explicit_memories == []


def test_store_forget_memory_not_found(store):
    result = store.forget_memory("u1", "nonexistent memory")
    assert result is False


def test_store_update_style(store):
    # Initial update
    store.update_style(
        "u1",
        style="technical",
        new_preferences=["bullet points"],
        new_topics=["Python"],
    )
    profile = store.get("u1")
    assert profile.communication_style == "technical"
    assert "bullet points" in profile.preferences
    assert "Python" in profile.topics_of_interest

    # Merge without duplicates
    store.update_style(
        "u1",
        style="technical and direct",
        new_preferences=["bullet points", "no filler"],
        new_topics=["Python", "AI"],
    )
    profile = store.get("u1")
    assert profile.communication_style == "technical and direct"
    assert profile.preferences.count("bullet points") == 1
    assert "no filler" in profile.preferences
    assert profile.topics_of_interest.count("Python") == 1
    assert "AI" in profile.topics_of_interest
