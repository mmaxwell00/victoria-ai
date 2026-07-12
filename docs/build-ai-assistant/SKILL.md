---
name: build-ai-assistant
description: >-
  Playbook for building or extending a personal, local-first AI assistant — a
  JARVIS-style agent with a local-LLM brain, opt-in cloud escalation, layered
  memory, voice (STT/TTS), learnable skills, tools/MCP, and an encrypted secrets
  vault. Use whenever the user wants to build, scaffold, architect, plan, or add
  a capability to a personal AI assistant, agent, copilot, companion, or
  "Jarvis"-style app — e.g. "build me an assistant", "scaffold a local LLM
  chatbot with cloud fallback", "add voice / memory / skills / tools / a vault
  to my bot", or "give my assistant a personality" — even if no framework is
  named. Carries a reference architecture, a phased build order, provider
  trade-offs, privacy/guardrail invariants, and hard-won gotchas. Not for:
  reviewing code or PRs, slide decks, LinkedIn or marketing copy, fine-tuning or
  training a model, debugging an existing model server, or a standalone
  RAG/search pipeline with no assistant.
---

# Build an AI Assistant

A reference playbook for building a **personal, local-first AI assistant** — the
kind that runs on the user's own machine, costs nothing by default, remembers
them across conversations, talks and listens, uses tools, and only reaches the
cloud when explicitly allowed. It generalizes a full build that shipped
end-to-end; the choices here are proven, not theoretical.

Treat this as a map, not a mandate. Adapt the stack to the user's platform and
taste — but keep the **principles** and heed the **gotchas**, because those are
where builds actually go wrong.

## Reference implementation

