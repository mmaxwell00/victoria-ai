# CLAUDE.md

> Project bible for any Claude session working on Victoria. Read this first.
> Decisions log lives at `docs/DECISIONS.md` — read that second.

---

## What is Victoria

Victoria is a personal Jarvis-style AI assistant. She runs on the user's own hardware (no cloud dependencies by default), is accessible via web HUD, Telegram, voice, or terminal, and routes intelligently between local LLMs (Docker Model Runner, Ollama) and Claude. She learns the user's style across conversations through layered memory and a persistent profile.

The vibe is **British, witty, brilliant** — think Jarvis from the MCU, not a generic chatbot. That tone is settled and not up for debate.

---

## About Alex (the user)

Alex is the maintainer of Victoria — an experienced engineer with a strong architecture and security background who is still building Python fluency. Victoria is both a real personal tool *and* a learning vehicle. Assume deep architecture/security instincts but explain Python-specific patterns when relevant; don't explain architecture or security concepts unless asked.

### How Alex wants Claude to work

- **Clear, honest, direct.** Push back when something is wrong. Don't hedge.
- **Warm, professional, intelligent** tone — not corporate, not cutesy.
- **Use analogies** when explaining technical concepts.
- **Assume technical depth.**
- **Don't pad responses** with caveats and disclaimers.
- **Code over ceremony** — show working artifacts over plans whenever possible.

---

## Repository at a glance

```
victoria-ai/
├── victoria/
│   ├── config.py               # All settings (env-driven)
│   ├── main.py                 # FastAPI app entrypoint
│   ├── core/
│   │   ├── conversation.py     # Central conversation manager
│   │   ├── llm_router.py       # Routes between Docker/Ollama/Claude
│   │   ├── memory.py           # Per-session SQLite conversation history
│   │   ├── semantic_memory.py  # ChromaDB cross-session semantic recall
│   │   ├── user_profile.py     # Persistent user profile
│   │   ├── profile_extractor.py# Regex + LLM style learning
│   │   └── transcription.py    # Whisper STT
│   ├── interfaces/
│   │   ├── api.py              # REST + streaming endpoints
│   │   ├── telegram_bot.py     # Telegram bot
│   │   └── static/             # JARVIS-style web HUD
│   ├── tools/
│   │   ├── registry.py         # Decorator-based tool registry
│   │   ├── web_search.py       # DuckDuckGo
│   │   ├── weather.py          # wttr.in
│   │   ├── datetime_tool.py
│   │   └── calculator.py
│   └── voice/
│       ├── conversation.py     # Voice session loop
│       ├── wake_word.py        # "Hello Victoria"
│       ├── audio.py            # Mic capture
│       └── tts/
│           ├── piper_tts.py    # Local
│           └── elevenlabs_tts.py
├── scripts/
│   ├── chat.py                 # Terminal chat
│   ├── run_telegram.py
│   └── run_voice.py
├── tests/                      # 305 pytest tests
├── docs/
│   └── DECISIONS.md            # Running decision log
├── CLAUDE.md                   # This file
├── README.md
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Architecture in one diagram

```
Interfaces (Web HUD / Telegram / Voice / Terminal)
            │
            ▼
   Conversation Manager
   ├─ Inject user profile
   ├─ Pull semantic memory
   ├─ Pull RAG document context  (planned)
   ├─ Route tool calls
   └─ Manage session history
            │
            ▼
       LLM Router
   ├─ Default: Docker Model Runner (local, free)
   ├─ Fallback: Ollama (local)
   └─ Escalate: Claude Sonnet 4.6 (when complexity threshold hit)
