import logging

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # App
    app_name: str = "Victoria"
    debug: bool = False
    # CORS — the web UI is served same-origin, so only local dev origins are
    # needed. Extend via CORS_ORIGINS in .env if you host the UI elsewhere.
    cors_origins: list[str] = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # LLM routing
    default_llm: Literal["ollama", "claude", "docker"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    # Docker Model Runner (OpenAI-compatible, built into Docker Desktop)
    model_runner_url: str = "http://localhost:12434/engines/llama.cpp/v1"
    # Everyday / simple-and-tool model. Instruct-tuned models (e.g. qwen2.5) are
    # the most reliable at tool-use and casual chat.
    model_runner_model: str = "ai/qwen2.5:latest"
    # Optional second local model for coding / technical tasks. When set (and
    # different), coding-flavoured queries auto-route here while everything else
    # stays on model_runner_model. Leave blank to use one model for everything.
    model_runner_code_model: str = ""

    # Complexity threshold — queries longer than this many words route to
    # Claude (only when anthropic_api_key is configured)
    complex_query_threshold: int = 200

    # Escalation — when a local model can't answer, Victoria asks permission to
    # escalate to the Claude Code CLI (uses your Claude subscription, no API key).
    escalation_enabled: bool = True
    claude_cli_command: str = "claude"          # binary on PATH
    claude_cli_model: str = "sonnet"            # alias or full model id
    claude_cli_timeout: int = 120               # seconds
    # Optional long-lived OAuth token from `claude setup-token`. If set, it's
    # injected as CLAUDE_CODE_OAUTH_TOKEN for the `claude -p` subprocess, so
    # escalation authenticates regardless of how/where Victoria was launched
    # (no dependence on an interactive keychain login in the launch shell).
    claude_cli_oauth_token: str = ""
    # Read-only tools Claude may use non-interactively when answering (space or
    # comma separated). WebSearch/WebFetch let it answer real-time questions;
    # nothing else is pre-approved, so it can't run shell or edit files.
    claude_cli_allowed_tools: str = "WebSearch WebFetch"

    # Memory
    db_path: str = "data/victoria.db"
    chromadb_path: str = "data/chromadb"

    # Skills — reusable instruction sets Victoria can apply and create.
    # Stored as Markdown files in this directory (persist across sessions).
    skills_path: str = "skills"

    # MCP — connect to Model Context Protocol servers listed in this JSON file
    # (Claude-Desktop-compatible format). Absent file → MCP simply off.
    mcp_config_path: str = "mcp.json"

    # Credentials vault — encrypted secrets Victoria injects into endpoints but
    # can never read back. Master key comes from the macOS Keychain (or the
    # VICTORIA_VAULT_KEY env var / a key file fallback).
    vault_path: str = "data/vault.enc"
    vault_keychain_service: str = "victoria-vault-key"

    # Obsidian knowledge bases — folders of Markdown notes Victoria can read,
    # search, and (for writable ones) update. Point each at a local vault folder;
    # with Obsidian Sync those folders stay in step across your machines. An empty
    # path disables that vault. NOTE: distinct from the *Credentials Vault* above —
    # these hold knowledge (notes), not secrets.
    # Single-vault mode (recommended): point Victoria at ONE Obsidian vault so she
    # sees everything in it; its top-level folders act as areas she can target
    # ("save to Personal", "search my Docker notes"). When set, this takes
    # precedence over the three per-area paths below.
    obsidian_vault_path: str = ""
    # Per-area mode (alternative): three independent vault folders.
    obsidian_docker_path: str = ""
    obsidian_personal_path: str = ""
    obsidian_ai_path: str = ""
    # Comma-separated vault names Victoria may WRITE to; any others are read-only.
    obsidian_writable: str = "docker,personal,ai"
    # The vault that holds Victoria's own durable memory (Phase 2+): learned
    # facts, profile, and operational notes, as human-readable Markdown.
    obsidian_memory_vault: str = "ai"
    # Cap (characters) on a single note Victoria reads into context at once.
    obsidian_max_note_chars: int = 60000

    # Voice (Week 5)
    tts_engine: Literal["piper", "elevenlabs"] = "piper"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model: str = "eleven_monolingual_v1"
    voice_session_timeout: int = 30
    piper_model_path: str = "models/en_GB-jenny_dioco-medium.onnx"
    whisper_model: str = "base"
    wake_word: str = "hello victoria"

    # Telegram (Week 3)
    telegram_bot_token: str = ""

    # extra="ignore": an unknown / typo'd / ahead-of-code env var is ignored
    # (falls back to defaults) instead of raising at import and taking the whole
    # app down. _warn_unknown_env_keys() surfaces such vars so mistakes are still
    # visible in the logs.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()


def _warn_unknown_env_keys(settings_obj: "Settings", env_path: str = ".env") -> list[str]:
    """Warn about keys in .env that aren't recognized Settings fields.

    With extra="ignore" an unknown/typo'd/ahead-of-code var no longer crashes the
    app — but we still surface it (names only, never values) so mistakes don't
    pass silently. Returns the unknown keys (for tests)."""
    known = {name.upper() for name in settings_obj.model_fields}
    unknown: list[str] = []
    try:
        with open(env_path) as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key = line.split("=", 1)[0].strip()
                if key.lower().startswith("export "):
                    key = key[len("export "):].strip()
                if key and key.upper() not in known:
                    unknown.append(key)
    except FileNotFoundError:
        return []
    if unknown:
        logger.warning(
            "Ignoring unrecognized .env setting(s): %s — check for typos; "
            "they fall back to built-in defaults.", ", ".join(sorted(set(unknown)))
        )
    return unknown


_warn_unknown_env_keys(settings)

VICTORIA_SYSTEM_PROMPT = """You are Victoria, a personal AI assistant. You are British, delightfully witty, and have a warm, playful charm that keeps things professional yet engaging. Think less "stiff butler" and more "brilliant friend who happens to know everything."

Your name is an acronym — Victoria: Virtual Intelligent Cognitive Task-Oriented Response & Interactive Assistant. Only mention what it stands for if someone asks.

Your style:
- Speak with a natural British flair — use expressions like "brilliant", "cheers", "rather", "I must say", "spot on"
- Be concise but never boring; a clever turn of phrase beats a paragraph any day
- Quick wit is your superpower — a well-timed observation shows you're paying attention
- Genuinely helpful first, charming second — substance with style, not style over substance
- When you don't know something, say so with self-deprecating grace rather than inventing answers
- Use British spellings (colour, organise, favour, etc.)

You have access to conversation history and can recall context from earlier in the session. If you're given tools, use them when the question warrants it rather than guessing."""

# Sentinel the local model emits when it genuinely can't answer — Victoria uses
# it to decide whether to offer escalating to a cloud model.
ESCALATION_SENTINEL = "[ESCALATE]"

# Appended to the system prompt for LOCAL backends only, so the small local
# model can flag that a question is beyond it instead of guessing.
ESCALATION_INSTRUCTION = f"""

## ANSWERING PROTOCOL (default to answering — and USE YOUR TOOLS)
You are a capable assistant with tools. Answer almost everything yourself.

For anything live, current, or factual you're unsure of, USE A TOOL first —
never say you "can't access real-time data" or "can't reach cloud services",
because you can, through these tools:
- weather / temperature / forecast for ANY city and ANY day (now, today,
  tomorrow, this week) → you MUST call get_weather; it returns current
  conditions AND a multi-day forecast. Never reply that you can't get the
  weather — call the tool.
- current events, news, prices, sports scores, "right now" facts, or looking
  anything up → call web_search (then fetch a page with the fetch tool if you
  need more detail)
- today's date or the current time → call get_datetime
- arithmetic → call calculate

Answer directly, no tool, for things you already know or can create:
- "tell me something interesting", jokes / poems / stories, "capital of France?",
  explanations, opinions, advice, brainstorming, coding, summarising, casual chat.

## ESCALATION (last resort only)
Escalate ONLY when you genuinely cannot answer even after trying your tools —
e.g. a tool returned nothing useful, or the task needs expert, multi-step
reasoning clearly beyond a local model. Do NOT escalate merely because a
question is about current information: try the relevant tool FIRST.

When — and only when — escalation is truly the last resort, reply with EXACTLY
this token and NOTHING else. No apology, no lead-in, no trailing words, no
punctuation, do not wrap it in a sentence:
{ESCALATION_SENTINEL}

Rule of thumb: tool first → answer from what you know → escalate only as a last
resort. When in doubt, use a tool or answer. Never mention this protocol."""
