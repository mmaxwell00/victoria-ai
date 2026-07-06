# Victoria AI

A personal Jarvis-style AI assistant — British, witty, and built to learn your style.

Victoria runs on your own hardware, costs nothing by default (local LLM via Docker Model Runner or Ollama), and learns your preferences across every conversation. Talk to her through a web chat UI, Telegram, a terminal, or your voice.

---

## What's in this repo

```
victoria-ai/
├── victoria/
│   ├── config.py               # All settings (env-driven)
│   ├── main.py                 # FastAPI app entrypoint
│   ├── core/
│   │   ├── conversation.py     # Central conversation manager
│   │   ├── llm_router.py       # Routes between Docker/Ollama/Claude backends
│   │   ├── memory.py           # Per-session SQLite conversation history
│   │   ├── semantic_memory.py  # ChromaDB cross-session semantic recall
│   │   ├── user_profile.py     # Persistent user profile (preferences, memories)
│   │   ├── profile_extractor.py# Regex + LLM style learning from conversations
│   │   └── transcription.py    # Whisper speech-to-text
│   ├── interfaces/
│   │   ├── api.py              # REST API (chat, stream, history, profile) — /v1 prefix
│   │   └── telegram_bot.py     # Telegram bot interface
│   ├── static/                 # JARVIS-style HUD web interface (HTML/CSS/JS)
│   ├── tools/
│   │   ├── registry.py         # Decorator-based tool registry
│   │   ├── web_search.py       # DuckDuckGo search (no API key)
│   │   ├── weather.py          # wttr.in weather (no API key)
│   │   ├── datetime_tool.py    # Current date/time with timezone
│   │   └── calculator.py       # Safe AST-based math evaluator
│   └── voice/
│       ├── conversation.py     # Voice session loop
│       ├── wake_word.py        # "Hello Victoria" wake word detection
│       ├── audio.py            # Microphone capture + silence detection
│       └── tts/
│           ├── piper_tts.py    # Local Piper TTS (free)
│           └── elevenlabs_tts.py # ElevenLabs TTS (paid, near-human)
├── scripts/
│   ├── chat.py                 # Terminal chat client
│   ├── run_telegram.py         # Telegram bot runner
│   └── run_voice.py            # Voice interface runner
├── tests/                      # 83 pytest tests
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## How Victoria works

```
┌─────────────────────────────────────────────────┐
│              Interfaces                          │
│   Web UI  ·  Telegram  ·  Voice  ·  Terminal    │
└──────────────────────┬──────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │   Conversation Manager  │
          │  • User profile inject  │
          │  • Semantic recall      │
          │  • Tool routing         │
          │  • Session memory       │
          └────────────┬────────────┘
                       │
          ┌────────────▼────────────┐
          │       LLM Router        │
          │  Docker Model Runner    │
          │  Ollama  ·  Claude API  │
          └─────────────────────────┘
```

**Memory layers (layered, always on):**
1. **Session memory** — full conversation history within a session (SQLite)
2. **Semantic memory** — ChromaDB vector search across all past sessions; relevant context surfaces automatically
3. **User profile** — persistent preferences, style, and explicit memories injected into every system prompt

### Local-first escalation (ask before going to the cloud)

Victoria always tries the **local model first**. If it genuinely can't answer — a real-time question, something outside its knowledge, or a backend error — it doesn't guess. It **pauses and asks you**:

> *"I'm afraid that one's rather beyond my local wits just now. Shall I put it to Claude for a proper answer? (yes / no)"*

Reply **yes** and she escalates to the **Claude Code CLI** — which uses your existing Claude **subscription**, so no API key is required. Reply **no** (or just ask something else) and nothing leaves your machine. Escalated answers may use read-only web search (`WebSearch`/`WebFetch`) so real-time questions get real answers.

How the local model signals it's stuck: local backends are given an *escalation protocol* in their system prompt and emit a hidden `[ESCALATE]` token, which Victoria intercepts (it never reaches you). Empty replies and backend errors trigger the same offer. Toggle the whole feature with `ESCALATION_ENABLED`; configure it via the `CLAUDE_CLI_*` settings below.

> Requires the [Claude Code CLI](https://claude.com/claude-code) installed and logged in (`claude` on your PATH). Adding ChatGPT as an alternative is a planned follow-up.

---

## Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3.11+ | Runtime | `brew install python@3.11` |
| Docker Desktop | Model Runner + containers | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop) |
| ffmpeg | Whisper audio transcription | `brew install ffmpeg` |
| PortAudio | Microphone input (voice only) | `brew install portaudio` |

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/mmaxwell00/victoria-ai.git
cd victoria-ai
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

```bash
# Use Docker Model Runner (built into Docker Desktop) — free, no API key
DEFAULT_LLM=docker
MODEL_RUNNER_MODEL=ai/llama3.2

