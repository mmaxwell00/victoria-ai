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
