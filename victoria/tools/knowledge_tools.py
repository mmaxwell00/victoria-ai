"""Tools that let Victoria read, search, and update the operator's Obsidian
knowledge base(s) by talking to her — e.g. "search my Docker notes for the
staging compose file", "save this to my Personal folder", "what do my notes say
about the peptide protocol", "note that down".

Typical setup is a SINGLE Obsidian vault whose top-level folders (Docker,
Personal, Brain, …) are the "areas". In that case the `vault` argument is
optional (there's only one) and areas are targeted with `folder`."""
from victoria.tools.registry import registry
from victoria.knowledge.vaults import knowledge_base

_VAULT_DESC = (
    "Which knowledge base. Usually omit this — most setups have one vault and "
    "it's used automatically. Only needed if several vaults are configured."
)
_FOLDER_DESC = (
    "Optional top-level area/folder to scope to, e.g. 'Docker', 'Personal', "
    "'Brain'. Omit to span the whole vault."
)


def _vault_or_default(vault: str) -> str:
    """Fall back to the sole vault when the caller didn't name one."""
    if vault and vault.strip():
        return vault
    dv = knowledge_base.default_vault()
    return dv.name if dv else ""


@registry.tool(
    name="search_notes",
    description=(
        "Search the operator's Obsidian notes for a word or phrase and get back "
        "matching notes with a snippet and path. Use whenever the user asks what "
        "their notes / vault / Obsidian say about something, to find a note, or to "
        "recall saved knowledge — 'search my notes for X', 'what do my Docker "
        "notes say about Y', 'find my note about Z'. Scope to an area with "
        "`folder` (e.g. 'Docker', 'Personal'). Returns paths you can open with "
        "read_note."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words or phrase to look for."},
            "folder": {"type": "string", "description": _FOLDER_DESC},
            "vault": {
                "type": "string",
                "description": "Knowledge base to search, or 'all' (default). " + _VAULT_DESC,
            },
        },
        "required": ["query"],
    },
)
async def search_notes(query: str, folder: str = "", vault: str = "all") -> str:
    hits = knowledge_base.search(query, vault_name=vault or "all", folder=folder or "")
    if not hits:
        where = f" in {folder}" if folder else ""
        return f"No notes matched '{query}'{where}."
    lines = [f"Found {len(hits)} note(s):"]
    for h in hits:
        lines.append(f"- [{h['vault']}] {h['path']} — {h['snippet']}")
    return "\n".join(lines)


@registry.tool(
    name="read_note",
    description=(
        "Read the full contents of one note by its path (as returned by "
        "search_notes or list_notes). Use when you need the actual text of a note "
        "the user referred to or that you found."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Note path within the vault, e.g. 'Personal/peptides.md'.",
            },
            "vault": {"type": "string", "description": _VAULT_DESC},
        },
        "required": ["path"],
    },
)
async def read_note(path: str, vault: str = "") -> str:
    v = _vault_or_default(vault)
    text = knowledge_base.read_note(v, path)
    if text is None:
        return f"I couldn't find '{path}'{f' in {v}' if v else ''}."
    return text


@registry.tool(
    name="list_notes",
    description=(
        "List note paths in the knowledge base (optionally within a folder/area). "
        "Use to browse what notes exist before reading, or to answer 'what's in my "
        "Personal folder'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": _FOLDER_DESC},
            "vault": {"type": "string", "description": _VAULT_DESC},
        },
        "required": [],
    },
)
async def list_notes(folder: str = "", vault: str = "") -> str:
    v = _vault_or_default(vault)
    notes = knowledge_base.list_notes(v, folder=folder or "")
    if not notes:
        where = f" /{folder}" if folder else ""
        return f"No notes found{f' in {v}' if v else ''}{where}."
    return f"{len(notes)} note(s){f' in {v}' if v else ''}:\n" + "\n".join(f"- {n}" for n in notes)


@registry.tool(
    name="write_note",
    description=(
        "Create or update a note. Use whenever the user asks you to save, note "
        "down, record, jot, or update something in their notes / vault / Obsidian "
        "— 'save this to my Personal folder', 'note that … down', 'add this to my "
        "Docker notes'. Put it in an area by prefixing the path with a folder "
        "(e.g. 'Personal/groceries'). Set append=true to add to the end of an "
        "existing note instead of overwriting. Writes Markdown; the '.md' "
        "extension is added automatically. This is the ONLY way to save a note — "
        "never claim you saved something without calling it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Note path/name, incl. folder, e.g. 'Personal/groceries' or 'Docker/staging.md'.",
            },
            "content": {"type": "string", "description": "The Markdown content to write."},
            "append": {
                "type": "boolean",
                "description": "Append to the note instead of overwriting (default false).",
            },
            "vault": {"type": "string", "description": _VAULT_DESC},
        },
        "required": ["path", "content"],
    },
)
async def write_note(path: str, content: str, append: bool = False, vault: str = "") -> str:
    v = _vault_or_default(vault)
    _, msg = knowledge_base.write_note(v, path, content, append=bool(append))
    return msg