# Optional: add your Anthropic key to unlock Claude as a fallback
ANTHROPIC_API_KEY=sk-ant-...
```

### 5. Pull a local model

Victoria uses Docker Desktop's built-in Model Runner. Enable it first:

> Docker Desktop → Settings → Features in development → **Docker Model Runner** → Apply & Restart

Then pull a model:

```bash
docker model pull ai/llama3.2        # ~2GB, fast, good for most tasks
# or
docker model pull ai/phi4            # ~9GB, slower, stronger reasoning
```

Verify it's running:

```bash
curl http://localhost:12434/engines/llama.cpp/v1/models
```

---

## Running Victoria

### Web chat UI (JARVIS-style HUD)

```bash
uvicorn victoria.main:app --reload
```

Open **http://localhost:8000** in your browser.

The interface is a full-screen dark HUD inspired by the Iron Man JARVIS OS — dark navy background, teal panel headers, red digit clock, and a rotating arc reactor ring as the chat backdrop.

**Left sidebar**
- Operator profile — name, communication style, topic count, preference count
- Memory banks — live list of all stored explicit memories
- Session log — today's and total session counts with a fill bar

**Center**
- Rotating V.I.C.T.O.R.I.A. arc reactor ring (glows and pulses while thinking)
- Chat messages overlaid on the ring — `OPERATOR` / `VICTORIA` labels with `HH:MM:SS` timestamps
- Each response gets a backend badge: `[DOCKER]`, `[OLLAMA]`, or `[CLAUDE]`

**Right sidebar**
- System status — active backend, tool count, memory status, API health
- Backend selector (Auto / Docker Model Runner / Ollama / Claude)
- Active tools list
- Last response status

**Talk to her (voice in the browser)**

The composer has two extra controls next to **Send**:
- 🎙 **Mic** — click to start recording, click again to stop. Your speech is transcribed (Whisper) and sent as a message; replies to voice input are spoken back automatically.
- 🔊 **Speaker** — toggle to have *every* reply read aloud, not just answers to voice input.

Voice runs through two endpoints — `POST /v1/transcribe` (audio → text) and `POST /v1/tts` (text → audio) — using the same Whisper + Piper/ElevenLabs stack as the terminal voice interface. It needs the **Piper voice model** downloaded (see [Voice interface](#voice-interface) below) and `python-multipart` (in `requirements.txt`). The mic uses your browser's microphone, so grant permission when prompted; switch to a natural cloud voice by setting `TTS_ENGINE=elevenlabs` (see config).

### Terminal chat

```bash
python scripts/chat.py
```

Type `claude`, `ollama`, or `docker` during a session to switch backends on the fly. Type `quit` to exit.

### Telegram bot

**One-time setup:**
1. Message `@BotFather` on Telegram → `/newbot`
2. Follow the prompts, copy the token
3. Add to `.env`: `TELEGRAM_BOT_TOKEN=your_token_here`

```bash
python scripts/run_telegram.py
```

**Telegram commands:**

| Command | What it does |
|---------|-------------|
| `/start` | Wake Victoria up |
| `/new` | Start a fresh conversation |
| `/remember <text>` | Store a persistent memory |
| `/forget <text>` | Remove a memory (exact match) |
| `/profile` | See everything Victoria knows about you |
| `/backend docker\|ollama\|claude` | Switch AI brain for this session |
| `/help` | Full command list |

Voice notes are transcribed automatically via Whisper.

### Voice interface

> Requires PortAudio (`brew install portaudio`) and the Piper voice model (see below).

**Download the Piper voice model (~65 MB):**

```bash
mkdir -p models && cd models
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx.json
cd ..
```

**Start the voice interface:**

```bash
python scripts/run_voice.py
```

Say **"Hello Victoria"** to activate. She'll respond with voice. The session stays open for 30 seconds of silence before returning to wake-word mode. Say "goodbye" to end the session immediately.

If a microphone isn't detected, it falls back to press-Enter mode automatically.

### Docker Compose (API + Telegram together)

```bash
docker compose up --build
```

This starts:
- `victoria-api` on port 8000 (web UI + REST API)
- `victoria-telegram` (Telegram bot)

Both containers connect to Docker Model Runner on the host via `model-runner.docker.internal`.

---

## Configuration reference

All settings are in `.env` (copy from `.env.example`).

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_LLM` | `docker` | Primary backend: `docker`, `ollama`, or `claude` |
| `MODEL_RUNNER_URL` | `http://localhost:12434/engines/llama.cpp/v1` | Docker Model Runner endpoint |
| `MODEL_RUNNER_MODEL` | `ai/llama3.2` | Model to use with Docker Model Runner |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint (if using Ollama) |
| `OLLAMA_MODEL` | `llama3.1` | Ollama model name |
| `ANTHROPIC_API_KEY` | _(empty)_ | Anthropic key for Claude fallback |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude model ID |
| `COMPLEX_QUERY_THRESHOLD` | `200` | Word count above which queries escalate to Claude |
| `ESCALATION_ENABLED` | `true` | Try local first; ask before escalating to Claude when it can't answer |
| `CLAUDE_CLI_COMMAND` | `claude` | Claude Code CLI binary used for escalation (uses your subscription) |
| `CLAUDE_CLI_MODEL` | `sonnet` | Model alias/id for the Claude Code CLI |
| `CLAUDE_CLI_TIMEOUT` | `120` | Seconds before a CLI escalation call is aborted |
| `CLAUDE_CLI_ALLOWED_TOOLS` | `WebSearch WebFetch` | Read-only tools Claude may use non-interactively when answering |
| `TTS_ENGINE` | `piper` | TTS backend: `piper` (free) or `elevenlabs` (paid) |
| `ELEVENLABS_API_KEY` | _(empty)_ | ElevenLabs key (only if `TTS_ENGINE=elevenlabs`) |
| `WAKE_WORD` | `hello victoria` | Voice activation phrase |
| `VOICE_SESSION_TIMEOUT` | `30` | Seconds of silence before returning to wake-word mode |
| `TELEGRAM_BOT_TOKEN` | _(empty)_ | Token from @BotFather |
| `DB_PATH` | `data/victoria.db` | SQLite database (conversation history + user profiles) |
| `CHROMADB_PATH` | `data/chromadb` | ChromaDB directory (semantic memory) |

