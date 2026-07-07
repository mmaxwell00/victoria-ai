"""Skills: named, reusable instruction sets Victoria can apply and create.

A skill is a Markdown file with a small frontmatter block:

    ---
    name: email-drafter
    description: Draft a concise, professional email from a purpose + recipient.
    ---
    When the user asks you to write an email:
    1. Confirm the recipient, purpose, and desired tone.
    2. ...

Skills are stored on disk so they persist across sessions ("learned") and can
be hand-edited. They contain instructions only — never executable code.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from victoria.config import settings

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "skill"


@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    path: Optional[Path] = None

    def to_markdown(self) -> str:
        return (
            f"---\n"
            f"name: {self.name}\n"
            f"description: {self.description}\n"
            f"---\n"
            f"{self.instructions.strip()}\n"
        )


class SkillStore:
    """CRUD over Markdown skill files in a single directory."""

    def __init__(self, skills_dir: Optional[str] = None):
        self.dir = Path(skills_dir or settings.skills_path)
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- parsing ---------------------------------------------------------- #
    @staticmethod
    def _parse(text: str, fallback_name: str) -> Skill:
        name, description, body = fallback_name, "", text
        m = _FRONTMATTER_RE.match(text)
        if m:
            front, body = m.group(1), m.group(2)
            for line in front.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    key, val = key.strip().lower(), val.strip()
                    if key == "name" and val:
                        name = val
                    elif key == "description":
                        description = val
        return Skill(name=name, description=description, instructions=body.strip())

    # -- reads ------------------------------------------------------------ #
    def list(self) -> list[Skill]:
        skills, seen = [], set()
        # Recursive so namespaced subdirs (e.g. imported/<repo>/) are picked up.
        # Top-level files sort first, so a local skill wins a name collision.
        for path in sorted(self.dir.rglob("*.md")):
            try:
                fallback = path.parent.name if path.name.lower() == "skill.md" else path.stem
                skill = self._parse(path.read_text(encoding="utf-8"), fallback_name=fallback)
                key = slugify(skill.name)
                if key in seen:
                    continue
                seen.add(key)
                skill.path = path
                skills.append(skill)
            except Exception:
                logger.exception("Failed to read skill %s", path)
        return skills

    def get(self, name: str) -> Optional[Skill]:
        target = slugify(name)
        for skill in self.list():
            if slugify(skill.name) == target or (skill.path and skill.path.stem == target):
                return skill
        return None

    def names(self) -> list[str]:
        return [s.name for s in self.list()]

    def index(self) -> str:
        """A compact 'name — description' list for the system prompt."""
        skills = self.list()
        if not skills:
            return ""
        return "\n".join(f"- {s.name}: {s.description or '(no description)'}" for s in skills)

    def exists(self, name: str) -> bool:
        return self.get(name) is not None

    # -- writes ----------------------------------------------------------- #
    def save(self, name: str, description: str, instructions: str,
             subdir: Optional[str] = None) -> Skill:
        skill = Skill(name=name.strip(), description=description.strip(),
                      instructions=instructions.strip())
        target_dir = self.dir / subdir if subdir else self.dir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{slugify(name)}.md"
        path.write_text(skill.to_markdown(), encoding="utf-8")
        skill.path = path
        logger.info("Saved skill %r → %s", skill.name, path)
        return skill

    def delete(self, name: str) -> bool:
        skill = self.get(name)
        if skill and skill.path and skill.path.exists():
            skill.path.unlink()
            logger.info("Deleted skill %r", skill.name)
            return True
        return False


# Module-level singleton used by tools + the conversation manager.
skill_store = SkillStore()
