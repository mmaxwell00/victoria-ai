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

### 2026-07-30 · Sandbox uptime — a host-side launchd watchdog, not an in-VM supervisor

**Status:** Built + verified. `scripts/victoria-watchdog.sh`,
`scripts/setup-watchdog.sh`, `scripts/com.victoria.watchdog.plist.template`.

**Context:** `:8001` went dark twice in one session. Neither was an app crash: the
sbx kit's `startup` service fires **once at `sbx run`**, so when Docker recycles the
sandbox's container (idle / resume / pressure) it kills uvicorn *and* the kit's in-VM
`while true` supervisor — while `sbx ls` still reads `running`. The tell is a changed
in-VM client IP (`172.17.0.8` → `172.17.0.6`) with no crash in `/tmp/victoria.log`. A
recycle can also drop the `sbx ports` publish by itself. The same one-shot `startup`
means a **Mac reboot** leaves her down too: Docker Desktop auto-starts (`AutoStart:
true`), but `sbx` has no autostart/restart concept for a sandbox.

**Decision:** Recover from the **host**, not from inside the VM.
- A launchd agent (`com.victoria.watchdog`, `RunAtLoad` + `KeepAlive`) polls
  `/health` every 30s. `RunAtLoad` covers reboot; polling covers recycles.
- **Repair is always the cheap path, never a recreate** — a recycle kills processes
  but not the filesystem, so the uv py3.11 venv + deps survive. Two shapes, two fixes:
  app healthy inside → re-publish the host port only; app dead → relaunch the
  supervised uvicorn (`sbx exec` starts a stopped sandbox first, covering reboot).
- **A deleted sandbox is explicitly out of scope.** Rebuilding needs a kit pack +
  mounts; too heavy to fire unattended, so it stays `./deploy-sandbox.sh` and the
  watchdog just logs it.

**Rejected:** *strengthening the in-VM supervisor* — anything inside the VM dies with
the container, which is the actual failure. *A `KeepAlive`-only launchd job wrapping
uvicorn* — the process lives inside the sandbox, not on the host. *Auto-running
`deploy-sandbox.sh` on failure* — a ~15–20 min cold rebuild as an unattended reflex,
when the venv is nearly always intact.

**Verified:** killed uvicorn + supervisor → self-recovered in ~15s; unpublished the
port with the app alive → re-published in ~20s; both hands-off, then chat + the
knowledge base confirmed working.

