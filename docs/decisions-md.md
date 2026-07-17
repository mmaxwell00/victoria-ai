# Decisions Log

> Running record of architecture and product decisions for Victoria.
> Newest entries at the top. Append; don't rewrite history.
>
> Format: lightweight ADR (Architecture Decision Record).
> Each entry: **Decision** · **Status** · **Context** · **Choice** · **Why** · **Trade-offs**.

---

## Open Questions

Items awaiting decision before implementation can proceed.

### Q1 · Video generation MCP provider

**Context:** Adding video creation to Victoria's MCP toolkit. Three viable paths in 2026.

**Options:**
- **Runway MCP** — official server, fronts Gen-4.5, Gen-4 Turbo, Aleph, Act-Two, Veo 3/3.1 under one OAuth. Broadest single-server coverage.
- **Higgsfield MCP** — 30+ models plus Soul ID character consistency. More variety, more setup, more configuration surface.
- **Defer** — skip video for v1, add later once Gmail/GitHub/RAG are landed and proven.

**Sora is explicitly ruled out** — OpenAI discontinuing web/app April 26 2026 and API September 24 2026. Not safe to build on.

**Awaiting:** Alex's call.

---

### Q2 · Gmail MCP scope

**Context:** Alex wants Gmail integration. Two scopes available.

**Options:**
- **`workspace-mcp` (Taylor Wilsdon)** — covers Gmail + Drive + Calendar + Docs + Sheets + Slides + Forms + Chat + Tasks + Contacts. Native OAuth 2.1.
- **Gmail-only servers** (GongRzhe, jeremyjordan, ajbr0wn) — narrower surface, simpler.

**Recommendation:** `workspace-mcp`. Same OAuth ceremony, eight bonus integrations for free. Calendar alone justifies it.

**Awaiting:** Alex's confirmation.

---

### Q3 · GitHub MCP transport

**Context:** Official `github/github-mcp-server` offers two flavors.

**Options:**
- **Remote hosted** at `https://api.githubcopilot.com/mcp/` — OAuth, auto-updates, no local infra.
- **Local Docker** — Personal Access Token, container to manage, full control.

**Recommendation:** Remote. Less infrastructure, same toolset coverage.

**Awaiting:** Alex's call.

---

### Q4 · RAG embedding model

**Context:** Document embeddings for the RAG document collection in ChromaDB.

**Options:**
- **Local** — `sentence-transformers/all-MiniLM-L6-v2` (or similar). Free, private, runs on CPU acceptably.
- **OpenAI** — `text-embedding-3-small`. Higher quality on benchmarks. Costs $0.02 per million tokens. Requires API key.

**Recommendation:** Local. Quality difference is measurable but small for personal-document use cases. Aligns with Victoria's privacy-first defaults.

**Awaiting:** Alex's call.

---

### Q5 · RAG document sources

**Context:** Where do documents come from?

**Options:**
- **Folder drop only** — Alex drops files in `data/documents/`, runs `python scripts/ingest.py`. Simple, explicit.
- **Folder drop + Google Drive auto-sync** — once `workspace-mcp` is wired, Victoria can pull docs from Drive directly. More work, more magic, harder to debug.

**Recommendation:** Folder drop for v1. Add Drive auto-sync only after the core RAG pipeline is solid.

**Awaiting:** Alex's call.

---

## Decided

### 2026-07-17 · Obsidian knowledge bases: three vaults Victoria reads/searches/writes

**Status:** Phase 1a implemented (this PR — native file access + tools + tests).
RAG (1b), AI-vault-as-memory (2), and Obsidian REST/MCP (3) are sequenced next.

