# Victoria AI — Session Handoff

> Read this entire file before doing anything. It is the complete working context
> for a fresh agent session with no other history. Repo: `~/victoria-ai`
> (github.com/mmaxwell00/victoria-ai). Session shell cwd may differ; all repo work
> happens in `~/victoria-ai`. After reading, confirm you're resuming from this file.
>
> STANDING RULES (hard):
> 1. NEVER self-merge a PR. Open it, report it, and wait for Mark to say "merge #N".
> 2. Work on a branch; open a PR for every change.
> 3. Keep `README.md`, `claude-md.md`, and the `build-ai-assistant` skill's
>    `victoria-reference.md` (repo AND the `~/.claude` copy) in sync — test counts,
>    endpoints, features, file tree.
> 4. Use the `code-review-repo` skill for repo consistency audits.
> 5. `git commit -m "…"` with backticks corrupts the message (shell command-subst).
>    Use `git commit -F -` with a quoted `<<'MSG'` heredoc, or `--body-file` for PRs.

Last updated: 2026-08-13. `main` at `bdddf1a`. **365 tests pass.**
All PRs through #96 merged. ⚠️ `sbx` **auto-updated to v0.38.0 mid-session** (older
notes below say v0.35 / v0.37.1) and its control plane became unreliable immediately
after — see §4.

## ⚡ "Victoria pauses before answering" — SOLVED, and it was FIVE causes

Mark reported this THREE times; each fix revealed the next underneath. Do not treat a
slow reply as one bug. Measured end-to-end on the real HUD path (`/v1/chat/stream`),
a weather question went from **~16s of silence** to **first words at ~1.4s**:

| PR | Cause | Evidence |
|---|---|---|
| #91 | ChromaDB re-downloaded its embedding model on EVERY query (egress-blocked → 142-byte 403 page → SHA256 fail) | ~4s wasted per turn, and cross-session recall silently dead while `/health` said `semantic_memory: true` |
| #93 | Docker Model Runner evicts an idle model after ~5 min; the next question pays the reload | **5.3s cold vs 0.36s warm**; no TTL knob exists, so `victoria/core/model_warmer.py` pings every `model_keepalive_seconds` (0 disables; costs ~4.4GB RAM) |
| #94 | Nothing streamed — the whole reply arrived as ONE chunk at the end | `+5.87s chunk_len=274` then done; 2 events for a 274-char answer. Post-tool synthesis now streams (41–78 events) |
| #95 | Per-turn context injected into the SYSTEM message changed the prompt prefix, discarding llama.cpp's KV cache | stable prefix **0.25–0.30s** (`cached 3190/3199`) vs mutated **2.85–2.96s** (`cached 834/3211`) — ~10x, twice per tool question |
| #96 | **Model calls relayed through the sandbox egress proxy** — `NO_PROXY` covered localhost + gateway but NOT `host.docker.internal` | same code, spaced 20s apart: **2.0s/turn on the host vs 6.9–8.8s in the sandbox** → 2.45–2.56s after the bypass. NOT in the code at all |

**The diagnostic that found all five:** compare Victoria against the RAW Model Runner
(`curl host :12434 …` → 0.1–0.3s warm). If she is 10–50x slower, the time is ours, not
the model's. Then bisect: raw model → +system prompt → +tool schemas → tool itself →
semantic search. Every one of those is measurable in isolation, and three of the five
causes were invisible from `/health`, and the last was not in the code at all — when
the same code is 3.5x slower in one environment than another, STOP bisecting the code
and compare environments (running `ConversationManager.chat()` in-process on the host
vs the identical turn in the sandbox is what cracked it).

Current measured state (sandbox, `:8001`): a tool question shows **first words at
~1.4s** and completes in ~2.0s; spaced-out turns ~2.45–2.56s, matching the host
baseline (2.0–2.13s). Plain chat ~0.4–0.8s. Residual, accepted: the FIRST request
after a restart is ~5s while the prompt cache repopulates — once per restart.