**Gotcha worth remembering (cost two failed repairs):** inside
`sbx exec <sbx> -- sh -lc '<cmd>'` the wrapper shell's cmdline **contains `<cmd>`**,
so `pgrep -f "uvicorn victoria.main"` matches *itself* (always "alive") and `pkill -f`
makes that shell **SIGTERM itself** — it logged "relaunched" while launching nothing.
Use bracket patterns (`uvicorn[ ]victoria.main`), keep kill and launch in *separate*
exec calls (a combined one re-triggers it via the runner's own `victoria-run` path),
prefer an HTTP probe over process matching, and background with `setsid nohup`.

### 2026-07-27 · Claude bridge — one-command setup + `exec` transport (no nightly)

**Status:** Built. `scripts/setup-bridge.sh`, `scripts/com.victoria.claude-bridge.plist.template`,
bridge `exec` mode, deploy wiring, tests. Implements the 2026-07-24 design below.

**Context:** The approved host-bridge design assumed hop 2 (bridge → claude sandbox)
used **SSH**, which needs the **nightly `sbx`**. Mark wanted a "create once, minimal
ongoing effort" activation that works on the **stable `sbx`** he already runs.

**Decision:**
- **Add an `exec` transport to the bridge** (`CLAUDE_BRIDGE_MODE=exec|ssh`, default
  `exec`). Exec mode reaches the governed sandbox via `sbx exec -i victoria-claude --
  claude -p` (prompt on stdin, `-i` for docker-exec stdin semantics). No SSH, no
  nightly. `ssh` mode is retained for when the nightly is available (more robust under
  load). Security invariants are unchanged in both modes: only validated tokens
  (model/flags/regex-checked tools/Victoria's own system prompt) are argv; the
  untrusted prompt is fed on **stdin**, never as a shell arg.
- **One-command setup** — `./scripts/setup-bridge.sh` (idempotent): generates the mTLS
  certs, creates the persistent governed `victoria-claude` sandbox, installs the bridge
  as a **launchd** agent (`com.victoria.claude-bridge`, `RunAtLoad`+`KeepAlive` →
  auto-start on login, self-restart on exit), and writes `~/.victoria/bridge-env`.
- **Deploy auto-wiring** — `deploy-sandbox.sh` reads `~/.victoria/bridge-env`, stages
  the mTLS **client** identity under the repo mount (`$REPO_STAGE/.bridge/`, readable
  in-sandbox at the same abs path), and substitutes `CLAUDE_BRIDGE_*` into the kit.
  When the bridge is configured it bakes **no** Claude token into the VM.

**Why:** honours credential containment (only the mTLS *client* identity — not the
Claude token — ever enters Victoria's VM) while removing the nightly-`sbx` dependency
and reducing activation to one command + a one-time `sbx run --name victoria-claude` login.

**Trade-offs:** `exec` mode leans on `sbx exec` per escalation (historically wedge-prone
when an exec is *interrupted* — a single launchd-managed call per escalation is the low-risk
case; `ssh` mode remains the sturdier option under heavy/concurrent load). The staged
client cert/key sit on disk under `~/sandboxes/**` (gitignored via `.bridge/`).

---

### 2026-07-24 · Claude escalation in the sandbox — host-bridge design (approved)

**Status:** Design approved; build starting. Diagrams in `docs/`.

**Context:** Victoria's "Claude" backend shells out to the Claude Code CLI
(subscription auth). Inside the sandbox that fails — and we established why across
a long investigation:
- The sbx **proxy OAuth** (sentinel-swap) that authenticates Claude Code is wired
  to the **built-in `claude` agent only**. A custom kit that declares the full
  `oauth:` block gets the credential *file seeded* but the proxy never swaps the
  sentinel for a custom agent — verified empirically (both OAuth-file and API-key
  paths fail: "OAuth session expired" / "Invalid API key"). So the pure
  proxy/secret-engine path is **not achievable for Victoria's custom agent**.
- A `claude setup-token` **injected into the VM** (Path 2) works and is subscription
  auth, but the real token then **lives inside Victoria's sandbox** — Victoria can
  read it. Rejected on credential-containment grounds.

**Decision — host bridge.** Victoria delegates escalation to a governed **built-in
`claude`-agent sandbox** (where proxy auth works), reached via a small **host
bridge**:
- **Control (Level 1, already built):** Victoria's local model *suggests* escalation
  and only calls Claude on the user's explicit **yes** — no auto-calls.
- **Hop 1** (Victoria → bridge): **mTLS**, prompt only — no credentials cross.
- **Hop 2** (bridge → claude sandbox): **SSH** (`sbx` SSH feature — nightly build).
- **Credential containment:** the real subscription token stays **only** in sbx's
  host subsystem (macOS Keychain + proxy), refreshed on use; **neither sandbox ever
  holds it** (the claude sandbox sees a `proxy-managed` sentinel; Victoria sees
  nothing but prompt/response).

**Why:** subscription auth (not API billing), and the real credential is never
exposed to Victoria's VM — the strongest containment available given the proxy
limitation.

**Diagrams (this PR):**
- [`docs/claude-bridge-architecture.svg`](claude-bridge-architecture.svg) — the approved end-to-end design + review notes.
- [`docs/claude-escalation-host-bridge.svg`](claude-escalation-host-bridge.svg) — bridge credential flow.
- [`docs/claude-escalation-path2-keychain.svg`](claude-escalation-path2-keychain.svg) — the rejected Path-2 (token-in-VM) for contrast.

**Build scope (next PR):** host bridge (~50–100 LOC) + Victoria `CLAUDE_BRIDGE_URL`
path in `llm_router.claude_cli()` (backward-compatible) + a persistent claude-agent
sandbox. SSH/proxy wiring activates once the nightly `sbx` + claude sandbox are set up.

**Trade-offs:** more moving parts than Path 2; hop 2 depends on the nightly `sbx`
SSH feature; a long idle gap needs `sbx run --name victoria-claude` to refresh the stored OAuth.

---

### 2026-07-22 · Sandbox Phase 3 hardening — egress blocked by org policy; credential env-fallback

**Status:** Q3 (credentials) implemented + verified. Q2 (egress) written into the
kit but intentionally **inert** — sbx egress governance is org-wide/team-scoped,
not per-sandbox, so we chose to leave egress broad (decision C) rather than flip
all sandboxes to default-deny for one. This PR.

**Context:** Phase 3 aimed to (Q2) restrict the sandbox's outbound egress to an
allowlist and (Q3) resolve credentials from the sbx secret engine instead of
mounting an `.env`.

**Q2 — egress (blocked at the org tier).** Added the target allowlist as a
top-level `network.allowedDomains` block in `sbx/spec.yaml`. But this environment
is governed by org policy `NetworkAll` (`allow ** network`, applies to all
sandboxes, active), and Docker's model has an **active org rule override
kit-defined network rules**. Verified empirically: from inside the sandbox a
non-allowlisted host (`https://example.com`) still returns HTTP 200 (including in
the `sandbox:victoria` policy context). So the kit allowlist restricts nothing
today. Crucially, sbx network governance is scoped **by org / team (user
membership), not per sandbox**, and all these sandboxes run under one Docker
identity — so there is **no supported way to harden only Victoria**. Tightening
egress means editing the org-wide `NetworkAll` policy in Docker Home (affects every
sandbox) or a team-scoped policy under a separate identity. **Decision (C): leave
egress broad** — the sandbox already gives the hardware/process isolation we
wanted, and org-wide default-deny carries a blast radius across all sandboxes for
one assistant's benefit. The kit block stays as the ready target so a future
org-wide default-deny activates cleanly. Also noted: the kit does build-time
installs (apt/pip/uv/HF), so a strict runtime-only allowlist would break sandbox
creation — the durable fix for a tight posture is to bake deps into a custom base
image, then trim the allowlist.

**Q3 — credentials (done).** Extended `victoria/vault/store.py` `resolve()` so a
`${vault:NAME}` not in the encrypted store falls back to `os.environ.get(NAME)`
(vault still wins if both exist; a truly-missing name is still left intact).
Verified: the sbx proxy injects the `github` service secret as env var
`GH_TOKEN`, which now resolves through the fallback with no `mcp.json` change. The
`anthropic` secret is OAuth/proxy-edge (not a plain env var), so escalation auth is
handled at the proxy. +2 tests (337 total).

**Why:** keep the hardened config version-controlled and correct even though the
governing org must flip the switch; make proxy-injected creds resolve transparently
without weakening the vault's "values never returned to the model" rule (`resolve`
is transport-edge only).

**Trade-offs / deferred:** true egress lockdown depends on an org policy change
(out of repo scope) and, for a tight posture, a custom prebuilt image.

---

### 2026-07-20 · Docker Sandbox (sbx) deployment — verified Phase 1

**Status:** Phase 1 verified working end-to-end. Kit + docs land in this PR.

**Context:** Alex needs Victoria to run in a **Docker Sandbox (sbx)** for
hardware isolation. The repo's original `sbx-kit.yaml` was aspirational and never
actually ran.

**Choice / what works:** A packed sbx kit (`sbx/spec.yaml`, `kind: sandbox` on
`docker/sandbox-templates:shell-docker`) that installs core deps and runs uvicorn
as a **background startup service**. The host Model Runner is reached at
`host.docker.internal:12434`; the repo is staged under `~/sandboxes/**` and the
Obsidian vault mounted (per an org filesystem-allow rule) as the knowledge base;
the HUD is published to the host at `127.0.0.1:8001`. Verified live: chat (local
LLM), knowledge base (mounted vault), dashboard (weather/markets/metals/volume/
news), egress.

**Gotchas (full list in SANDBOX-DEPLOYMENT.md):** kits must be *packed*
(`sbx kit pack`), not raw YAML; the agent name must equal the kit name; Model
Runner is `host.docker.internal`, not `localhost`; a long-running service belongs
in `commands.startup` (`background: true`), not the entrypoint (which dies on
detach); the published service is **IPv4-only** (`127.0.0.1`, not `localhost`→`::1`);
mounts are **org-governed and case-sensitive** (`~/sandboxes/**`; the vault rule
must match the folder case, `~/Obsidian/**`); the sandbox FS is per-instance, so
deps are baked into the kit.

**Update (Phase 2 done):** the kit now installs the full dependency set on a
uv-managed **Python 3.11** venv (uv ships in the image; the install steps run as
`user: "1000"` so the venv is agent-executable). ChromaDB semantic memory is
**active** and the Whisper/Piper voice deps install. Verified live: `/health`,
chat, and `Semantic memory initialised at data/chromadb`. Native mic/wake-word
stays N/A (a headless sandbox has no audio device; `sounddevice` can't init
PortAudio) — browser voice is the path.

**Deferred (Phase 3):** tighten `network.allowedDomains` to an allowlist (Q2);
move Victoria's vault secrets to the `sbx secret` credential engine (Q3).

**Supersedes:** the old `kind: sandbox` `sbx-kit.yaml` (invalid for sbx v0.35) —
removed in this PR.

---

### 2026-07-20 · MARKETS box: add Gold/Silver prices + S&P/NASDAQ volume

**Status:** Implemented (this PR).

**Context:** Alex wanted the MARKETS box to show more than tracked equities —
precious-metal prices and index trading volume.

**Choice:** Two FIXED sub-sections in the box (not conversationally tracked,
unlike stocks): **Metals** (Gold `GC=F`, Silver `SI=F` — COMEX front-month
futures) and **Volume** (S&P 500 `^GSPC`, NASDAQ `^IXIC` regular-session volume).
All four come from the same Yahoo v8 chart endpoint already used for stocks
(`query1.finance.yahoo.com` — already allow-listed, no new egress).
`GET /v1/dashboard/stocks` now returns `{items, metals, indices}`, fetched
concurrently via `feeds.fetch_markets()`; the box renders labelled sub-sections
and scrolls if needed.

**Why:** Reuses the proven Yahoo fetcher and existing egress; fixed (not tracked)
keeps a small, stable set simple. Volume formatted B/M; metals as `$` with
thousands separators.

**Trade-offs:** Metals follow the front-month futures contract (near spot, not
exact spot). The box scrolls when stocks + metals + volume exceed its height —
stock count can be trimmed later if a no-scroll at-a-glance view is preferred.

---

### 2026-07-20 · Dashboard news: dropped CNN (dead RSS), added NBC News

**Status:** Implemented (this PR).

**Context:** The HUD headlines box stopped updating. CNN retired its public RSS —
`rss.cnn.com` returns HTTP 200 but frozen content (items from 2023, last built
Aug 2024); Fox was fine. MSNBC + Newsmax were evaluated as replacements: MSNBC has
no working feed (rebranded to "MS NOW"; old + new domains + Google News all dead or
stale to 2021), and Newsmax blocks direct RSS (Cloudflare) — reachable only via a
lagging Google-News proxy.

**Choice:** Drop CNN; add **NBC News** (`feeds.nbcnews.com/nbcnews/public/news` —
direct, fresh, reliable) alongside Fox. Direct feeds only (no proxy). `_load` now
prunes any unsupported news source from a persisted store, so a retired feed can't
linger.

**Why:** Direct feeds beat proxies (which bring redirect links, title suffixes,
days-lag, and an unofficial dependency). NBC News is a reliable, fresh counterpart
to Fox — the balance CNN used to provide.

**Trade-offs:** No Newsmax/MSNBC (no usable feeds today). A Google-News-proxy mode
was offered and declined for now; it remains the path if an outlet without a real
feed is ever required.

---

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

**Update (2026-07-17, later):** Alex went with a **single vault** (recommended
over multiple, since Obsidian's `[[wikilinks]]` / graph / search are vault-scoped
and a connected corpus is the point). Added **single-vault mode**
(`OBSIDIAN_VAULT_PATH`): one whole-vault knowledge base — Victoria sees
everything, and top-level folders (`Docker/`, `Personal/`, `Brain/`…) are the
areas she targets via a `folder` argument (with tolerant "folder-as-vault"
matching so "search my Personal notes" just works). His vault is
`~/Obsidian/AI/AI-Victoria`; a separate, empty `Docker` vault predates the
decision and is being folded in as a folder (to confirm). Verified live against
the real vault (list + folder-scoped write). Per-area mode retained as the
alternative. The installer (`setup-victoria-mac.sh`) now prompts for the vault —
detecting candidates from Obsidian's own `obsidian.json` and letting the user
pick one, type a path, or skip — so no location or name is hard-coded
(`--obsidian-vault` / a no-TTY run skip the prompt).

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
24-hr local time + °F), Yahoo Finance v8 (stock price + name), NBC News/Fox RSS
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
