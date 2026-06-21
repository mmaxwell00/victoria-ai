import logging
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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
        """Store a message. Silently skips if content is empty or ChromaDB unavailable."""
        if not self._available or not content.strip():
            return
        try:
            self._collection.add(
                ids=[doc_id or str(uuid.uuid4())],
                documents=[content],
                metadatas=[{"session_id": session_id, "role": role}],
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

        Each result: {"content": str, "role": str, "session_id": str}
        Returns [] if unavailable or nothing found.
        """
        if not self._available or not query.strip():
            return []
        try:
            total = self._collection.count()
            if total == 0:
                return []

            kwargs: dict = {
                "query_texts": [query],
                "n_results": min(n, total),
            }
            if exclude_session:
                kwargs["where"] = {"session_id": {"$ne": exclude_session}}

            results = self._collection.query(**kwargs)
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]

            return [
                {
                    "content": doc,
                    "role": meta.get("role", "unknown"),
                    "session_id": meta.get("session_id", ""),
                }
                for doc, meta in zip(docs, metas)
            ]
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