---

## How Victoria learns your style

Victoria builds a persistent profile per user that gets injected into her system prompt before every response.

**Explicit memories** — stored instantly via regex, zero latency:
```
"remember that I prefer bullet points"
"note that I'm based in Alabama"
"don't forget I work primarily in Python"
"fyi, I like concise answers"
```

**Implicit style learning** — runs silently in the background every 5 turns:
After each 5th message, Victoria analyses the conversation with the LLM and extracts style signals — preferred response length, topics you keep raising, communication patterns. This fires as a background task and never delays your response.

**Profile in the system prompt** (example):
```
About this user:
The user's name is Mark.
Communication style: direct and technical.
Response preferences:
- prefers bullet points
- wants code examples when relevant
Topics they care about: Python, AI, software architecture.
Things to remember:
- based in Saraland, Alabama
- prefers dark mode UIs
```

Use `/profile` in Telegram or `GET /v1/profile/{user_id}` via the API to inspect your profile at any time. The web HUD shows your profile, memories, and session stats live in the left sidebar.

---

## REST API

The API runs on port 8000 when you start the app with `uvicorn`.

```bash
# Chat (blocking)
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the weather in London?", "user_id": "mark"}'

# Chat (streaming — Server-Sent Events)
curl -X POST http://localhost:8000/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me about Python generators", "user_id": "mark"}'

# Force a specific backend for one request
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Explain quantum entanglement", "user_id": "mark", "backend": "claude"}'

# Conversation history for a session
curl http://localhost:8000/v1/sessions/mark

# User profile (preferences, memories, style)
curl http://localhost:8000/v1/profile/mark

# Speech-to-text — transcribe an audio clip
curl -X POST http://localhost:8000/v1/transcribe -F "audio=@clip.webm"

# Text-to-speech — synthesize speech (saves audio to a file)
curl -X POST http://localhost:8000/v1/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Good evening, Mark."}' --output victoria.wav

# Health check (includes tool count + memory status)
curl http://localhost:8000/health
```

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| API framework | FastAPI + Uvicorn |
| Local LLM | Docker Model Runner / Ollama |
| Cloud LLM | Anthropic Claude (Sonnet 4.6) |
| Speech-to-text | faster-whisper (local) |
| Text-to-speech | Piper (local) / ElevenLabs (cloud) |
| Wake word | openWakeWord |
| Telegram | python-telegram-bot |
| Session memory | SQLite |
| Semantic memory | ChromaDB (embedded) |
| User profiles | SQLite |
| Web search tool | DuckDuckGo (no API key) |
| Containerisation | Docker Compose |

---

## Troubleshooting

**Chat returns `502` / logs show `model not found` (404 from the Model Runner)**
`MODEL_RUNNER_MODEL` must match an id from `docker model ls` *exactly*, including
any tag. Pulling `ai/llama3.2` can resolve to a tagged id such as
`ai/llama3.2:3B-Q4_K_M` — in that case set `MODEL_RUNNER_MODEL` to the full id.

```bash
docker model ls                                            # see the exact id
curl http://localhost:12434/engines/llama.cpp/v1/models    # or query the runner
```

**`pip install` fails with `In --require-hashes mode, all requirements must have their versions pinned`**
Your environment (a `pip.conf` or `PIP_REQUIRE_HASHES` env var) enforces hashed,
fully-pinned installs, which `requirements.txt` does not provide. For a one-off
install without changing your global config:

```bash
PIP_REQUIRE_HASHES=false pip install -r requirements.txt
```

---

## Running the tests

```bash
python3 -m pytest tests/ -v
```

83 tests across memory, conversation, tools, voice, Telegram, user profiles, API, and profile integration layers.
