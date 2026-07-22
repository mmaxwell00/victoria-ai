# Victoria AI — Session Handoff

> Read this entire file before doing anything. It is the complete working context
> for a fresh agent session with no other history. Repo: `~/victoria-ai`
> (github.com/mmaxwell00/victoria-ai). Session shell cwd may differ; all repo work
> happens in `~/victoria-ai`.
>
> STANDING RULES (hard):
> 1. NEVER self-merge a PR. Open it, report it, and wait for Mark to say "merge #N".
> 2. Work on a branch, open a PR for every change.
> 3. Keep `README.md` and the `build-ai-assistant` skill updated with changes.
> 4. Use the `code-review-repo` skill for repo consistency audits.
> 5. `git commit -m "…"` with backticks corrupts the message (shell command-subst).
>    Use `git commit -F -` with a quoted heredoc, or `--body-file` for PRs.

## 1. Goal

Victoria is Mark's local-first, JARVIS-style personal AI assistant (British, witty):
local LLM via Docker Model Runner, opt-in Claude escalation, layered memory, a
web HUD, tools, MCP, an encrypted vault, and an Obsidian-backed knowledge base.
The **active thread** is running Victoria **inside a Docker Sandbox (`sbx`)** for
hardware isolation from the host (the isolation tier Mark requires), with as many
features as possible, and pointing her at Mark's Obsidian vault as memory + RAG.
The **queued thread** is RAG (Phase 1b): semantic recall over the vault's notes.
Business reason: Mark needs strong host isolation for an always-on assistant, and
wants his notes to be Victoria's human-readable, syncable memory.

## 2. Current State

