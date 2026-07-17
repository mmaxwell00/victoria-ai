# Reference implementation — Victoria

`build-ai-assistant` is distilled from a real, shipped assistant. When a decision
is unclear, look at how Victoria actually did it.

- **Repo:** https://github.com/mmaxwell00/victoria-ai
- **Stack:** FastAPI + uvicorn, static HTML/CSS/JS HUD, SQLite + ChromaDB,
  Docker Model Runner / Ollama (local) + Claude Code CLI (cloud escalation),
  Piper + faster-whisper (voice), Fernet + macOS Keychain (vault). Runs native
  (uvicorn in a venv) or containerized (docker-compose).
- **Scale as of this writing:** ~33 Python modules, 331 tests (22 files),
  3 shipped skills.
- **Reliable local tool-use lives in two places:** `llm_router._docker_with_tools`
  (forced-tool retry via `tool_choice="required"` + the `_looks_like_tool_refusal`
  detector) and `conversation._history_for_model` (strips stale refusals from the
  replayed context so they don't prime the model). See the "Hard-won gotchas" in
  SKILL.md for why.

## Layer → path map

Use this to see a working version of each layer the playbook describes.

| Playbook layer | Where it lives in the repo |
|---|---|
| App entrypoint | `victoria/main.py` |
| Typed, env-driven config (provider knobs) | `victoria/config.py` — pydantic `Settings`, `Literal` types (`default_llm`, `tts_engine`, …) |
| Conversation core (orchestrator) | `victoria/core/conversation.py` — builds the prompt, routes, runs the escalation dialogue, persists turns |
| LLM router (local ↔ cloud) | `victoria/core/llm_router.py` |
| Memory — session history (SQLite) | `victoria/core/memory.py` |
| Memory — semantic recall (ChromaDB) | `victoria/core/semantic_memory.py` |
| Memory — user profile + learning | `victoria/core/user_profile.py`, `victoria/core/profile_extractor.py` |
| STT (Whisper) | `victoria/core/transcription.py` |
| Voice loop / capture / wake word | `victoria/voice/{conversation,audio,wake_word}.py` |
| TTS engines (swappable) | `victoria/voice/tts/{base,factory,piper_tts,elevenlabs_tts}.py` |
| Skills (store + GitHub import) | `victoria/skills/{store,importer}.py`; shipped skills in `skills/*.md` |
| Tools + registry | `victoria/tools/{registry,web_search,weather,datetime_tool,calculator,skills_tools}.py` |
| MCP client + guardrails | `victoria/mcp/manager.py` |
| Secrets vault | `victoria/vault/store.py` |
| Interfaces — REST API (`/v1`) | `victoria/interfaces/api.py` |
| Interfaces — chat platform bot | `victoria/interfaces/telegram_bot.py` |
| Interfaces — web HUD | `victoria/static/{index.html,app.js,style.css}` |
| HUD dashboard — weather / stocks / news + conversational tracking | `victoria/dashboard/{store,feeds}.py`, `victoria/tools/dashboard_tools.py` (dashboard row in `victoria/static/`) |
| Knowledge bases — Obsidian vaults (read / search / write notes) | `victoria/knowledge/vaults.py`, `victoria/tools/knowledge_tools.py` — path-safe native file access over Markdown vaults |
| Ops — install / update / self-heal / launch | `setup-victoria-mac.sh`, `scripts/{update,ensure-model-runner,start}.sh` |
| Deployment | `Dockerfile`, `docker-compose.yml` (native or containerized) |
| Docs — README, arch diagram, decisions | `README.md`, `docs/architecture.svg`, `docs/decisions-md.md` |
| Tests | `tests/` (per-feature: escalation, voice, skills, MCP, vault, profile, api, …) |

## API surface (the shape to aim for)

All under a `/v1` prefix, plus a top-level `/health`:

- `POST /chat`, `POST /chat/stream` — turn + streamed turn
- `GET /sessions/{user}`, `GET /sessions/{user}/{session}/history` — chat history
- `GET /profile/{user}`, `POST /profile/{user}/onboard` — profile + first-run
- `POST /transcribe`, `POST /tts` — voice in/out
- `GET /vault`, `POST /vault`, `DELETE /vault/{name}` — write-only secrets
- `GET /models`, `POST /models/select` — runtime local-model switch
- `GET /dashboard/{config,weather,stocks,news}` — HUD dashboard data (tracked cities / stocks / headlines; items are managed conversationally via the `track_dashboard` / `untrack_dashboard` tools)
- `GET /knowledge/vaults` — configured Obsidian vaults + note counts (notes are read / searched / written via the `search_notes` / `read_note` / `list_notes` / `write_note` tools)

## Phase → repo mapping

Each playbook phase has a concrete landing spot, which is a good way to read the
repo in build order:

- Phase 0 Foundation → `main.py`, `config.py`, `core/memory.py`, `static/`
- Phase 1 Local brain → `core/llm_router.py`, `core/conversation.py`
- Phase 2 Memory/identity → `core/semantic_memory.py`, `core/user_profile.py`, onboard endpoint
- Phase 3 Escalation → `llm_router.py` (cloud backend) + `conversation.py` (sentinel + ask-first)
- Phase 4 Voice → `core/transcription.py`, `voice/`, `/transcribe` + `/tts`
- Phase 5 Skills → `skills/store.py`, `skills/importer.py`
- Phase 6 Tools/MCP → `tools/`, `mcp/manager.py`
- Phase 7 Vault → `vault/store.py` + `${vault:NAME}` resolution in `mcp/manager.py`
- Phase 8 Ops → `setup-victoria-mac.sh`, `scripts/`

> Verified against the repo at commit `b43a8b8`. The layout is the pattern to
> emulate, not to copy verbatim — adapt module names and the stack to the target.
