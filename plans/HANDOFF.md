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

Last updated: 2026-08-01. `main` at `e51b968` (+ the watchdog auth-awareness PR).
**347 tests pass.** All PRs through #87 merged.
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

⚠️ **Known live issue right now:** the `sbx` CLI is **signed out of Docker**
(`sbx ls` → `401 Unauthorized … no valid user session found`). Victoria is UNAFFECTED
and still serving (the sandbox container outlives the CLI session), but the watchdog
repairs *through* `sbx`, so **its safety net is disarmed until someone runs
`sbx login`**. `./scripts/setup-watchdog.sh --status` now says so explicitly. This is
also the likely root cause of 2026-07-30's two container recycles and the
`com.victoria.claude-bridge` SIGTERM (`last exit -15`) — a Docker Desktop session
expiry/restart explains all three.

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
  shows `kit  sandbox:victoria  network: 20 allow`, so the kit's `network.allowedDomains`
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

**Test suite: 346 pass** (`python -m pytest -q`, use `.venv/bin/python`).
All PRs #39–#80 merged. #78 = one-command bridge setup; #79 = auth-wording fix;
#80 = the mTLS fixes that made escalation actually work (see §4).

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
  `sbx policy ls` → `kit  sandbox:victoria  network: 20 allow`. Keep `sbx/spec.yaml`'s
  `network.allowedDomains` current or features 403. Full detail: `SECURITY-AUDIT.md`.
- **[LOCKED] Claude escalation = host bridge, subscription auth, credential never in the
  VM.** API-key billing rejected; token-in-VM (Path 2) rejected on containment grounds.
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

**A) Claude bridge — DONE + activated (2026-07-27).** No open work; it escalates
end-to-end. Operational notes if it ever misbehaves:
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
