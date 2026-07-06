import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class UserProfile:
    """Persistent per-user profile. Victoria learns and remembers across sessions."""
    user_id: str
    name: str = ""
    communication_style: str = ""            # e.g. "direct, technical, prefers brevity"
    preferences: list[str] = field(default_factory=list)   # ["bullet points", "no filler"]
    topics_of_interest: list[str] = field(default_factory=list)  # ["Python", "AI"]
    explicit_memories: list[str] = field(default_factory=list)   # ["based in [redacted]"]
    updated_at: str = ""

    def to_system_context(self) -> str:
        """Format the profile as a system prompt section. Returns '' if profile is empty."""
        parts = []
        if self.name:
            parts.append(f"The user's name is {self.name}.")
        if self.communication_style:
            parts.append(f"Communication style: {self.communication_style}.")
        if self.preferences:
            prefs = "\n".join(f"- {p}" for p in self.preferences)
            parts.append(f"Response preferences:\n{prefs}")
        if self.topics_of_interest:
            parts.append("Topics they care about: " + ", ".join(self.topics_of_interest) + ".")
        if self.explicit_memories:
            mems = "\n".join(f"- {m}" for m in self.explicit_memories)
            parts.append(f"Things to remember about this user:\n{mems}")
        if not parts:
            return ""
        return "About this user:\n" + "\n".join(parts)

    def is_empty(self) -> bool:
        return not any([
            self.name, self.communication_style,
            self.preferences, self.topics_of_interest, self.explicit_memories
        ])


class ProfileStore:
    """SQLite-backed store for UserProfile. Shares the same DB file as MemoryStore."""

    def __init__(self, db_path: str = "data/victoria.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # WAL + busy_timeout: this file is shared with MemoryStore and written
        # from background tasks — avoids "database is locked" errors.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id             TEXT PRIMARY KEY,
                name                TEXT DEFAULT '',
                communication_style TEXT DEFAULT '',
                preferences         TEXT DEFAULT '[]',
                topics_of_interest  TEXT DEFAULT '[]',
                explicit_memories   TEXT DEFAULT '[]',
                updated_at          TEXT
            )
        """)
        self.conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def get(self, user_id: str) -> UserProfile:
        """Return the profile for user_id, creating a blank one if it doesn't exist."""
        row = self.conn.execute(
            "SELECT name, communication_style, preferences, topics_of_interest, "
            "explicit_memories, updated_at FROM user_profiles WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            return UserProfile(user_id=user_id)
        return UserProfile(
            user_id=user_id,
            name=row[0] or "",
            communication_style=row[1] or "",
            preferences=json.loads(row[2] or "[]"),
            topics_of_interest=json.loads(row[3] or "[]"),
            explicit_memories=json.loads(row[4] or "[]"),
            updated_at=row[5] or "",
        )

    def save(self, profile: UserProfile) -> None:
        profile.updated_at = self._now()
        self.conn.execute("""
            INSERT INTO user_profiles
                (user_id, name, communication_style, preferences, topics_of_interest,
                 explicit_memories, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                communication_style=excluded.communication_style,
                preferences=excluded.preferences,
                topics_of_interest=excluded.topics_of_interest,
                explicit_memories=excluded.explicit_memories,
                updated_at=excluded.updated_at
        """, (
            profile.user_id, profile.name, profile.communication_style,
            json.dumps(profile.preferences), json.dumps(profile.topics_of_interest),
            json.dumps(profile.explicit_memories), profile.updated_at,
        ))
        self.conn.commit()

    def add_memory(self, user_id: str, memory: str) -> None:
        """Append an explicit memory string. Deduplicates on exact match."""
        profile = self.get(user_id)
        if memory not in profile.explicit_memories:
            profile.explicit_memories.append(memory)
            self.save(profile)

    def forget_memory(self, user_id: str, memory: str) -> bool:
        """Remove a memory by exact string. Returns True if it was found and removed."""
        profile = self.get(user_id)
        if memory in profile.explicit_memories:
            profile.explicit_memories.remove(memory)
            self.save(profile)
            return True
        return False

    def update_style(
        self,
        user_id: str,
        style: Optional[str] = None,
        new_preferences: Optional[list[str]] = None,
        new_topics: Optional[list[str]] = None,
    ) -> None:
        """Merge style/preference/topic updates into the profile."""
        profile = self.get(user_id)
        if style:
            profile.communication_style = style
        if new_preferences:
            for p in new_preferences:
                if p not in profile.preferences:
                    profile.preferences.append(p)
        if new_topics:
            for t in new_topics:
                if t not in profile.topics_of_interest:
                    profile.topics_of_interest.append(t)
        self.save(profile)
