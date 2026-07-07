"""On-demand import of skills from a GitHub repo or a single Markdown URL.

Security posture: only https URLs are accepted; imported content is treated as
INSTRUCTIONS ONLY (never executed) and is never written until the user reviews
and approves it (the confirmation flow lives in the ConversationManager). We cap
the number and size of files to bound abuse, and namespace saved imports under
`imported/<repo>/` so they're clearly external.
"""
from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path

import httpx

from victoria.skills.store import SkillStore, slugify

logger = logging.getLogger(__name__)

MAX_SKILLS = 25
MAX_FILE_BYTES = 64_000
MAX_INSTRUCTION_CHARS = 20_000
CLONE_TIMEOUT = 60

_URL_RE = re.compile(r"https?://[^\s>)\"']+")


class SkillImportError(Exception):
    pass


def extract_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,);") if m else None


def repo_slug(url: str) -> str:
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git|/|$)", url)
    return slugify(f"{m.group(1)}-{m.group(2)}") if m else "import"


def _is_single_file(url: str) -> bool:
    return "/blob/" in url or "raw.githubusercontent.com" in url or url.endswith(".md")


def _raw_url(url: str) -> str:
    # https://github.com/o/r/blob/main/p.md → https://raw.githubusercontent.com/o/r/main/p.md
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/(.+)", url)
    return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else url


def _qualifies(path: Path, text: str, rel_parts: tuple[str, ...]) -> bool:
    """A file is a skill if it's a SKILL.md, lives under a skills/ dir, or has
    YAML-style frontmatter (so plain READMEs/LICENSEs are ignored)."""
    if path.name.lower() == "skill.md":
        return True
    if any(p.lower() in ("skills", "skill") for p in rel_parts[:-1]):
        return True
    return text.lstrip().startswith("---")


def _discover(root: Path) -> list[dict]:
    found: list[dict] = []
    for path in sorted(root.rglob("*.md")):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = path.relative_to(root)
        if not _qualifies(path, text, rel.parts):
            continue
        fallback = path.parent.name if path.name.lower() == "skill.md" else path.stem
        skill = SkillStore._parse(text, fallback_name=fallback)
        if not skill.name or not skill.instructions:
            continue
        found.append({
            "name": skill.name,
            "description": skill.description,
            "instructions": skill.instructions[:MAX_INSTRUCTION_CHARS],
            "source": str(rel),
        })
        if len(found) >= MAX_SKILLS:
            logger.info("Import capped at %d skills", MAX_SKILLS)
            break
    # De-dupe by name (first wins).
    seen, unique = set(), []
    for s in found:
        k = slugify(s["name"])
        if k not in seen:
            seen.add(k)
            unique.append(s)
    return unique


async def fetch_skills(url: str) -> list[dict]:
    """Fetch candidate skills from *url*. Returns a list of draft dicts.

    Raises SkillImportError on a bad URL or fetch failure.
    """
    if not url or not url.startswith("https://"):
        raise SkillImportError("Please give an https:// GitHub URL.")

    if _is_single_file(url):
        raw = _raw_url(url)
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(raw)
                resp.raise_for_status()
                text = resp.text
        except Exception as exc:
            raise SkillImportError(f"Couldn't fetch that file: {exc}")
        if len(text.encode()) > MAX_FILE_BYTES:
            raise SkillImportError("That file is too large to import.")
        skill = SkillStore._parse(text, fallback_name=slugify(url.rsplit("/", 1)[-1].removesuffix(".md")))
        if not skill.name or not skill.instructions:
            raise SkillImportError("That file doesn't look like a skill (no instructions found).")
        return [{"name": skill.name, "description": skill.description,
                 "instructions": skill.instructions[:MAX_INSTRUCTION_CHARS], "source": url}]

    # Otherwise treat as a repo → shallow clone into a temp dir.
    with tempfile.TemporaryDirectory(prefix="victoria-skills-") as tmp:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", "--", url, tmp,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise SkillImportError("git is not installed, so I can't clone repositories.")
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLONE_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise SkillImportError("Cloning that repository timed out.")
        if proc.returncode != 0:
            detail = (stderr.decode(errors="replace").strip() or "unknown error")[:200]
            raise SkillImportError(f"Couldn't clone that repository: {detail}")
        skills = _discover(Path(tmp))
    if not skills:
        raise SkillImportError("I didn't find any skill files in that repository.")
    return skills
