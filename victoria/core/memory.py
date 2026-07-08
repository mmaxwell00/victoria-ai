import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class MemoryStore:
    def __init__(self, db_path: str = "data/victoria.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # WAL + busy_timeout: several connections share this file (ProfileStore,
        # background profile updates) — avoids "database is locked" errors.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                channel     TEXT NOT NULL DEFAULT 'api',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL REFERENCES sessions(id),
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                llm_used    TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        """)
        # Migrate older DBs that predate the per-session title (shown in the
        # HUD's Topics / chat-history list).
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(sessions)")}
        if "title" not in existing:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT DEFAULT ''")
        self.conn.commit()

    @staticmethod
    def _derive_title(text: str, limit: int = 48) -> str:
        """A short chat title from the first user message."""
        title = " ".join((text or "").split())
        if len(title) > limit:
            title = title[:limit].rstrip() + "…"
        return title

    def get_or_create_session(
        self, session_id: str, user_id: str = "default", channel: str = "api"
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()

        if row:
            return {"id": row[0], "user_id": row[1], "channel": row[2]}

        self.conn.execute(
            "INSERT INTO sessions (id, user_id, channel, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, channel, now, now),
        )
        self.conn.commit()
        return {"id": session_id, "user_id": user_id, "channel": channel}

    def get_session(self, session_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT id, user_id, channel FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "user_id": row[1], "channel": row[2]}

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        llm_used: Optional[str] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at, llm_used) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, now, llm_used),
        )
        self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )
        # Title the session from its first user message (only once — the guard
        # on empty title means later messages don't overwrite it).
        if role == "user":
            self.conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ? AND (title IS NULL OR title = '')",
                (self._derive_title(content), session_id),
            )
        self.conn.commit()

    def get_history(self, session_id: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """SELECT role, content FROM messages
               WHERE session_id = ?
               ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        history = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
        # The window must start on a user message — the Anthropic API rejects
        # conversations whose first message is from the assistant.
        while history and history[0]["role"] != "user":
            history.pop(0)
        return history

    def list_sessions(self, user_id: str) -> list[dict]:
        """Sessions for a user, newest first, each with its title and message
        count. Sessions with no messages yet are omitted (nothing to show)."""
        rows = self.conn.execute(
            """SELECT s.id, s.channel, s.created_at, s.updated_at, s.title,
                      COUNT(m.id) AS msg_count
               FROM sessions s
               JOIN messages m ON m.session_id = s.id
               WHERE s.user_id = ?
               GROUP BY s.id
               ORDER BY s.updated_at DESC""",
            (user_id,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "channel": r[1],
                "created_at": r[2],
                "updated_at": r[3],
                "title": r[4] or self._derive_title_fallback(r[0]),
                "message_count": r[5],
            }
            for r in rows
        ]

    def _derive_title_fallback(self, session_id: str) -> str:
        """Title for legacy sessions saved before titles existed: derive from
        the first user message, or fall back to the short id."""
        row = self.conn.execute(
            "SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row and row[0]:
            return self._derive_title(row[0])
        return session_id[:8].upper()