⚠️ **Egress lens on the same lesson.** Default-deny egress has broken three separate
things, none of which looked like a network problem. Before debugging anything odd in
the sandbox, ask "is a host missing from `network.allowedDomains`?" — a 403 rarely
announces itself; it surfaces as a corrupt download, an "unsigned" apt repo, or a
silently empty search.
- **A cold `./deploy-sandbox.sh` was BROKEN** (fixed #90): four missing build hosts.
  Deploy runs `sbx rm` *before* create, so a failed create leaves **no sandbox at all**.
- **Semantic memory was silently DEAD** (fixed #91): ChromaDB re-downloaded its
  embedding model on every query, got the proxy's ~142-byte 403 page, failed its SHA256
  check, and returned nothing — while `/health` still said `semantic_memory: true`.
  This also made every chat turn ~4s slower. Fix: allow-list
  `chroma-onnx-models.s3.amazonaws.com`. NOTE it was only ONE of five causes of the
  reported "pausing" — see the table at the top before concluding a slow reply is
  explained.
- **Escalation from the sandbox is denied** — that one is DELIBERATE (see below).

Claude escalation: the host bridge **works from the host** (verified 2026-08-04,
`http=200`, real Claude answer) but is **DENIED from inside the sandbox by egress
policy — deliberately.** Mark's decision: network policy is the control point, so the
sandbox is **local-model-only** and escalation happens from the native/host run. A
filesystem side-channel to restore it was **rejected as a covert channel.** See §2 and
the 2026-08-04 ADR.
The sandbox has a **host-side launchd watchdog** (#87) so `:8001` survives reboots and
Docker container recycles — **validated by a real reboot**, not just simulation: on
2026-08-01 it fired at login, waited out Docker's boot, and had Victoria serving again
~44s later with no human involved (see §2). Victoria also correctly **owns her Obsidian
knowledge base** in conversation as of #86 (she used to deny filesystem access).

**Resolved, but expect it to recur:** the `sbx` CLI signs itself out periodically
(`sbx ls` → `401 … no valid user session found`). Mark fixed it with `sbx login`
(2026-08-04) and the watchdog is armed again. Victoria keeps serving throughout — the
sandbox container outlives the CLI session — but the watchdog repairs *through* `sbx`,
so a signed-out CLI silently **disarms the safety net**;
`./scripts/setup-watchdog.sh --status` now says so explicitly. A Docker Desktop session
expiry also explains 2026-07-30's two container recycles and the
`com.victoria.claude-bridge` SIGTERM (`last exit -15`). Separately, the sbx control
plane can WEDGE (see §4) — different symptom, different fix.

## 1. Who / Goal

**User:** Mark Maxwell (Docker CSM/AE). Prefers direct action over questions —
diagnose and fix end-to-end with clear status summaries. Security-minded (CISSP/
CRISC): credential-containment matters to him.

**Victoria** is Mark's local-first, JARVIS-style personal AI assistant (British,
witty): local LLM via Docker Model Runner, **opt-in Claude escalation** (human-in-
the-loop), layered memory, a web HUD, tools, MCP, an encrypted vault, and an
Obsidian-backed knowledge base.

**Where the work is right now (newest first):**
- **Sandbox egress is now DEFAULT-DENY, and escalation from the sandbox is OFF by
  policy (2026-08-04).** The org `NetworkAll: allow **` rule is **gone**; `sbx policy ls`
  shows `kit  sandbox:victoria  network: 34 allow` (was 20; #90/#91/#96 added build, embedding + proxy-bypass entries), so the kit's `network.allowedDomains`
  **is** the live policy (verified: allow-listed → 200, Yahoo → 429 = allowed/rate-limited,
  `example.com` → **403**). ⚠️ **The allowlist is load-bearing:** any feature calling a
  host not in `sbx/spec.yaml` fails 403 until it's added + redeployed.
  Consequence for escalation: the egress proxy (`gateway.docker.internal:3128`)
  **ssl-bumps host-directed TLS** — `CONNECT host.docker.internal:8787` is granted then
  answered with the proxy's own cert (`CN=localhost`, issuer `Docker Sandboxes Proxy CA`),
  so mTLS to the bridge cannot work (client cert can't traverse a bumping proxy; the
  bridge requires one). Only `:12434` (Model Runner) is usable on the host.
  **Mark's decision: keep it that way** — network policy is the control, the sandbox is
  local-model-only, escalate from the native/host run. Full rationale + rejected options
  in the 2026-08-04 ADR and `SECURITY-AUDIT.md`.
- **Claude escalation via a host bridge — built and working FROM THE HOST** (denied from
  the sandbox, see above; that path was verified end-to-end on 2026-07-27, before the
  egress change). Set up once
  with `./scripts/setup-bridge.sh` (certs + governed `victoria-claude` sandbox + launchd
  auto-start bridge); `deploy-sandbox.sh` auto-wires Victoria from `~/.victoria/bridge-env`.
  Verified 2026-07-27: `POST /v1/chat {backend:"claude"}` → Victoria (sandbox) → mTLS →
  host bridge → `sbx exec victoria-claude -- claude -p` → real Claude answer. The Claude
  token never enters Victoria's VM (she holds only the mTLS *client* identity); the
  built-in claude agent authenticates via the **global** anthropic OAuth (`sbx secret ls`).
  See §2 and §5.
- **Docker Sandbox (`sbx`) deployment — mature.** Victoria runs in an isolated
  microVM, self-healing, with a portable kit. See §2.
- **RAG Phase 1b — queued, not started.** Semantic recall over the vault's notes.
- **Knowledge Phase 2 (AI-vault-as-memory), Obsidian REST/MCP — later.**

## 2. Current State (what exists now)

**Test suite: 358 pass** (`python -m pytest -q`, use `.venv/bin/python`).
All PRs #39–#94 merged (#95 open). Most recent: #86 = Victoria owns her Obsidian knowledge base
(she used to deny filesystem access); #87 = the host-side uptime watchdog; #88 =
watchdog recognises a signed-out `sbx`; #89 = egress/escalation security docs;
**#90 = repaired the cold deploy**; **#91 = unblocked ChromaDB's embedding model**
(semantic memory was silently dead + ~4s/turn slower — see the warning at the top).

**Cold deploy — fixed in #90, and what to expect (measured 2026-08-05).** Under
default-deny egress the build needs hosts that were never allow-listed, and each
failure looked like something else entirely:
| Symptom | Actually missing |
|---|---|
| `apt … exited 100`, "repository is not signed" | Ubuntu mirrors — the base image moved to **Ubuntu**, so apt uses `ports.ubuntu.com`, not `deb.debian.org` |
| ffmpeg absent though create "succeeded" | `download.docker.com` — the image ships a Docker CE apt repo, so `apt-get update` exits non-zero and the `&&` skips the install |
| `uv venv --python 3.11` → 403 | `release-assets.githubusercontent.com` (covered by `*.githubusercontent.com`) |
| `/v1/transcribe` → "CAS Client Error … 403" | Hugging Face **Xet CDN** on rotating hosts; also `HF_HUB_DISABLE_XET=1` |
Two rules baked in: apt is now **non-fatal** (a failing start hook must not cost the
whole deployment), and **sbx wildcards are `*` = ONE label, `**` = many** —
`*.hf.co` does NOT match `us.aws.cdn.hf.co`. A cold rebuild takes ~4–5 min and was
verified end-to-end: HUD, semantic memory, knowledge base, dashboard, ffmpeg,
TTS 200, and STT transcribing real audio.

**Sandbox deployment (the primary run mode):**
- Kit at `sbx/spec.yaml` (`kind: sandbox`, image `docker/sandbox-templates:shell-docker`).
  A **Python 3.11 venv** (via preinstalled `uv`) installs the FULL `requirements.txt`
  → ChromaDB semantic memory + voice deps active.
- **Portable kit (#71):** the kit uses `__VICTORIA_REPO__` / `__VICTORIA_VAULT__`
  placeholders that `deploy-sandbox.sh` substitutes at pack time. No per-user edits.
- **Self-healing (#73):** the startup command runs uvicorn in a `while true` supervisor
  loop — if uvicorn dies (crash, or an `sbx exec` cycling the sandbox), it restarts in
  ~3s. Demonstrated working. **BUT this only covers uvicorn dying — not the container
  dying.** `startup` is one-shot at `sbx run`, so a Docker container recycle (idle /
  resume / pressure) kills uvicorn AND this supervisor, and a Mac reboot never re-fires
  it. Both leave `:8001` dark. See the watchdog below.
- **Host-side watchdog (#87):** `./scripts/setup-watchdog.sh` installs a launchd agent
  (`com.victoria.watchdog`, `RunAtLoad`+`KeepAlive`) that polls `/health` every 30s and
  repairs from the HOST — the only place that survives a container recycle. App healthy
  inside but host port dead → re-publish only; app dead → relaunch the supervised
  uvicorn (`sbx exec` starts a stopped sandbox first, so reboot is covered). Never
  recreates: the venv survives, so repair is seconds. A DELETED sandbox is out of scope
  (still `./deploy-sandbox.sh`). Log: `~/Library/Logs/victoria-watchdog.log`;
  status: `./scripts/setup-watchdog.sh --status`. Verified: killed uvicorn → back in
  ~15s; unpublished the port → back in ~20s, both hands-off; then a **real reboot** on
  2026-08-01 → recovered ~44s after login (it logged `Docker not ready yet` once, then
  repaired on the next cycle).
  **Two hard dependencies to know about:** the watchdog repairs *through* the `sbx` CLI,
  so it is disarmed if `sbx` is (a) **signed out** (`sbx login` — it now logs exactly
  this) or (b) **wedged** (hung `sbx ls`/`exec` processes seen surviving >24h; every call
  is timeout-bounded so the watchdog survives, but repairs queue). Check both with
  `./scripts/setup-watchdog.sh --status`.
- **Startup race guard (#66):** startup waits (≤~20 min) for `import uvicorn, victoria.main`
  before launching — covers cold uncached wheel installs on a slow host.
- **Voice (#70):** the Piper model (`en_GB-jenny_dioco-medium.onnx`) is gitignored, so
  `deploy-sandbox.sh` stages it into `$REPO_STAGE/models` (copy from a native checkout,
  else download from Hugging Face). STT (Whisper) + TTS (Piper) both work in-browser.
- **Claude CLI (#72):** the kit installs `@anthropic-ai/claude-code` (npm, LAST +
  non-fatal); `CLAUDE_CLI_COMMAND` is the absolute path `/usr/local/share/npm-global/bin/claude`.
- Sandbox named `victoria`; repo staged at `~/sandboxes/victoria-ai`; vault mounted from
  `~/Obsidian/AI/AI-Victoria`; host Model Runner at `host.docker.internal:12434`; HUD
  published at **`http://127.0.0.1:8001`** (IPv4 — NOT localhost). `./deploy-sandbox.sh`
  reproduces it (cold rebuild ~15–20 min on this host).
- **As of last session Victoria was running supervised in the sandbox** and healthy.

**Native app:** runs via `uvicorn victoria.main:app` on `:8000`. Mark KILLED the native
`:8000` instance last session (it was bound to `0.0.0.0`); it's currently stopped. The
sandbox (`:8001`) is the live deployment.

**Claude escalation — the host-bridge design (approved #74, built #75):**
- **Control = Level 1 (already built, default):** Victoria's local model *suggests*
  escalation ("Shall I put it to Claude? (yes/no)") and only calls Claude on the user's
  explicit **yes**. Code: `conversation.py::_offer_escalation` / `_classify_reply`.
- **Transport (built, #75):** when `CLAUDE_BRIDGE_URL` is set, `llm_router.claude_cli()`
  routes to `_claude_via_bridge()` — POSTs the prompt to a **host bridge** over **mTLS**
  instead of running `claude -p` locally. Backward-compatible (blank URL → local CLI).
- **The bridge** = `scripts/claude-bridge.py` (stdlib-only, runs on the HOST). mTLS
  server (client cert required) that runs `claude -p` in a governed built-in
  `claude`-agent sandbox (prompt on stdin, never a shell arg). `--gen-certs` makes
  CA + server + client certs. **Two transports** (`CLAUDE_BRIDGE_MODE`, default `exec`):
  `exec` → `sbx exec -i victoria-claude` (works on the **stable `sbx`**, no nightly);
  `ssh` → `ssh victoria-claude.sbx` (nightly `sbx`, sturdier under load).
- **One-command setup (#78):** `./scripts/setup-bridge.sh` (idempotent) —
  certs → `sbx create --name victoria-claude claude` → launchd agent
  (`com.victoria.claude-bridge`, auto-start + self-restart) → writes `~/.victoria/bridge-env`.
  `deploy-sandbox.sh` reads that file, stages the mTLS **client** identity under the repo
  mount (`$REPO_STAGE/.bridge/`, readable in-sandbox) and sets `CLAUDE_BRIDGE_*`; when the
  bridge is configured it bakes **no** Claude token into the VM.
- **Credential containment (the point):** the real subscription token stays ONLY in
  sbx's host subsystem (Keychain + proxy); the claude sandbox sees a `proxy-managed`
  sentinel; Victoria sees only prompt/response + holds only the mTLS *client* identity
  (not the Claude token). No Claude credential in any sandbox.
- **ACTIVATED + verified end-to-end (2026-07-27).** `setup-bridge.sh` ran (bridge is a
  launchd agent `com.victoria.claude-bridge` on `:8787`, exec mode → `victoria-claude`);
  Victoria redeployed and wired. Real escalation returns a Claude answer (see §4 for the
  two mTLS bugs fixed to get here). No manual `sbx run` login was needed — the anthropic
  OAuth is **global**. Refresh only if escalation later errors "OAuth session expired":
  `sbx run --name victoria-claude` → `/login`.
- Diagrams: `docs/claude-bridge-architecture.svg` (approved design + review notes),
  `docs/claude-escalation-host-bridge.svg`, `docs/claude-escalation-path2-keychain.svg`.

**Dashboard:** MARKETS = your tracked stocks (tracked order) + Gold `GC=F` / Silver `SI=F` + volume S&P `^GSPC`
/ NASDAQ `^IXIC` (Yahoo v8). NEWS = NBC + Fox (CNN dropped, dead RSS).

**Knowledge base:** single-vault via `OBSIDIAN_VAULT_PATH` (`~/Obsidian/AI/AI-Victoria`);
tools `search_notes` / `read_note` / `list_notes` / `write_note`.

## 3. Architecture & locked decisions

- **Stack:** Python 3.11 · FastAPI + Uvicorn · ChromaDB (semantic memory) · SQLite
  (session memory) · Fernet vault · httpx · faster-whisper (STT) · Piper (TTS).
- **[LOCKED] Sandbox egress = DEFAULT-DENY via the kit allowlist** (supersedes the old
  "broad / decision C", which assumed org rules override kit rules — no longer true).
  `sbx policy ls` → `kit  sandbox:victoria  network: N allow`. Keep `sbx/spec.yaml`'s
  `network.allowedDomains` current or features 403. Full detail: `SECURITY-AUDIT.md`.
- **[LOCKED] Claude escalation = host bridge, subscription auth, credential never in the
  VM.** API-key billing rejected; token-in-VM (Path 2) rejected on containment grounds.
- **[LOCKED] The prompt PREFIX must stay byte-stable across turns (#95).** Do NOT put
  per-turn content (recalled memories, message-relevant skills, timestamps, anything
  that varies) into the system prompt — it invalidates llama.cpp's KV cache and forces
  a ~3.2k-token re-prefill (including ~2,250 tokens of tool schemas) on EVERY pass:
  0.25-0.30s cached vs 2.85-2.96s not. Volatile context goes through
  `_turn_context()` + `_with_turn_context()`, which attach it to the last USER message.
  `tests/test_tool_calling.py::test_system_prompt_is_identical_across_turns` guards
  this — if it fails, someone has moved volatile text back into the prefix.
- **[LOCKED] Escalation stays NETWORK-GATED — no side-channels.** Victoria must not be
  able to reach Claude by any route that bypasses network policy. A mount-based
  request/response channel was proposed and **rejected as a covert channel**. If sandbox
  escalation is ever wanted, do it the policy-visible way: `api.anthropic.com` (already
  allow-listed) with the credential **proxy-injected via `sbx secret`**, never a token in
  the VM and never a filesystem side-channel.
- **[LOCKED] Level-1 consent** — Victoria suggests, the user gives the final yes. Never
  auto-call Claude.
- **Governance:** `sbx` managed by org `mmaxwelldemoorg` (remote-synced). Active
  fs-mount allow rules: `~/sandboxes/**` and `~/Obsidian/**` (both required, case-sensitive).

## 4. What's Been Tried That Failed (DO NOT REPEAT)

**Chasing the wrong layer on a "slow reply" complaint (cost most of a session):**
- **Do NOT start in the tool loop.** It was fine every time. The give-away was that a
  NON-tool question ("say hello in five words") was equally slow — 16.6s while the raw
  Model Runner answered the same thing in 0.15s. Measure the model directly FIRST.
- **Do NOT trust a single measurement.** Latency here is bimodal (cold/warm model,
  cached/uncached prefix). One 11.5s reading did not reproduce across 5 runs and a
  second idle test; one 4.2s reading was the cache being populated. Take 3+.
- **`memory_pressure` ruled out paging** (0 pageouts, 91% free) — worth checking before
  blaming swap for an after-idle cost.
- **Streaming is not cosmetic here.** Even at 1.8s total, emitting one chunk at the end
  means the HUD shows only typing dots; the perceived delay is time-to-FIRST-token, so
  measure `time_starttransfer` and count `data:` events, not just total.

**Trusting `/health` to tell you a subsystem works (cost a silent regression):**
- **`semantic_memory: true` does NOT mean recall works.** It only means ChromaDB
  initialised. Every `search()` was failing (blocked embedding-model download → SHA256
  mismatch) and returning nothing, so cross-session memory was dead for an unknown
  period while health looked green. DO NOT REPEAT: verify memory **functionally** —
  state a distinctive fact in session A, ask about it in a NEW session B. Fixed in #91
  by allow-listing `chroma-onnx-models.s3.amazonaws.com`; if it recurs, check
  `/tmp/victoria.log` in-sandbox for "does not match expected SHA256".
- **Chasing the tool loop for a "slow/no answer" complaint.** The tool loop was fine.
  The give-away was that a NON-tool question ("say hello in five words") was equally
  slow (16.6s) while the raw Model Runner answered the same thing in 0.3s. Measure the
  model directly before touching orchestration code.

**sbx credential injection for Claude escalation (tested 2026-08-05, REJECTED):**
- **`serviceDomains`/`serviceAuth` for `api.anthropic.com` + `proxyManaged:
  [ANTHROPIC_API_KEY]`.** DO NOT REPEAT without an actual API key. The kit mechanism
  works (the VM got the documented `ANTHROPIC_API_KEY=proxy-managed` sentinel, and
  egress was already fine — 401 from Anthropic, not 403), **but sbx reported
  `SBX_CRED_ANTHROPIC_MODE=none`**: the stored global `anthropic` secret is an OAuth
  *subscription* session, not an injectable API key, so it still 401'd. A real API key
  means metered billing, which is a standing no.
- **Worse, it silently degraded Victoria.** The sentinel is read by pydantic into
  `settings.anthropic_api_key`, which `llm_router._pick_backend` uses as a **TRUTHY
  GATE** — so every query over `complex_query_threshold` (200 words) would be routed to
  the Claude API with a bogus key and 401 **instead of being answered locally**
  (verified: gate `True`). Comments in `sbx/spec.yaml` warn against re-adding it.
- **Kit alignment cannot reopen the mTLS host bridge either.** Host-service access is
  undocumented across build-an-agent / kits / kit-reference / credentials — no
  `host.docker.internal`, localhost, raw TCP, or client certs. The model assumes all
  egress goes through the host proxy, which ssl-bumps host-directed TLS.

**Two ways the sandbox got taken down while debugging (both avoidable):**
- **Wrapping `sbx daemon start` in a timeout.** DO NOT REPEAT. It runs in the
  **FOREGROUND** — the long-lived process *is* the daemon (a healthy one shows as a
  days-old `sbx daemon start`). A timeout wrapper kills the daemon it just started, and
  Victoria goes down with it. Start it detached: `nohup sbx daemon start >log 2>&1 &`.
- **Assuming a failed deploy is harmless.** `deploy-sandbox.sh` runs `sbx rm --force`
  *before* create, so a failed create leaves NO sandbox — and the watchdog deliberately
  won't rebuild one. Unload the watchdog before a redeploy (it fights the recreate) and
  reload it after.
- **Diagnosing a `user: "0"` install step via `sbx exec`.** `sbx exec` runs as **uid
  1000**, so an apt test through it fails with `Permission denied` on
  `/var/lib/apt/lists/` and tells you nothing about the real install. Read the
  create-time error from the daemon log instead:
  `~/Library/Application Support/com.docker.sandboxes/sandboxes/sandboxd/daemon.log`.

**sbx v0.38.0 (auto-updated 2026-08-13) — control plane became unreliable:**
- Symptoms in one session: THREE wedges; `POST /sandbox/victoria/start` → **500** in a
  loop; two `sbx run` invocations hanging **34 and 39 minutes**; `sbx ls` and
  `sbx exec` hanging while `sbx daemon status` answered instantly.
- Victoria survives it: the container starts and serves even when `sbx run`'s attach
  hangs — check `curl -4 127.0.0.1:8001/health` before assuming she is down.
- Recovery that worked: `pkill -9 -f 'sbx exec'` / `'sbx ls'`, then kill and restart
  the daemon **detached** (`nohup sbx daemon start &` — it runs in the FOREGROUND, so a
  timeout wrapper kills it), then re-run the deploy. If that fails, the next remedy is
  a Docker Desktop restart (Mark's documented one; affects his whole Docker env, so
  ASK). **Never kill `/usr/libexec/sandboxd`** — that is Apple's, not Docker's.
- This is now a bigger availability risk than latency ever was. If it keeps recurring,
  consider pinning the sbx version.

**sbx control-plane wedge (recurs; the watchdog survives it, repairs queue):**
- Symptom: `sbx daemon status` answers instantly ("running") while `sbx ls`/`sbx exec`
  hang ~10s then jam, and hung `sbx` processes pile up (seen surviving >24h). Daemon log
  shows `create SDK client: health check: context canceled` for every runtime — it can't
  reach its own `docker.sock` proxy. Docker itself is fine.
- Remedy: `kill -9` the hung `sbx` clients, then the Docker daemon process
  (`/opt/homebrew/bin/sbx daemon start`) and restart it **detached**. **NEVER kill
  `/usr/libexec/sandboxd`** — that is Apple's own system daemon, not Docker's.
- Always bound your own `sbx` calls in scripts; the watchdog already does (`run_timeout`
  + depth-first `kill_tree`, since `pkill -P` orphans grandchildren).

**Watchdog repair via process matching (fixed in #87 — cost two silent failed repairs):**
- **`pgrep -f` / `pkill -f` inside `sbx exec`.** DO NOT REPEAT. `sbx exec <sbx> -- sh -lc
  '<cmd>'` gives the wrapper shell a cmdline that **contains `<cmd>`**, so
  `pgrep -f "uvicorn victoria.main"` matches **itself** → always reports ALIVE (it
  reported ALIVE while uvicorn was dead), and `pkill -f "uvicorn victoria.main"` makes
  that shell **SIGTERM itself** → it logged "relaunched" while launching nothing, and
  even killed a live server. Bracket patterns (`uvicorn[ ]victoria.main`) fix the
  self-match, but **a combined kill+launch command still self-matches** via the runner's
  own path (`.victoria-run.sh` contains `victoria-run`) — so keep the kill and the launch
  in **separate `sbx exec` calls**. Prefer an **HTTP probe** (`curl` the in-sandbox
  `/health`) over process matching for liveness, and background survivors with
  `setsid nohup` (a plain `&` job dies with the exec session).
- **Strengthening the in-VM supervisor instead.** Pointless for this failure: anything
  inside the VM dies with the container. Recovery must come from the host.
- **Auto-running `deploy-sandbox.sh` on failure.** Rejected — a ~15-20 min cold rebuild
  as an unattended reflex, when a recycle leaves the venv intact and a relaunch takes
  seconds. The watchdog only ever does the cheap repair.

**Bridge mTLS activation (fixed in #80 — the two bugs that made escalation silently fail):**
- **Self-signed CA without extensions.** The old `_gen_certs` made a CA with no
  `basicConstraints`/`keyUsage`. `curl`/LibreSSL accept it, so it "worked" in early tests —
  but strict path validation (Python's `ssl` / OpenSSL 3.x, which the bridge AND Victoria
  use) rejects it (`CA cert does not include key usage extension` → handshake reset). DO
  NOT verify mTLS with `curl` alone — it's too lenient. Certs now carry CA:TRUE +
  keyCertSign and leaf keyUsage + serverAuth/clientAuth EKU; a test runs `openssl verify
  -x509_strict`.
- **httpx `cert=(crt,key)` + `verify=<ca_path>`.** In httpx 0.28 this completes the TLS
  handshake but NEVER presents the client cert → the mTLS bridge resets the connection →
  `httpx.ReadError` / server-side "certificate required". DO NOT use the `cert=`/`verify=`
  kwargs for mTLS. Build an explicit `ssl.SSLContext` (`create_default_context(cafile=ca)`
  + `load_cert_chain`) and pass `verify=ctx` (`llm_router::_claude_via_bridge`).
- **Bounding a hung `sbx` call with SIGALRM.** `perl -e 'alarm N; exec sbx …'` does NOT
  time out — `sbx` is a Go binary and ignores SIGALRM; it hangs anyway and spawns another
  stuck client. DO NOT. Run `sbx` detached (background) and `kill -9` it if it doesn't
  return. See the `sbx-daemon-wedge` memory: a stuck Openclaw `sbx cp` wedged the shared
  daemon and blocked Victoria's `sbx ls`/`create`; recovery = kill hung clients + the
  `sbx daemon start` pid (auto-respawns), never touch `/usr/libexec/sandboxd` (Apple).

**Claude escalation / sbx credentials:**
- **Making the sbx proxy authenticate Victoria's CUSTOM agent.** DO NOT REPEAT.
  Declaring the full anthropic `oauth:` block (+ serviceDomains/serviceAuth/credentials/
  proxyManaged) in the kit DOES seed `~/.claude/.credentials.json` with a sentinel, BUT
  the proxy's OAuth swap/refresh is wired to the **built-in `claude` agent only** — it
  never swaps for a custom agent. Verified both ways: OAuth-file path → "OAuth session
  expired… could not be refreshed"; ambient API-key path → "Invalid API key". This is
  why the design delegates to a built-in claude agent via the bridge. (`sbx secret set
  --oauth` is openai-only; anthropic OAuth is (re)seeded by running `sbx run --name victoria-claude`.)
- **Injecting the token into the VM (Path 2).** Works, but the real token then lives in
  Victoria's env — she can read it. Rejected on containment grounds (kept as the merged
  #72 file-token FALLBACK only; `CLAUDE_CLI_OAUTH_TOKEN` via `~/.victoria/claude-oauth-token`).

**sbx operational (learned the hard way this session):**
- **Interrupting an `sbx exec` mid-flight (TaskStop / kill of the bash wrapper).** DO NOT
  REPEAT. It orphans the sbx CLI child holding the daemon lock and **wedges the sbx
  daemon** (subsequent `sbx ls`/`exec`/deploy hang for minutes). Happened ~5×. Recovery:
  `ps` for hung `sbx exec/ls/rm` + `bash ./deploy-sandbox.sh`, `kill -9` them, `kill -9`
  the `sbx daemon start` pid (it respawns), then a bounded `sbx ls` to confirm.
- **Diagnostic `sbx exec` against a running/deploying sandbox.** Flaky on this host and a
  frequent wedge trigger. PREFER: read files with **`sbx cp victoria:/path ./local`**
  (reliable, no wedge); verify health/chat over **HTTP** (`curl -4 127.0.0.1:8001/...`);
  never run `sbx exec` during a deploy.
- **8-min startup gate on a cold uncached install.** DO NOT REPEAT (too short). The full
  wheel set (torch/faster-whisper/chromadb) re-downloads each rebuild; gate is now ~20 min.
- **npm-installing the Claude CLI BEFORE the uv/pip venv install.** DO NOT REPEAT. The
  large npm download starved/delayed the venv install → the gate FATAL'd on "No module
  named uvicorn". CLI install must be LAST in `commands.install`.
- **`claude` by bare name from the uvicorn subprocess.** DO NOT REPEAT. It installs to
  `/usr/local/share/npm-global/bin` (on interactive-shell PATH via profile, NOT the
  service PATH). Use the absolute `CLAUDE_CLI_COMMAND`.

**sbx deployment (still valid from earlier):**
- **Raw-YAML kit / mounting `~/victoria-ai` or the vault directly / `localhost` for Model
  Runner / uvicorn as `entrypoint` / `sbx exec nohup uvicorn &` / `curl localhost:8001`
  (IPv6 reset) / full deps on py3.14 / uv steps as root.** All DO NOT REPEAT — details in
  git history (#62/#63/#66) and `SANDBOX-DEPLOYMENT.md` gotchas.
- **`import sounddevice` in the sandbox** → `PortAudioError`. EXPECTED (headless microVM,
  no audio device). Lazy-imported; browser voice is the path. Don't "fix" native mic.
- **`git commit -m "...backticks..."`.** DO NOT REPEAT (shell command-substitutes them).

## 5. What to Do Next

**A) Claude bridge — works FROM THE HOST; deliberately unreachable from the sandbox.**
No open work, and **do not "fix" the sandbox path** — escalation is network-gated by
decision (§3, and the 2026-08-04/08-05 ADRs). The sandbox is local-model-only; escalate
from the native/host run. Verified 2026-08-04: host → bridge returns a real Claude
answer (`http=200`). Operational notes if it ever misbehaves:
- Bridge is a launchd agent `com.victoria.claude-bridge` on `:8787` (exec mode →
  `victoria-claude`). Logs: `~/Library/Logs/victoria-claude-bridge.log`. Manage:
  `launchctl unload/load -w ~/Library/LaunchAgents/com.victoria.claude-bridge.plist`.
- Quick health from the host (mTLS): `curl --cert ~/.victoria/bridge-certs/client.crt
  --key …/client.key --cacert …/ca.crt -X POST https://127.0.0.1:8787/ask -d
  '{"prompt":"hi","model":"sonnet"}'`.
- Victoria's path: `POST http://127.0.0.1:8001/v1/chat {"message":"…","backend":"claude"}`.
- If "OAuth session expired": `sbx run --name victoria-claude` → `/login` (refreshes the
  global anthropic OAuth). Optional/not built: `ssh` mode on the nightly `sbx`
  (`BRIDGE_MODE=ssh ./scripts/setup-bridge.sh`, sturdier under load); harden hop-1 further.

**B) RAG Phase 1b (now the top open item — branch → PR):**
6. Add a SEPARATE ChromaDB collection for vault docs (distinct from the `conversations`
   collection in `semantic_memory.py`). Ingest the Obsidian vault markdown, chunked by
   heading, note-path metadata for citations. Embedding model: local
   `sentence-transformers/all-MiniLM-L6-v2` (Open Q4 in `docs/decisions-md.md` — confirm
   with Mark). Re-index on startup + on `write_note` + a `reindex` tool.
7. Wire retrieval into `conversation.py`: top-k vault chunks into the prompt with citations.
8. Tests; keep counts in sync (README/claude-md.md/victoria-reference.md ×2); add an ADR.

**C) Later:** knowledge Phase 2 (persist profile/learned facts as markdown in the AI
vault); Obsidian Local REST API / MCP.

**D) Small, optional, known:**
- **The bridge listens on `0.0.0.0:8787`** (LAN-reachable; mTLS still required). Nothing
  needs it off-host — the sandbox can't reach it either — so binding to `127.0.0.1`
  costs nothing. Raised with Mark, not yet done.
- **`docs/screenshots/sbx-hud.png`** still shows `MEMORY: OFFLINE` from Phase 1; it's
  ACTIVE now. Cosmetic re-capture.
- **Adding any feature that calls a new host** = add it to `network.allowedDomains` in
  `sbx/spec.yaml` + redeploy, or it 403s. The allowlist is load-bearing now.

## 6. Key files

```
victoria/core/llm_router.py          # claude_cli() → _claude_via_bridge() when CLAUDE_BRIDGE_URL set; Model Runner / Ollama / Claude backends
victoria/core/conversation.py        # orchestrator; Level-1 escalation consent (_offer_escalation/_classify_reply); RAG wires in here
victoria/config.py                   # settings incl. CLAUDE_BRIDGE_* (mTLS), CLAUDE_CLI_*, OBSIDIAN_VAULT_PATH, MODEL_RUNNER_URL
victoria/core/semantic_memory.py     # ChromaDB layer (conversation turns); RAG-over-vault extends THIS
victoria/vault/store.py              # Fernet vault; resolve() falls back to os.environ (Q3)
scripts/claude-bridge.py             # HOST bridge — mTLS in, `sbx exec -i` (default) or `ssh` out to claude -p; --gen-certs
scripts/setup-bridge.sh              # ONE-COMMAND setup: certs + `sbx create` governed sandbox + launchd auto-start + writes ~/.victoria/bridge-env
scripts/com.victoria.claude-bridge.plist.template  # launchd template setup-bridge.sh substitutes + installs
tests/test_claude_bridge.py          # bridge tests — Victoria's router path (mocked) + the script's exec/ssh transport (8 tests)
sbx/spec.yaml                        # THE kit — placeholders (incl. CLAUDE_BRIDGE_*), ~20-min import gate, supervisor loop, Claude CLI install, egress block (inert)
deploy-sandbox.sh                    # stage(code+Piper+bridge certs)->substitute->pack->run->publish->poll; auto-wires bridge from ~/.victoria/bridge-env
SANDBOX-DEPLOYMENT.md                # sbx guide: quickstart, gotchas, isolation & credentials (bridge + file-token), roadmap
SECURITY-AUDIT.md                    # egress decision-C writeup + org-activation steps
docs/decisions-md.md                 # ADRs (newest at top of "## Decided"); bridge one-command+exec ADR 2026-07-27, host-bridge design 2026-07-24
docs/claude-bridge-architecture.svg  # approved escalation design (+ 2 companion diagrams)
docs/build-ai-assistant/references/victoria-reference.md  # keep counts in sync (repo + ~/.claude copy)
~/sandboxes/victoria-ai              # staged clone the sandbox mounts (MUST be under ~/sandboxes/**)
~/Obsidian/AI/AI-Victoria            # the vault mounted into the sandbox
~/.victoria/claude-oauth-token       # (optional) file-token fallback, never committed
in-sandbox: /home/agent/venv         # py3.11 venv; /tmp/victoria.log = uvicorn log (pull with `sbx cp`)
```
