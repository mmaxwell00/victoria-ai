"""Tools that let Victoria apply and create skills.

- use_skill:  load a skill's full instructions on demand (progressive disclosure).
- save_skill: STAGE a new/updated skill for the user to confirm — it is not
              written to disk until the user approves (handled in the
              ConversationManager). This enforces the draft-then-confirm flow.
- delete_skill / list_skills: manage the library.
"""
from victoria.tools.registry import registry
from victoria.skills.store import skill_store

# Single staged draft awaiting user confirmation (personal, single-user app).
_staged_skill: dict = {}


def stage_skill(name: str, description: str, instructions: str) -> None:
    _staged_skill.clear()
    _staged_skill.update(name=name, description=description, instructions=instructions)


def pop_staged_skill() -> dict | None:
    if not _staged_skill:
        return None
    draft = dict(_staged_skill)
    _staged_skill.clear()
    return draft


def has_staged_skill() -> bool:
    return bool(_staged_skill)


def peek_staged_skill() -> dict | None:
    return dict(_staged_skill) if _staged_skill else None


@registry.tool(
    name="use_skill",
    description=(
        "Load the full instructions for one of your saved skills so you can follow "
        "them. Call this when a skill from your skill list is relevant to the user's "
        "request, then follow the instructions it returns."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The skill name, e.g. 'email-drafter'"},
        },
        "required": ["name"],
    },
)
def use_skill(name: str) -> str:
    skill = skill_store.get(name)
    if not skill:
        available = ", ".join(skill_store.names()) or "(none yet)"
        return f"No skill named '{name}'. Available skills: {available}"
    return f"SKILL '{skill.name}' — follow these instructions:\n\n{skill.instructions}"


@registry.tool(
    name="save_skill",
    description=(
        "Stage a NEW or UPDATED skill (a reusable instruction set) as a draft. This "
        "does NOT save immediately — it stages the draft, which is saved only once the "
        "user confirms. Call this WHEN drafting the skill; then show the user the "
        "proposed name, description and instructions and ask them to confirm."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short kebab-case skill name, e.g. 'standup-update'"},
            "description": {"type": "string", "description": "One line: what the skill does and when to use it"},
            "instructions": {"type": "string", "description": "The step-by-step instructions Victoria should follow"},
        },
        "required": ["name", "description", "instructions"],
    },
)
def save_skill(name: str, description: str, instructions: str) -> str:
    stage_skill(name, description, instructions)
    return (
        f"Staged draft of skill '{name}'. It is NOT saved yet — ask the user to "
        f"confirm (yes/no) and it will be saved on their approval."
    )


@registry.tool(
    name="list_skills",
    description="List all of Victoria's saved skills with their descriptions.",
    parameters={"type": "object", "properties": {}},
)
def list_skills() -> str:
    index = skill_store.index()
    return index or "No skills saved yet."


@registry.tool(
    name="delete_skill",
    description="Delete a saved skill by name. Only do this when the user explicitly asks.",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The skill name to delete"}},
        "required": ["name"],
    },
)
def delete_skill(name: str) -> str:
    return f"Deleted skill '{name}'." if skill_store.delete(name) else f"No skill named '{name}' to delete."