Native app (host): runs via `uvicorn victoria.main:app` on `:8000` from `~/victoria-ai`
on `main`. **337 tests pass.** All recent features merged (PRs #39-#67): weather
tool-use reliability, sidebar avatar, HUD dashboard (weather / markets / headlines /
system), Obsidian knowledge bases, interactive installer, `code-review-repo` skill.

Dashboard (merged): MARKETS box = top-5 tracked stocks + **METALS** (Gold `GC=F`,
Silver `SI=F`) + **VOLUME** (S&P 500 `^GSPC`, NASDAQ `^IXIC`), all via Yahoo v8.
NEWS = **NBC News + Fox** (CNN dropped: its RSS is dead/frozen since 2024).

Knowledge base (merged): single-vault mode. `OBSIDIAN_VAULT_PATH` points at one
Obsidian vault; tools `search_notes` / `read_note` / `list_notes` / `write_note`.
Mark's vault: `~/Obsidian/AI/AI-Victoria` (folders: Brain, Docker, Personal).

Docker Sandbox deployment: **DONE through Phase 2** (PRs #62, #63 merged).
- Working packed kit at `sbx/spec.yaml` (`kind: sandbox`, image `docker/sandbox-templates:shell-docker`).
- A **Python 3.11 venv** (via preinstalled `uv`) installs the FULL `requirements.txt`,
  so **ChromaDB semantic memory is ACTIVE** in the sandbox (was OFFLINE in Phase 1).
- Sandbox named `victoria`; repo staged at `~/sandboxes/victoria-ai`; vault mounted
  from `~/Obsidian/AI/AI-Victoria`; host Model Runner reached at
  `host.docker.internal:12434`; HUD published at **`http://127.0.0.1:8001`** (IPv4).
- Verified live: `/health` 200, chat (host Model Runner), knowledge base reads the
  vault, dashboard, and `Semantic memory initialised at data/chromadb`.
- `./deploy-sandbox.sh` reproduces the whole thing.

IN PROGRESS / NOT DONE:
- **Phase 3 (hardening): Q3 DONE; Q2 intentionally deferred (Phase 3 PR).** Q3
  (credentials) DONE: `resolve()` falls back to `os.environ`, and the sbx proxy
  injects the `github` secret as `GH_TOKEN` (resolves transparently). Q2 (egress)
  is WRITTEN into the kit (`network.allowedDomains`) but **INERT by decision (C)** —
  the org `NetworkAll` (`allow **`) overrides kit rules (verified: `example.com`
  still returns 200 inside the sandbox, even in the `sandbox:victoria` context).
  sbx egress governance is **org/team-scoped, not per-sandbox**, and all sandboxes
  share Mark's Docker identity, so there is NO way to harden only `victoria`.
  Activating egress means tightening the org-wide `NetworkAll` in Docker Home
  (affects every sandbox) — deliberately NOT done; the sandbox's hardware isolation
  is the security property we wanted. Kit block kept as documented target. For a
  tight runtime-only allowlist later, bake deps into a custom image (build-time
  egress otherwise breaks creation).
- **RAG Phase 1b: NOT started.** ChromaDB is active but only stores conversation
  turns (`semantic_memory.py`); there is no vault-note ingestion/retrieval yet.
- **AI-vault-as-memory (Phase 2 of knowledge): NOT started.**
- Committed `docs/screenshots/sbx-hud.png` shows `MEMORY: OFFLINE` (Phase 1);
  after Phase 2 it would show ACTIVE. Minor; optional re-capture.

## 3. Files Being Touched (exact paths)

```
~/victoria-ai/sbx/spec.yaml                         # THE sbx kit (kind: sandbox, uv+py3.11 full deps, uvicorn startup-background)
~/victoria-ai/deploy-sandbox.sh                     # sbx deploy runbook (stage->pack->run->publish->verify)
~/victoria-ai/SANDBOX-DEPLOYMENT.md                 # sbx guide + screenshot + gotchas + roadmap
~/victoria-ai/SECURITY-AUDIT.md                     # Phase-3 egress-hardening TARGET (allowlist table)
~/victoria-ai/docs/decisions-md.md                  # ADRs (newest at top of "## Decided"); sbx ADR dated 2026-07-20
~/victoria-ai/docs/screenshots/sbx-hud.png          # sandboxed HUD (2400x1500); stale MEMORY:OFFLINE
~/victoria-ai/victoria/dashboard/feeds.py           # fetch_stocks/fetch_metals/fetch_indices/fetch_markets (Yahoo v8)
~/victoria-ai/victoria/dashboard/store.py           # SUPPORTED_NEWS = nbcnews+foxnews; DEFAULTS; _load prunes dead sources
~/victoria-ai/victoria/knowledge/vaults.py          # KnowledgeBase; single-vault via OBSIDIAN_VAULT_PATH
~/victoria-ai/victoria/tools/knowledge_tools.py     # search/read/list/write_note tools
~/victoria-ai/victoria/core/semantic_memory.py      # ChromaDB layer (conversation turns); RAG-over-vault extends THIS
~/victoria-ai/victoria/core/conversation.py         # orchestrator; RAG retrieval wires in here
~/victoria-ai/victoria/config.py                    # OBSIDIAN_VAULT_PATH + obsidian_* settings; MODEL_RUNNER_URL
~/victoria-ai/victoria/vault/store.py               # Fernet vault; resolve() does ${vault:NAME} (add env fallback for Q3)
~/victoria-ai/requirements.txt                      # full deps (chromadb, faster-whisper, piper-tts, sounddevice, ...)
~/victoria-ai/.env                                  # native run: OBSIDIAN_VAULT_PATH=~/Obsidian/AI/AI-Victoria
~/victoria-ai/docs/build-ai-assistant/references/victoria-reference.md   # keep layer/endpoint/counts in sync (repo + ~/.claude copy)
~/sandboxes/victoria-ai                             # STAGED CLONE the sandbox mounts (MUST be under ~/sandboxes/**)
~/Obsidian/AI/AI-Victoria                           # the vault mounted into the sandbox
/tmp/victoria-kit.zip                               # packed kit output (regenerate: sbx kit pack sbx/ -o /tmp/victoria-kit.zip)
in-sandbox: /home/agent/venv                        # py3.11 venv the kit builds
in-sandbox: /tmp/victoria.log                       # uvicorn log inside the sandbox
```
Governance: `sbx` is managed by org `mmaxwelldemoorg` (remote-synced policies).
Active fs-mount allow rules: `~/sandboxes/**` and `~/Obsidian/**` (both required).

## 4. What's Been Tried That Failed

- **`sbx run --kit ./sbx-kit.yaml` on the raw YAML.** DO NOT REPEAT. Error:
  `INVALID: artifact zip … not a valid zip file`. sbx v0.35 kits are PACKED
  artifacts (`sbx kit pack <dir>` -> zip; dir needs `spec.yaml`). The old root
  `sbx-kit.yaml` was aspirational + wrong-schema; it was removed. Use `sbx/spec.yaml` + `sbx kit pack`.
- **Mounting `~/victoria-ai` or the vault directly.** DO NOT REPEAT. Error:
  `403 Forbidden: mount policy denied … no applicable policies`. Org governance
  locks mounts to allowed roots and does NOT delegate fs rules to local policy
  (`local-policy` fs rules are inactive: "corporate policy takes precedence").
  Fix: stage code under `~/sandboxes/**`; get an org fs-allow rule for the vault.
- **Vault rule `~/obsidian/**` (lowercase) for folder `~/Obsidian`.** DO NOT REPEAT.
  The policy matcher is CASE-SENSITIVE, so the capital-O path was denied. The rule
  must match exact case (`~/Obsidian/**`, now fixed by Mark).
- **`MODEL_RUNNER_URL=http://localhost:12434` inside the sandbox.** DO NOT REPEAT.
  `localhost` is the sandbox itself. The host Model Runner is
  `http://host.docker.internal:12434/engines/llama.cpp/v1` (confirmed: 200).
- **uvicorn as `sandbox.entrypoint`.** DO NOT REPEAT. The entrypoint is the
  interactive agent process; on detach (`-d`) it dies (procs: 0, health 000).
  A long-running service belongs in `commands.startup` with `background: true`.
- **`sbx exec … nohup uvicorn &` to run the service.** DO NOT REPEAT. The sandbox
  stops between exec calls (each exec transiently starts/stops it), so the process
  and any exec-installed deps do not persist. Bake deps + service into the kit.
- **`curl http://localhost:8001` from the host.** DO NOT REPEAT. Connection reset.
  The service is IPv4-only; `localhost` resolves to `::1` (IPv6) first. Use
  `127.0.0.1` for publish and curl (`sbx ports … --publish 127.0.0.1:8001:8000`).
- **Full `requirements.txt` on Python 3.14** (shell-docker default). DO NOT REPEAT.
  chromadb / faster-whisper / piper have no 3.14 wheels. Use `uv venv --python 3.11`.
- **`uv venv` / `uv pip install` run as root in the kit.** DO NOT REPEAT. Error at
  startup: `/home/agent/venv/bin/python: Permission denied` (venv + uv-managed
  interpreter were root-owned, agent-run startup could not execute them). The `uv`
  install steps need `user: "1000"` (agent); apt steps stay `user: "0"`.
- **`import sounddevice` in the sandbox.** EXPECTED FAILURE, not a bug:
  `PortAudioError: … Can't connect to server`. A headless microVM has no audio
  device, so native mic/wake-word is impossible in ANY sandbox. It is lazy-imported
  so boot is unaffected; browser voice (Whisper STT + Piper TTS) works. Do not try
  to "fix" native mic in the sandbox.
- **`git commit -m "...backticks..."`.** DO NOT REPEAT. Backticks inside a
  double-quoted `-m` are command-substituted by the shell and silently drop text.
  Use `git commit -F -` with a `<<'MSG'` quoted heredoc.

## 5. What to Do Next

Resume + verify the sandbox (do this first):
1. Confirm host Model Runner: `curl -fsS http://localhost:12434/engines/llama.cpp/v1/models` (if down: `docker desktop enable model-runner --tcp=12434`).
2. Redeploy: `cd ~/victoria-ai && ./deploy-sandbox.sh` (idempotent: stages the clone, packs `sbx/`, runs, publishes `127.0.0.1:8001`).
3. Verify: `curl -4 -sS http://127.0.0.1:8001/health` (expect 200); `sbx exec victoria -- grep -i "semantic memory" /tmp/victoria.log` (expect "Semantic memory initialised").

Phase 3 hardening (the Phase 3 PR landed the below):
4. Egress (Q2): WRITTEN-BUT-INERT, deliberately deferred (decision C). The
   `network.allowedDomains` block is in `sbx/spec.yaml`, but the org `NetworkAll`
   (`allow **`) overrides it (verified: `example.com` → 200 inside the sandbox).
   sbx egress governance is **org/team-scoped, not per-sandbox** — and all
   sandboxes share Mark's Docker identity — so hardening ONLY `victoria` is not
   supported. The only real lever is tightening the org-wide `NetworkAll` in Docker
   Home (Governance API), which flips EVERY sandbox to default-deny; not done on
   purpose (sandbox hardware isolation is the security property we wanted). If ever
   activated: first bake deps into a custom base image (build-time egress otherwise
   breaks sandbox creation), then verify `sbx exec victoria -- curl … example.com`
   FAILS while chat + dashboard still work. Full detail in `SECURITY-AUDIT.md`.
5. Credentials (Q3): DONE. `victoria/vault/store.py` `resolve()` now falls back to
   `os.environ` (vault wins if both set; missing name still left intact). The sbx
   proxy injects the `github` secret as env `GH_TOKEN` (resolves transparently);
   `anthropic` is OAuth/proxy-edge, so escalation auth is handled at the proxy.
   +2 tests.

RAG Phase 1b (queued, not started; branch -> PR):
6. Add a SEPARATE ChromaDB collection for vault docs (distinct from the `conversations` collection in `semantic_memory.py`). Ingest the Obsidian vault markdown, chunked by heading, storing note-path metadata for citations. Embedding model: local `sentence-transformers/all-MiniLM-L6-v2` (see `docs/decisions-md.md` Open Q4; confirm with Mark). Re-index on startup + on `write_note` + a `reindex` tool.
7. Wire retrieval into `victoria/core/conversation.py`: pull top-k vault chunks into the prompt with citations, alongside existing semantic recall.
8. Add tests (`tests/test_*`), keep the count current in README/claude-md.md/victoria-reference.md, add an ADR.

Then (later): knowledge Phase 2 (persist Victoria's profile/learned facts as markdown in the AI vault); Obsidian Local REST API / MCP (Phase 3 of knowledge).