```

**Three memory layers, always-on:**

1. **Session memory** — full conversation within a session (SQLite)
2. **Semantic memory** — ChromaDB vector recall across all past sessions
3. **User profile** — persistent preferences, style, explicit memories injected into every system prompt

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| API | FastAPI + Uvicorn |
| Local LLM | Docker Model Runner / Ollama |
| Cloud LLM | Anthropic Claude (Sonnet 4.6) |
| STT | faster-whisper |
| TTS | Piper (local) / ElevenLabs (cloud) |
| Wake word | openWakeWord |
| Telegram | python-telegram-bot |
| Session memory | SQLite |
| Semantic memory | ChromaDB (embedded) |
| Web search tool | DuckDuckGo (no API key) |
| Tests | pytest |
| Container | Docker Compose |

---

## Conventions

- **Python 3.11+ required.** Typing improvements over 3.10 are used.
- **Virtual env in `.venv/`** — standard `python -m venv`, not Poetry/PDM.
- **Dependencies in `requirements.txt`** — keep it simple.
- **Config via `.env`** — see `.env.example` for the full list of vars.
- **Tests with pytest** — 305 tests currently. Never let coverage regress.
- **Decorator-based tool registry** — `@tool` in `victoria/tools/registry.py`.
- **All settings env-driven** — never hardcode credentials or paths.
- **Async where it counts** — FastAPI endpoints, Telegram handlers. Sync OK for tool calls.
- **Type hints expected** on public functions.

---

## Running things

```bash
# Web HUD
uvicorn victoria.main:app --reload          # → http://localhost:8000

# Terminal chat
python scripts/chat.py

# Telegram bot
python scripts/run_telegram.py

# Voice interface
python scripts/run_voice.py

# Tests
python -m pytest tests/ -v

# Full stack (API + Telegram containers)
docker compose up --build
```

---

## Current focus

> When this changes, update both this section AND append an entry to `docs/DECISIONS.md`.

**Recently shipped:** reliable local tool-use (stream-with-tools + forced-tool retry + history de-poisoning), the sidebar avatar (framed portrait with state-coloured glow), and the HUD dashboard row (weather / stocks / news + conversational tracking). MCP client integration is done.

**Next up:** RAG document ingestion (ChromaDB doc collection separate from semantic memory; local embeddings — not yet built), plus more MCP servers (Gmail, video).

- **Open questions:** see `docs/DECISIONS.md` § Open

---

## Working with Claude on Victoria

### When a fresh Claude session picks up work

1. **Read this file first.** It's the project bible.
2. **Read `docs/DECISIONS.md` next.** Decisions made in prior sessions live there.
3. **Read `README.md`** if you need user-facing context (install, features, REST API surface).
4. **Don't propose architecture changes that contradict DECISIONS.md** without flagging it explicitly as a revisit. Alex decides if a decision is reopened.
5. **Code lives in the repo. Plans live in chat.** When generating code, write proper files and tell Alex which paths.
6. **Commit messages are navigation aids.** Make them clear and informative.

### Working style

- Prefer working code over plans
- Ship the smallest version that works, then layer
- Never invent APIs or library functions — verify if uncertain
- When integrating third-party services (MCP servers, models, providers), check current docs; this space moves monthly
- Tests aren't optional. New modules need pytest coverage.
- If Claude isn't sure, ask. Don't guess.

### What Claude should NOT do

- Don't rebuild what's already working
- Don't add dependencies without checking with Alex
- Don't change Victoria's personality (British, witty, brilliant) — that's settled
- Don't refactor for taste; refactor only when something is broken or in the way
- Don't strip Alex's existing patterns to impose framework preferences

---

## Personality reference

Victoria's voice — concrete examples for system prompts and any tone tuning:

- **British** by default. Uses "shall," "rather," "quite," "brilliant," "indeed."
- **Witty, never sarcastic at Alex's expense.** Dry humor, not snark.
- **Confident, not deferential.** She tells Alex when he's wrong, gently.
- **Concise.** Long-winded answers break the Jarvis illusion.
- **Address Alex as "Alex"** by default, "sir" sparingly and only when contextually apt.
- **Acknowledges her own limits** without performative humility ("I can't reach that system from here" vs. "I'm just an AI and...").

---

*Last updated: see `docs/DECISIONS.md` for the chronological record.*
