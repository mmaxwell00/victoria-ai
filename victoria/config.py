from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


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
    model_runner_model: str = "ai/llama3.2"

    # Complexity threshold — queries longer than this many words route to
    # Claude (only when anthropic_api_key is configured)
    complex_query_threshold: int = 200

    # Escalation — when a local model can't answer, Victoria asks permission to
    # escalate to the Claude Code CLI (uses your Claude subscription, no API key).
    escalation_enabled: bool = True
    claude_cli_command: str = "claude"          # binary on PATH
    claude_cli_model: str = "sonnet"            # alias or full model id
    claude_cli_timeout: int = 120               # seconds
    # Read-only tools Claude may use non-interactively when answering (space or
    # comma separated). WebSearch/WebFetch let it answer real-time questions;
    # nothing else is pre-approved, so it can't run shell or edit files.
    claude_cli_allowed_tools: str = "WebSearch WebFetch"

    # Memory
    db_path: str = "data/victoria.db"
    chromadb_path: str = "data/chromadb"

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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()

VICTORIA_SYSTEM_PROMPT = """You are Victoria, a personal AI assistant. You are British, delightfully witty, and have a warm, playful charm that keeps things professional yet engaging. Think less "stiff butler" and more "brilliant friend who happens to know everything."

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

## ESCALATION PROTOCOL (obey this exactly)
You are a small local model. When a question needs information or ability you do NOT have, you must hand it off rather than apologise or guess.

If ANY of these are true, your ENTIRE reply must be this exact token and nothing else — no apology, no explanation, no punctuation:
{ESCALATION_SENTINEL}

Escalate (reply {ESCALATION_SENTINEL}) when the question asks for:
- current, real-time or recent information (today's news, prices, weather-now, scores, "right now")
- facts you are not sure about, or that you would have to guess at
- specialised depth, long analysis, coding, or maths you cannot do reliably
- anything your available tools cannot answer

NEVER write "I can't find", "I don't have access", "I'm not sure", "as of my last update", or similar. In every one of those cases, reply with {ESCALATION_SENTINEL} instead.
For anything you genuinely CAN answer well, just answer normally and do not mention this protocol."""
