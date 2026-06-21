import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class MemoryStore:
    def __init__(self, db_path: str = "data/victoria.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
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
        self.conn.commit()

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
        self.conn.commit()

    def get_history(self, session_id: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """SELECT role, content FROM messages
               WHERE session_id = ?
               ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def list_sessions(self, user_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, channel, created_at, updated_at FROM sessions WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [{"id": r[0], "channel": r[1], "created_at": r[2], "updated_at": r[3]} for r in rows]
