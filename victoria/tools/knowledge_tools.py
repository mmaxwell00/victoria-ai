"""Tools that let Victoria read, search, and update the operator's Obsidian
knowledge bases (the Docker / Personal / AI vaults) by talking to her — e.g.
"search my Docker notes for the staging compose file", "save this to my AI
vault", "what do my personal notes say about the peptide protocol", "note that
down in my personal vault"."""
from victoria.tools.registry import registry
from victoria.knowledge.vaults import knowledge_base

_VAULT_DESC = (
    "Which knowledge base: 'docker' (work), 'personal', or 'ai' (Victoria's own)."
)


@registry.tool(
    name="search_notes",
    description=(
        "Search the operator's Obsidian knowledge bases (their notes) for a word "
        "or phrase and get back matching notes with a snippet and the note path. "
        "Use whenever the user asks what their notes / vault / Obsidian say about "
        "something, to find a note, or to recall previously saved knowledge — "
        "'search my notes for X', 'what do my Docker notes say about Y', 'find my "
        "note about Z'. Returns note paths you can then open with read_note."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words or phrase to look for."},
            "vault": {
                "type": "string",
                "description": (
                    "Knowledge base to search: 'docker', 'personal', 'ai', or "
                    "'all' (default) to search every vault."
                ),
            },
        },
        "required": ["query"],
    },
)
async def search_notes(query: str, vault: str = "all") -> str:
    hits = knowledge_base.search(query, vault_name=vault or "all")
    if not hits:
        return f"No notes matched '{query}'."
    lines = [f"Found {len(hits)} note(s):"]
    for h in hits:
        lines.append(f"- [{h['vault']}] {h['path']} — {h['snippet']}")
    return "\n".join(lines)


@registry.tool(
    name="read_note",
    description=(
        "Read the full contents of one note from a knowledge base, by its path "
        "(as returned by search_notes or list_notes). Use when you need the "
        "actual text of a note the user referred to or that you found."
    ),
    parameters={
        "type": "object",
        "properties": {
            "vault": {"type": "string", "description": _VAULT_DESC},
            "path": {
                "type": "string",
                "description": "Note path within the vault, e.g. 'Projects/victoria.md'.",
            },
        },
        "required": ["vault", "path"],
    },
)
async def read_note(vault: str, path: str) -> str:
    text = knowledge_base.read_note(vault, path)
    if text is None:
        return f"I couldn't find '{path}' in the {vault} vault."
    return text


@registry.tool(
    name="list_notes",
    description=(
        "List the note paths in a knowledge base (optionally within a sub-folder). "
        "Use to browse what notes exist before reading, or to answer 'what's in my "
        "X vault'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "vault": {"type": "string", "description": _VAULT_DESC},
            "folder": {
                "type": "string",
                "description": "Optional sub-folder to limit the listing.",
            },
        },
        "required": ["vault"],
    },
)
async def list_notes(vault: str, folder: str = "") -> str:
    notes = knowledge_base.list_notes(vault, folder=folder or "")
    if not notes:
        where = f" /{folder}" if folder else ""
        return f"No notes found in the {vault} vault{where}."
    return f"{len(notes)} note(s) in {vault}:\n" + "\n".join(f"- {n}" for n in notes)


@registry.tool(
    name="write_note",
    description=(
        "Create or update a note in a knowledge base. Use whenever the user asks "
        "you to save, note down, record, jot, or update something in their notes / "
        "vault / Obsidian — 'save this to my AI vault', 'note that … in my personal "
        "vault', 'add this to my Docker notes'. Set append=true to add to the end "
        "of an existing note instead of overwriting it. Writes Markdown; the '.md' "
        "extension is added automatically. This is the ONLY way to save a note — "
        "never claim you saved something without calling it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "vault": {"type": "string", "description": _VAULT_DESC},
            "path": {
                "type": "string",
                "description": "Note path / name, e.g. 'Ideas/dashboard.md' or 'Meeting notes'.",
            },
            "content": {"type": "string", "description": "The Markdown content to write."},
            "append": {
                "type": "boolean",
                "description": "Append to the note instead of overwriting (default false).",
            },
        },
        "required": ["vault", "path", "content"],
    },
)
async def write_note(vault: str, path: str, content: str, append: bool = False) -> str:
    _, msg = knowledge_base.write_note(vault, path, content, append=bool(append))
    return msg