**Context:** Alex wants Victoria's knowledge to live in **Obsidian**, across three
vaults — **Docker** (work), **Personal**, **AI** (Victoria's own) — synced across
machines via Obsidian Sync (a paid upgrade he's taking). This also feeds the
long-planned RAG work (see Open Q4/Q5).

**Choice (three forks, Alex's calls):**
- **Access = Both.** Native, path-safe file access (an Obsidian vault is just a
  Markdown folder) is the substrate now; Obsidian's Local REST API + MCP layers
  live actions later (Phase 3). Phase 1a ships native only.
- **Memory model = AI vault becomes durable memory.** Victoria's learned facts /
  profile will persist as human-readable Markdown in the AI vault (Phase 2);
  SQLite keeps per-session history; ChromaDB becomes the *index over the vaults*
  rather than a separate semantic store. Mirrors the `~/.claude/memory/*.md`
  pattern Alex already uses.
- **Write policy = all three read-write.** `OBSIDIAN_WRITABLE` still enforces
  per-vault mode in code, so any vault can be locked read-only later.

Implemented as `victoria/knowledge/vaults.py` (`KnowledgeBase`, path-traversal
guarded, `.obsidian`/`.trash` reserved) + four tools (`search_notes`,
`read_note`, `list_notes`, `write_note`) + `GET /v1/knowledge/vaults`. Vault
paths are env-driven (`OBSIDIAN_*_PATH`); blank = disabled, so the feature ships
dormant until pointed at real folders.

**Why:** Markdown-on-disk makes Victoria's memory *inspectable and editable by
Alex in Obsidian* (trust + portability) instead of an opaque vector blob;
native file access has no dependency on Obsidian running and works headless;
keyword search ships value in 1a while RAG is built.

**Trade-offs:** Naming overlap with the **Credentials Vault** (secrets) — kept
distinct by calling these "knowledge bases." Keyword search is O(notes) per
query until RAG lands (fine at personal scale). External edits in Obsidian
won't be re-indexed until the Phase 4 file-watcher. "Victoria across computers"
(running the *server* on multiple machines) is a separate, deferred question —
Sync only solves knowledge portability.

---

### 2026-07-15 · Dashboard tracking via deterministic interception; installer fully interactive

**Status:** Implemented (PRs #50, #51).

**Context:** Two follow-ups after the dashboard shipped. (1) "Include Saraland
in the weather" changed nothing — qwen2.5 confidently replied "added!" (even
fabricating the weather) but never called `track_dashboard`; forcing the tool
made it escalate or return an empty completion. (2) The installer only asked
about escalation; the local model and voice were silent defaults / flag-only.

**Choice:** (1) Intercept dashboard commands in the conversation manager
(`_is_dashboard_command` + `_handle_dashboard_command`): detect add/remove
intent, have the local model only EXTRACT `{action, kind, value}` as JSON, then
mutate the store in code. (2) `setup-victoria-mac.sh` now prompts for model
(RAM-recommended), escalation token, and voice up front via `/dev/tty` (works
through `curl | bash`); each flag skips its prompt; no TTY → sensible defaults.

**Why:** Small local models won't reliably *call* a tool but will reliably
return *structured JSON* — so a must-happen mutation shouldn't ride on
stochastic tool-calling. And an installer that asks beats one that hides
choices behind flags the user has to know exist.

**Trade-offs:** The intent detector is a keyword heuristic — a missed phrasing
falls through to a normal turn (no harm, just no change). JSON extraction adds
one local-model call per dashboard command.

---

### 2026-07-15 · HUD dashboard row: four info boxes + conversational tracking

**Status:** Implemented (PR #46; layout tuned in #47/#48).

**Context:** Wanted an at-a-glance top strip in the HUD — weather, stocks,
headlines — that the operator manages by talking to Victoria.

**Choice:** A `dash-row` of four boxes above a shortened chat (WEATHER / MARKETS
/ HEADLINES / reserved). Data via free, no-key sources — wttr.in (weather,
24-hr local time + °F), Yahoo Finance v8 (stock price + name), CNN/Fox RSS
(headlines, open in a new tab). Tracked lists persist in `data/dashboard.json`
(`victoria/dashboard/store.py`); fetchers in `feeds.py` are independently
fault-tolerant. Tracking is conversational via `track_dashboard` /
`untrack_dashboard` tools; the LLM converts company names → tickers.

**Why:** No API keys keeps it local-first and zero-setup; per-source resilience
means one dead feed never blanks the row; tools reuse the existing registry so
"track Dallas" just works.

**Trade-offs:** Yahoo/wttr are unofficial endpoints (can rate-limit or change
shape → box shows a placeholder). wttr switches output by User-Agent (needs a
curl UA). Drudge Report has no feed, so it's unsupported. Sandbox egress must
allowlist the new hosts (done in `sbx-kit.yaml`).

---

### 2026-07-15 · Avatar: stylized SVG face, then a framed portrait image

**Status:** Implemented (PRs #43, #45).

**Context:** Wanted a visible "Victoria" presence in the sidebar that reacts to
state (idle / listening / thinking / speaking). Explored a real-time 3D head
(three.js + Ready Player Me, PR #44) but RPM shut down (Jan 2026) and true
photoreal-live-local isn't practical on a Mac.

**Choice:** Ship a lightweight avatar dock bottom-left whose look is swappable
behind a fixed state model. Landed on a **framed portrait image**
(`victoria-avatar.png`) with a state-coloured, voice-reactive glowing border
(teal idle · green listening · purple thinking · fuchsia speaking) — the exact
look with zero 3D/asset pipeline. The 3D test bench is preserved on a branch.

**Why:** The state model (`hfPhase` / `isStreaming` / TTS amplitude) is the
contract; the renderer (SVG → framed image → future 3D/Rive) can change without
re-plumbing. A framed image gives lifelike fidelity locally that in-browser 3D
can't match cheaply.

**Trade-offs:** No facial lip-sync (the frame glow carries the "life"). The
image is user-supplied (licensing is the operator's call).

---

### 2026-07-14 · Reliable local tool-use: stream-with-tools + forced-tool retry + history de-poisoning

**Status:** Implemented (PRs #39, #41).

**Context:** The local model intermittently declined tool-answerable questions
("I'm unable to fetch real-time weather data") even though `get_weather` /
`web_search` work. Three compounding causes: (1) the streaming chat path sent a
plain completion with **no tools**, while only the non-streaming path passed
them — and the HUD streams; (2) small instruct models are stochastic about
tool-calling and occasionally refuse even with tools present; (3) worst of all,
a long session replayed the model's **own past refusals** (from before tools
worked) back into context, priming it to keep refusing — so single-city asks
succeeded while harder multi-city asks failed.

**Choice:**
- Route the streaming local turn through the tool-aware `_local_answer`
  (it already buffers to detect `[ESCALATE]`, so no streaming UX is lost).
- In `_docker_with_tools`, if the model returns a tool-answerable refusal on its
  first turn without calling anything, retry once with `tool_choice="required"`.
  Guarded so a post-tool summary is never re-forced.
- Add `_history_for_model()` to strip refusal-shaped assistant turns (and the
  questions that prompted them) from the **replayed** context — stored history
  and the UI transcript are untouched.

**Why:** De-poisoning fixes the root cause (verified: poisoned session went 3/4
→ 6/6); the forced retry is a deterministic backstop for residual stochastic
refusals. Together they made the failing multi-city weather query reliable and
let long-lived sessions self-heal.

**Trade-offs:** The refusal detector is a regex heuristic (could miss a novel
phrasing or, rarely, strip a legitimately-worded "can't"); acceptable because
the cost of a false strip is only losing one stale turn of replayed context.
The local tool path is fully buffered (no token streaming), which was already
true for the escalation-enabled path.

---

### 2026-06-28 · MCP architecture: client-side integration into existing tool registry

**Status:** Accepted.

**Context:** Need to add Gmail, GitHub, and video MCP servers without rewriting Victoria's tool system.

**Decision:** Treat MCP as a client capability. Victoria connects to external MCP servers on startup, discovers their tools, and registers them into the existing `victoria/tools/registry.py` as if they were native tools. The LLM doesn't distinguish.

**Implementation outline:**
- New `victoria/core/mcp_client.py` — manages MCP server connections (stdio + streamable_http transports)
- New `victoria/tools/mcp_adapter.py` — wraps discovered MCP tools as registry entries
- New `config/mcp_servers.yaml` — declarative server config; add a server = add a YAML entry
- `victoria/tools/registry.py` — extended to accept both `@tool`-decorated functions and MCP-sourced tools

**Why:** Alex's existing tool routing in the conversation manager already works. Don't rebuild what's working. MCP becomes additive.

**Trade-offs:**
- Slightly more code than calling MCP servers per-request
- Cleaner LLM prompt surface (one unified tool list)
- Easier to add/remove servers without touching conversation logic

---

### 2026-06-28 · RAG architecture: separate ChromaDB collection alongside semantic memory

**Status:** Accepted.

**Context:** Adding document retrieval. Victoria already uses ChromaDB for cross-session conversation memory.

**Decision:** Add a second ChromaDB collection for documents, separate from the conversation semantic memory collection. Both get injected into the conversation manager's context-building step.

**Implementation outline:**
- New `victoria/rag/` module: `ingest.py`, `loaders/`, `chunker.py`, `store.py`, `retriever.py`
- New `scripts/ingest.py` CLI for one-shot ingestion: `python scripts/ingest.py path/to/folder`
- Loaders for PDF, markdown, DOCX, HTML, plaintext
- Chunking via recursive character splitting with overlap (LangChain-style algorithm, no LangChain dependency)
- Document search exposed as a registered tool so Victoria can invoke it explicitly when needed

**Why:** Same retrieval pattern Victoria already uses, applied to a new content type. Reuses existing ChromaDB infrastructure. Conversation manager already handles context injection — adding a second collection is a small extension, not a rewrite.

**Trade-offs:**
- Two collections to maintain (mitigated: same DB, same embedding model can be reused)
- Need to balance how much document context vs. semantic memory gets injected per turn (tune later)
- Document chunks may overlap topically with conversation memory; rely on retrieval relevance to deduplicate at query time

---

### 2026-06-28 · Cross-session continuity: `CLAUDE.md` + `docs/DECISIONS.md` pattern

**Status:** Accepted.

**Context:** Alex works across multiple MacBooks. Chat sessions don't share state across devices, but the repo does.

**Decision:** Use the repo as the synchronization mechanism. `CLAUDE.md` at repo root acts as the project bible. `docs/DECISIONS.md` (this file) is the running record. Any Claude session, on any machine, reads both and is current.

**Why:** Conversation state is ephemeral. Code and decisions are durable. Sync the durable layer; let the ephemeral layer be ephemeral.

**Trade-offs:**
- Requires discipline to update DECISIONS.md when decisions are made
- More upfront writing; saves significant re-explaining later
- The repo becomes self-documenting for any collaborator (human or AI)

---

## How to use this file

**When a new decision gets made during a session:**
1. Add an entry to the **Decided** section (newest at top).
2. Date it, give it a clear title.
3. Capture: context, decision, why, trade-offs.
4. Keep it short — ADR style, not essay style.

**When something in Open gets resolved:**
1. Move the entry from **Open Questions** to **Decided**.
2. Reformat as a full ADR entry.
3. Don't delete the option that wasn't chosen — note it as the rejected alternative.

**When a prior decision gets revisited:**
1. Add a new entry referencing the old one ("Supersedes: 2026-MM-DD entry").
2. Don't delete the old entry. The history is the value.

**When something is blocking or stuck:**
1. Add to Open Questions with what's missing.
2. Tag with who/what is blocking it.