This playbook is distilled from a real, shipped assistant — **Victoria**
(https://github.com/mmaxwell00/victoria-ai). When a decision is unclear or you
want to see a working version of any layer below, read
`references/victoria-reference.md`, which maps every layer and build phase to its
exact path in that repo. It's the worked example behind everything here.

## What you're building

A single app with three swappable layers behind one orchestrator:

```
        Interfaces:  web chat UI  ·  voice  ·  chat platform  ·  terminal
                                   │
                        ┌──────────▼───────────┐
                        │   Conversation core   │  persona · memory inject ·
                        │   (the orchestrator)  │  tool routing · escalation
                        └──────────┬───────────┘
             ┌────────────────────┼─────────────────────┐
      ┌──────▼──────┐     ┌────────▼────────┐    ┌────────▼────────┐
      │  LLM router │     │  Voice (TTS/STT)│    │ Tools · Skills  │
      │ local↔cloud │     │                 │    │ · MCP · Vault   │
      └─────────────┘     └─────────────────┘    └─────────────────┘
             │
   Memory (always on):  session history  +  semantic recall  +  user profile
```

Each layer is **provider-based and env-selected** — you can swap the local model,
the voice engine, or the tool set without touching the orchestrator.

## Core principles (read before writing code)

These are the load-bearing ideas. Everything else is detail.

1. **Local-first, privacy-first.** Default to a local model and local voice so the
   assistant works fully offline and nothing leaves the machine. Going to the
   cloud is an **explicit, per-turn, opt-in** action — never silent. This is both
   a privacy stance and a cost stance, and it shapes the whole architecture.

2. **Layered, swappable providers.** Decouple *what it says* (LLM), *how it
   sounds* (voice), *what it can do* (tools/skills/MCP), and *what it appears as*
   (UI/avatar). Select each via typed, env-driven config and a small registry.
   New capabilities become drop-ins, not rewrites.

3. **Escalate by self-signal, not by classifier.** Give the local model an
   "escalation protocol" in its system prompt: when it genuinely can't answer
   (real-time info, out-of-knowledge, a backend error), it emits a hidden
   sentinel token (e.g. `[ESCALATE]`). The orchestrator intercepts it and *asks
   the user* before calling the cloud. This is simpler and more honest than
   training a router, and it keeps the human in control.

4. **Small local models are not big models.** They are unreliable at
   function/tool-calling and get lost among many tools. Prefer **injecting
   instructions into the prompt** and **parsing structured fenced blocks** over
   relying on tool-calling. Keep the exposed tool set small. Pick a model that
   follows instructions well (a 7B that obeys beats a 3B that improvises), and
   match model size to available RAM.

5. **Treat all external instructions as untrusted.** Imported skills, MCP servers,
   fetched web content — anything that lands in the prompt can carry
   *instruction injection*. Gate additions behind **review-before-add**, and make
   tools respect **read-only / allowlist** guardrails. The safeguard is that
   skills are instructions-only (never executed code) and the user approves them.

6. **Secrets are write-only from the model's side.** The assistant can *use* a
   credential without ever *seeing* it: store encrypted, keep the master key in
   the OS keychain, and resolve `${vault:NAME}` only at the transport edge — never
   in the prompt, tool args, results, or logs.

7. **Memory is layered and always on.** Session history (recent turns) + semantic
   recall (vector search over all past conversations) + a persistent user profile
   injected into every system prompt. Together they make it feel like it *knows*
   the user.

8. **Ship one capability at a time, verified.** Each feature: its own branch,
   real tests, and an end-to-end check that drives the actual flow (not just unit
   tests). Open a PR for human review; don't self-merge. This keeps a large build
   controllable.

## Reference architecture

- **App shell**: an async web framework (FastAPI/uvicorn is a great default) with
  a versioned API prefix, a `/health` endpoint, and static assets for the UI.
- **Config**: one typed settings object (e.g. pydantic `Settings`) with
  `Literal`-typed provider knobs (`default_llm`, `tts_engine`, `avatar_provider`,
  …) read from env. This is how every layer stays swappable.
- **Conversation core**: builds the system prompt (persona + profile + relevant
  memories + relevant skills), routes to a backend, handles the escalation
  dialogue, and persists the turn.
- **LLM router**: one interface, multiple backends — a local runtime and a cloud
  fallback (see choices below).
- **Memory**: a small SQLite DB for sessions/messages/profile; a vector store for
  semantic recall.
- **Interfaces**: a web chat UI is the primary surface; a chat-platform bot and a
  terminal client are cheap add-ons that reuse the same core.

## Phased build sequence

Build in this order — each phase yields a working assistant that the next phase
enriches. "Done when" gives you the exit criterion.

- **Phase 0 — Foundation.** App shell + typed config + `/health` + a static UI
  shell + session memory (SQLite). *Done when:* the server starts, the page
  loads, and turns persist to the DB.
- **Phase 1 — Local brain.** LLM router with a local backend; persona system
  prompt; chat + streaming endpoints. *Done when:* you can hold a streamed
  conversation fully offline.
- **Phase 2 — Memory & identity.** Semantic recall (vector store) + a persistent
  user profile injected into the prompt + a first-run onboarding step (name, how
  to be addressed). *Done when:* it recalls facts across sessions and greets the
  user by preference.
- **Phase 3 — Cloud escalation.** Add a cloud backend; the self-signal sentinel;
  the ask-before-escalate dialogue; robust interception. *Done when:* the local
  model hands off only on your "yes," and the sentinel never leaks to the user.
- **Phase 4 — Voice.** STT endpoint (speech→text) + TTS endpoint (text→speech) +
  mic/speaker controls in the UI; optional wake word. *Done when:* you can speak
  a question and hear the answer.
- **Phase 5 — Skills.** Instruction-only skill store (Markdown + frontmatter);
  inject an index every turn plus full instructions for relevant skills;
  create (draft→confirm→save) and import-with-review. *Done when:* it applies a
  saved skill and can safely create/import new ones.
- **Phase 6 — Tools & MCP.** A small built-in tool registry (search, weather,
  date, math…) + an MCP client (stdio + remote) with read-only/allowlist
  guardrails. *Done when:* it uses external tools within the guardrails.
- **Phase 7 — Secrets vault.** Encrypted store, keychain-held key, write-only
  API, transport-edge `${vault:NAME}` resolution. *Done when:* it can auth to a
  service whose credential it can never reveal.
- **Phase 8 — Ops & polish.** One-command install, a native updater, a
  start/self-heal script, a health check, and a runtime model selector.
  *Done when:* a fresh machine gets to a running assistant in one command, and a
  reboot recovers cleanly.

## Provider & tech choices (proven defaults, with alternatives)

Pick per the user's platform; these are the ones that worked.

- **Local LLM runtime:** Docker Model Runner (llama.cpp engine, host TCP) or
  Ollama. Both expose an OpenAI-compatible endpoint. *Alt:* llama.cpp/LM Studio.
- **Cloud fallback:** a subscription-auth CLI (e.g. the Claude Code CLI, so no API
  key is needed) or a plain API key. Route through the same router interface.
- **TTS:** Piper (fast, local, free) as default; a cloud voice (e.g. ElevenLabs)
  as an optional, higher-quality opt-in.
- **STT:** faster-whisper (needs `ffmpeg`). Do speech capture in the browser and
  post audio to a transcribe endpoint — avoids native audio deps on the server.
- **Web access (for real-time info):** a built-in search tool (e.g. DuckDuckGo,
  no key) plus a lightweight page-reader MCP (`mcp-server-fetch`) gives you
  *search → read*, which answers most "what's happening now" questions without
  escalating to the cloud. Prefer this over a full headless browser for a local
  model — one `fetch` tool won't drown it, whereas a Playwright/Chrome MCP adds
  ~20 tools and is better reserved for the cloud backend. (If `uvx` isn't
  available, `pip install mcp-server-fetch` into the app venv and run it as
  `python -m mcp_server_fetch`.)
- **Vector store:** ChromaDB (embedded, no server) for semantic memory.
- **Secrets:** Fernet symmetric encryption; master key in the OS keychain
  (macOS Keychain via the `security` CLI), with an env/`0600`-file fallback.
- **UI:** a single static HTML/CSS/JS page talking to the API over
  fetch + Server-Sent Events for streaming. A strong visual theme (e.g. a
  "HUD") makes it feel like a product, not a demo.

## Privacy & guardrail invariants

Hold these true regardless of stack:

- The **default path transmits nothing off the machine.** Every cloud edge is
  opt-in or triggered by an explicit action, and the UI should make outbound
  calls visible.
- **Bind the API to loopback** if it has no auth; never expose an unauthenticated
  assistant on the LAN.
- **Secrets never cross into** the prompt, tool arguments, tool results, logs, or
  the API surface. The vault is one-way.
- **External instructions are reviewed before they're trusted**, and tools honor
  read-only/allowlist limits. MCP tools run under the user's credentials — only
  add servers the user trusts.

## Hard-won gotchas (these will bite you)

- **Streaming leaks control tokens.** If you stream tokens straight through, the
  escalation sentinel (and any "thinking" prose) can flash to the user. Buffer
  the stream and filter/route control signals before display.
- **Don't lean on tool-calling with small models.** They drop or malform tool
  calls. Use injected instructions + a parseable fenced block for structured
  actions (like creating a skill), and keep the tool list short.
- **Tool-aware escalation, or it cannibalizes your tools.** If the model has both
  tools *and* a "signal when you can't answer" escalation protocol, a naive
  escalation prompt makes it escalate for exactly the live questions its own
  tools handle — e.g. listing "weather / news / prices" as *escalate-now*
  examples means it emits the escalation token instead of calling `get_weather`
  or `web_search`, and the user hears "I can't access real-time data" even though
  it can. Order it explicitly: **try the relevant tool first → answer from what
  you know → escalate only as a last resort**, and never list tool-handled
  queries as escalation examples.
- **Web-search scraping libraries churn — and fail *silently*.** DuckDuckGo-style
  search packages rename and break often (e.g. `duckduckgo_search` → `ddgs`); the
  stale one returns *zero results* rather than raising, so search looks "working"
  while every query comes back empty and the model says "I couldn't find
  anything." Pin the maintained package, and add a smoke check ("does a known
  query return ≥1 result?") so a dead search fails loudly.
- **Match the exact model id.** Local runtimes resolve tags — pulling `foo/bar`
  may become `foo/bar:3B-Q4_K_M`. Use the id the runtime actually lists, or you'll
  get silent 404s.
- **A polluted launch env hijacks the cloud CLI's auth.** If escalation shells out
  to a subscription-auth CLI, variables inherited from the shell that started the
  server — a gateway `*_BASE_URL`, or session markers like `CLAUDECODE` — can
  override the machine's real login and the CLI 401s. Invoke the CLI with a
  *scrubbed* environment (drop the base-URL / auth-token / session vars). The most
  robust fix is to authenticate with an **explicit long-lived token** (e.g.
  `claude setup-token`) injected into the subprocess env, so auth doesn't depend
  on the launch context at all. And **read the CLI's stdout for errors** — many
  CLIs (Claude Code included) print auth failures to stdout, not stderr, so a
  stderr-only error handler reports a useless "exit 1, no output" instead of the
  real `401`. Symptom: escalation returns `401 Invalid authentication credentials`.
- **Real WAV headers.** Some TTS calls return raw PCM or headerless audio; use the
  library's proper WAV writer or the browser can't play it.
- **Hermetic/hardened Python envs** may enforce hashed, fully-pinned installs.
  Provide an override (e.g. `PIP_REQUIRE_HASHES=false`) in your install/update
  scripts so setup doesn't fail on locked-down machines.
- **Local runtime host ports drop on reboot.** A container-based model runner can
  lose its host TCP binding after a Docker/OS restart — the model list goes empty
  though the models exist. Add a self-heal step (disable→re-enable the runner, or
  re-bind the port) to your start script.
- **Empty collections can be falsy.** If a registry/collection defines `__len__`,
  an empty one is "falsy" — `x or default` will wrongly replace it. Use
  `x if x is not None else default`.
- **Guardrail verb lists need hardening.** A read-only filter that only blocks
  create/update/delete misses `push`, `fork`, `merge`, etc. Enumerate write verbs
  deliberately, and don't over-block reads (`get_*`, `search_*`).
- **Migrate the DB in place.** When you add profile/session columns later, guard
  with `PRAGMA table_info` and `ALTER TABLE ADD COLUMN` so existing users' data
  survives an upgrade.
- **The history window must start on a user message** for most chat APIs — trim
  leading assistant turns when you slice recent history.

## Working discipline

- **One feature per branch → test → verify end-to-end → PR for review.** Drive the
  real flow to verify (send a chat, click the button, screenshot the UI), not just
  unit tests. Let the human merge.
- **Make it operable:** one-command install, a one-command updater, a launcher
  that self-heals dependencies and health-checks the server. A great assistant
  that's fiddly to start won't get used.
- **Preflight optional auth, don't block on it.** At startup, check whether
  optional external auth (e.g. the cloud-escalation login) is present and *tell
  the user how to set it up if it's missing* — but still start, because
  local-first must work without it. Report the status ("escalation: ready ✓")
  rather than failing silently the first time they need it.
- **Persona is a feature.** A consistent voice/name/tone in the system prompt is
  what turns "an LLM endpoint" into "an assistant."

## When the user is extending, not starting

If the assistant already exists, skip to the relevant phase. Map the request to a
layer (brain / memory / voice / skills / tools / vault / ops), respect the
existing provider seams and config knobs, and keep the one-feature-per-PR
discipline. Reach for the gotchas list first — most "it broke after I added X"
reports are on it.
