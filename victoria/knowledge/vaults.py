"""Obsidian knowledge bases: folders of Markdown notes Victoria can read,
search, and update.

An Obsidian vault is just a directory of ``.md`` files (plus a ``.obsidian/``
config dir and attachments), so "access" is plain, guarded filesystem I/O. This
module is native file access — the substrate that the RAG index (Phase 1b) will
embed and that Obsidian's Local REST API (Phase 3) will layer live actions on.

This is deliberately DISTINCT from the *Credentials Vault* in ``victoria/vault/``
(Fernet-encrypted secrets). These "vaults" hold knowledge (notes); that one
holds secrets. To avoid the overloaded word, this subsystem is the
"knowledge base(s)".

Safety invariants (enforced in code, not just prompts):
- every read/write path is resolved *inside* its vault root — traversal (``..``),
  absolute escapes, and the reserved ``.obsidian`` / ``.trash`` dirs are rejected;
- writes are refused for vaults not listed as writable;
- a missing/undonfigured vault degrades gracefully (no crash), never guesses.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from victoria.config import settings

logger = logging.getLogger(__name__)

# Directories inside a vault we never read, list, search, or write into.
_SKIP_DIRS = {".obsidian", ".trash", ".git", ".sync", ".DS_Store"}
# File extensions we treat as readable notes.
_NOTE_EXTS = {".md", ".markdown"}


def _norm_name(name: str) -> str:
    return (name or "").strip().lower()


@dataclass
class Vault:
    name: str
    root: Path
    writable: bool

    @property
    def exists(self) -> bool:
        return self.root.is_dir()


class KnowledgeBase:
    """A set of named Obsidian vaults with path-safe read / search / write."""

    def __init__(
        self,
        vaults: Optional[dict[str, Vault]] = None,
        max_note_chars: Optional[int] = None,
    ):
        self.max_note_chars = max_note_chars or getattr(
            settings, "obsidian_max_note_chars", 60000
        )
        self._vaults: dict[str, Vault] = (
            vaults if vaults is not None else _vaults_from_settings()
        )

    # -- registry ------------------------------------------------------
    def vaults(self) -> list[Vault]:
        """Enabled vaults: a path is configured and the folder exists on disk."""
        return [v for v in self._vaults.values() if v.exists]

    def names(self) -> list[str]:
        return [v.name for v in self.vaults()]

    def get(self, name: str) -> Optional[Vault]:
        return self._vaults.get(_norm_name(name))

    # -- path safety ---------------------------------------------------
    def _resolve(self, vault: Vault, rel_path: str) -> Path:
        """Resolve ``rel_path`` inside the vault root, or raise ``ValueError``."""
        rel = (rel_path or "").strip().lstrip("/")
        if not rel:
            raise ValueError("a note path is required")
        root = vault.root.resolve()
        candidate = (root / rel).resolve()
        if candidate == root or not candidate.is_relative_to(root):
            raise ValueError("path escapes the vault")
        if _SKIP_DIRS & set(candidate.relative_to(root).parts):
            raise ValueError("that path is reserved")
        return candidate

    @staticmethod
    def _with_md(path: str) -> str:
        p = (path or "").strip()
        if p and not any(p.lower().endswith(e) for e in _NOTE_EXTS):
            p += ".md"
        return p

    # -- iteration -----------------------------------------------------
    def _iter_notes(self, vault: Vault) -> Iterable[Path]:
        try:
            paths = vault.root.rglob("*")
        except OSError:
            return
        for p in paths:
            if p.suffix.lower() not in _NOTE_EXTS or not p.is_file():
                continue
            if _SKIP_DIRS & set(p.relative_to(vault.root).parts):
                continue
            yield p

    def note_count(self, vault: Vault) -> int:
        return sum(1 for _ in self._iter_notes(vault))

    # -- read ----------------------------------------------------------
    def list_notes(self, vault_name: str, folder: str = "", limit: int = 200) -> list[str]:
        v = self.get(vault_name)
        if not v or not v.exists:
            return []
        prefix = folder.strip("/") + "/" if folder and folder.strip("/") else ""
        out: list[str] = []
        for p in sorted(self._iter_notes(v)):
            rel = p.relative_to(v.root).as_posix()
            if prefix and not rel.startswith(prefix):
                continue
            out.append(rel)
            if len(out) >= limit:
                break
        return out

    def read_note(self, vault_name: str, path: str) -> Optional[str]:
        v = self.get(vault_name)
        if not v or not v.exists:
            return None
        try:
            target = self._resolve(v, self._with_md(path))
        except ValueError:
            return None
        if not target.is_file():
            return None
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > self.max_note_chars:
            text = text[: self.max_note_chars] + "\n\n…[truncated]…"
        return text

    def search(self, query: str, vault_name: str = "all", limit: int = 8) -> list[dict]:
        """Keyword search across note titles + bodies (semantic RAG is Phase 1b).

        Each hit: ``{"vault", "path", "title", "snippet"}``. All whitespace-split
        terms must appear (AND) in the note's stem or body.
        """
        q = (query or "").strip().lower()
        if not q:
            return []
        if _norm_name(vault_name) in ("", "all"):
            targets = self.vaults()
        else:
            v = self.get(vault_name)
            targets = [v] if v and v.exists else []
        terms = [t for t in re.split(r"\s+", q) if t]
        hits: list[dict] = []
        for v in targets:
            for p in self._iter_notes(v):
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                hay = (p.stem + "\n" + text).lower()
                if all(t in hay for t in terms):
                    hits.append(
                        {
                            "vault": v.name,
                            "path": p.relative_to(v.root).as_posix(),
                            "title": p.stem,
                            "snippet": _snippet(text, terms),
                        }
                    )
                    if len(hits) >= limit:
                        return hits
        return hits

    # -- write ---------------------------------------------------------
    def write_note(
        self, vault_name: str, path: str, content: str, append: bool = False
    ) -> tuple[bool, str]:
        v = self.get(vault_name)
        if not v:
            known = ", ".join(self.names()) or "none configured yet"
            return False, f"I don't have a vault called '{vault_name}'. I have: {known}."
        if not v.exists:
            return False, f"The {v.name} vault folder isn't set up yet."
        if not v.writable:
            return False, f"The {v.name} vault is read-only, so I can't save there."
        try:
            target = self._resolve(v, self._with_md(path))
        except ValueError as exc:
            return False, f"Can't write there: {exc}."
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            body = content or ""
            if append and target.is_file():
                existing = target.read_text(encoding="utf-8", errors="replace")
                body = existing.rstrip() + "\n\n" + body.strip() + "\n"
            elif not body.endswith("\n"):
                body += "\n"
            target.write_text(body, encoding="utf-8")
        except OSError as exc:
            return False, f"Couldn't save the note: {exc}."
        rel = target.relative_to(v.root.resolve()).as_posix()
        return True, f"{'Updated' if append else 'Saved'} '{rel}' in the {v.name} vault."


def _snippet(text: str, terms: list[str], width: int = 160) -> str:
    low = text.lower()
    idx = min((low.find(t) for t in terms if t in low), default=-1)
    if idx < 0:
        return text.strip()[:width].replace("\n", " ")
    start = max(0, idx - width // 3)
    body = text[start : start + width].strip().replace("\n", " ")
    return ("…" if start else "") + body + "…"


def _vaults_from_settings() -> dict[str, Vault]:
    writable = {
        n.strip().lower()
        for n in (settings.obsidian_writable or "").split(",")
        if n.strip()
    }
    spec = {
        "docker": settings.obsidian_docker_path,
        "personal": settings.obsidian_personal_path,
        "ai": settings.obsidian_ai_path,
    }
    out: dict[str, Vault] = {}
    for name, raw in spec.items():
        if raw and raw.strip():
            out[name] = Vault(
                name=name,
                root=Path(raw).expanduser(),
                writable=name in writable,
            )
    return out


knowledge_base = KnowledgeBase()
