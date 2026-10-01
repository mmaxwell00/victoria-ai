import logging
import re
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# An ANSWER carrying live data — weather, market prices, headlines, the date/time.
# It was true when it was said and is wrong a day later. Recalled into a new turn,
# it reads to the local model as a fact it already has, so the model skips the
# tool and repeats it: "What's the weather in London today?" was answered for
# weeks with an August 4 reading, and each repeat was stored and recalled again.
# Such answers are not stored, and pre-existing ones are never recalled. Only
# assistant content is checked; the user's questions hold no stale facts.
_TIME_SENSITIVE_RE = re.compile(
    r"\b(weather|forecast|temperature|humidity|precipitation|feels like)\b"
    r"|°\s?[CF]\b|\b\d+\s?degrees\b"
    r"|\b(stock price|share price|trading at|ticker|nasdaq|s&p\s?500|dow jones|market cap)\b"
    r"|\b(headlines?|breaking news|latest news|news today)\b"
    r"|\b(today'?s date|the date is|the (current )?time is)\b",
    re.IGNORECASE,
)

# Cosine distance under which a recalled USER message is just this same question
# asked before ("What is…" vs "What's…" measured 0.012). It adds nothing and
# would take a recall slot from something useful.
_ECHO_DISTANCE = 0.05


def is_time_sensitive(text: str) -> bool:
    """True when *text* reads like a live-data answer that goes stale."""
    return bool(text) and bool(_TIME_SENSITIVE_RE.search(text))


class SemanticMemory:
    """ChromaDB-backed semantic memory for cross-session context recall.

    Stores every conversation turn as an embedding. Given a new user message,
    retrieves the N most semantically similar past messages to inject as context.

    Usage:
        mem = SemanticMemory(db_path="data/chromadb")
        mem.add(session_id="abc", role="user", content="What's the weather?")
        results = mem.search("weather in London", n=3)
    """

    def __init__(self, db_path: str = "data/chromadb"):
        Path(db_path).mkdir(parents=True, exist_ok=True)
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=db_path)
            self._collection = self._client.get_or_create_collection(
                name="conversations",
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
            logger.info("Semantic memory initialised at %s (%d entries)", db_path, self._collection.count())
        except Exception as exc:
            logger.warning("ChromaDB unavailable (%s) — semantic memory disabled", exc)
            self._available = False
            self._collection = None

    # ------------------------------------------------------------------ #
    # Write                                                                #
    # ------------------------------------------------------------------ #

    def add(
        self,
        session_id: str,
        role: str,
        content: str,
        doc_id: Optional[str] = None,
    ) -> None:
        """Store a message, timestamped so recall can say how old it is.

        Silently skips if content is empty, ChromaDB is unavailable, or it is an
        assistant answer carrying live data (see `_TIME_SENSITIVE_RE`).
        """
        if not self._available or not content.strip():
            return
        if role == "assistant" and is_time_sensitive(content):
            logger.debug("Not storing a time-sensitive answer in semantic memory")
            return
        try:
            self._collection.add(
                ids=[doc_id or str(uuid.uuid4())],
                documents=[content],
                metadatas=[{"session_id": session_id, "role": role, "ts": time.time()}],
            )
        except Exception as exc:
            logger.warning("semantic_memory.add failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Read                                                                 #
    # ------------------------------------------------------------------ #

    def search(
        self,
        query: str,
        n: int = 3,
        exclude_session: Optional[str] = None,
    ) -> list[dict]:
        """Return up to n semantically similar past messages.

        Each result: {"content": str, "role": str, "session_id": str,
        "ts": float | None} — `ts` is None for entries stored before timestamps.
        Live-data answers and echoes of this same question are filtered out, so
        the store is over-fetched to still fill n. Returns [] if unavailable or
        nothing found.
        """
        if not self._available or not query.strip():
            return []
        try:
            total = self._collection.count()
            if total == 0:
                return []

            kwargs: dict = {
                "query_texts": [query],
                "n_results": min(n * 4, total),
                "include": ["documents", "metadatas", "distances"],
            }
            if exclude_session:
                kwargs["where"] = {"session_id": {"$ne": exclude_session}}

            results = self._collection.query(**kwargs)
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = (results.get("distances") or [[]])[0] or [None] * len(docs)

            out: list[dict] = []
            for doc, meta, dist in zip(docs, metas, dists):
                role = meta.get("role", "unknown")
                if role == "assistant" and is_time_sensitive(doc):
                    continue
                if role == "user" and dist is not None and dist < _ECHO_DISTANCE:
                    continue
                out.append({
                    "content": doc,
                    "role": role,
                    "session_id": meta.get("session_id", ""),
                    "ts": meta.get("ts"),
                })
                if len(out) == n:
                    break
            return out
        except Exception as exc:
            logger.warning("semantic_memory.search failed: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    # Utility                                                              #
    # ------------------------------------------------------------------ #

    def count(self) -> int:
        if not self._available:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0

    @property
    def available(self) -> bool:
        return self._available
