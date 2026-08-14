## Victoria Sandbox — Network Security Audit

> **Status (verified 2026-08-04): the egress allowlist is now LIVE — default-deny
> is in force.** This supersedes the earlier "INERT / leave egress broad" status.
> The org policy `NetworkAll` (`allow **`) that used to override kit rules is **no
> longer present**; `sbx policy ls` now shows the kit's own policy applying to this
> sandbox:
>
> ```
> ffb208da-…  kit  sandbox:victoria  network: 34 allow
> ```
>
> So the `network.allowedDomains` block in [`sbx/spec.yaml`](sbx/spec.yaml) is the
> effective policy. Confirmed empirically from inside the sandbox:
>
> | Destination | Result |
> |---|---|
> | `wttr.in`, `github.com` (allow-listed) | **200** |
> | `query1.finance.yahoo.com` (allow-listed) | **429** — allowed through; Yahoo rate-limit |
> | `https://example.com` (**not** listed) | **403** — denied by the proxy |
>
> **The allowlist is therefore load-bearing.** Any new feature that calls a host not
> on the list will fail with **403** until the domain is added to `sbx/spec.yaml` and
> the sandbox redeployed. Egress now runs through a Docker Sandboxes proxy at
> `gateway.docker.internal:3128` (see the interception note below).
>
> Victoria still also relies on **hardware / process / filesystem isolation** from
> the host; egress lockdown is now an additional layer rather than a future step.
> The host Model Runner is reached at `host.docker.internal:12434` (not `localhost`).

> **Host-directed TLS is intercepted (ssl-bumped), and only `:12434` is reachable.**
> `host.docker.internal` is on the allowlist, but that does not make arbitrary host
> ports usable. Measured from inside the sandbox:
>
> - `http://host.docker.internal:12434` → **200** (the Model Runner integration).
> - `http://host.docker.internal:8787` (Claude bridge) → **403**; any other host port
>   tested (e.g. `:9911`) → **403** proxied, **000** direct.
> - `https://host.docker.internal:8787` → the proxy grants the tunnel
>   (`CONNECT … → HTTP/1.0 200 OK`) and then **presents its own certificate**:
>   `subject: O=Docker Sandboxes; CN=localhost`, `issuer: … CN=Docker Sandboxes Proxy CA`.
>
> That last point is why **mTLS to the host bridge cannot work from the sandbox**: a
> bumping proxy terminates TLS, so Victoria's *client* certificate never reaches the
> bridge (which requires one), and the server certificate can never match the bridge
> CA. Victoria surfaces it as `CERTIFICATE_VERIFY_FAILED: self-signed certificate in
> certificate chain`. Non-443 CONNECT is *not* the blocker — a tunnel to
> `github.com:8787` is granted cleanly; the interception is specific to host-directed
> traffic. See "Claude escalation" below.

## Activation — an org-wide policy change (no per-sandbox option)

sbx network governance is scoped **by org or by team (user membership) — not per
sandbox.** All of these sandboxes run under one Docker identity, so there is **no
supported way to harden only `victoria`** while other sandboxes stay broad. Egress
can only be tightened in **Docker Home** (or the Governance API), not the local
`sbx` CLI. Realistic paths (both affect more than just Victoria):

1. **Tighten `NetworkAll` org-wide** — replace its `allow **` with a granular
   allowlist. This enforces default-deny + allowlist for **every** sandbox in the
   org, so each one then needs its own hosts allow-listed (via its kit or org
   policy) or it loses egress. Choose this only if org-wide default-deny is the goal.
2. **Team-scoped hardened policy** — only isolates `victoria` if it runs under a
   **separate identity/team**; that's extra identity setup, not in place today.

**Superseded (2026-08-04).** Decision (C) — "leave egress broad" — is no longer the
state of the world, and no longer requires the org-wide change described above. The
`NetworkAll` (`allow **`) rule is gone, so the kit's allowlist now *is* the policy
for `sandbox:victoria`: **default-deny with 34 allowed hosts**, per-sandbox, with no
blast radius across other sandboxes. The outcome path 1 was meant to achieve arrived
without us tightening anything org-wide.

## Claude escalation from the sandbox — denied, on purpose

Escalation from **inside the sandbox is OFF**, and that is the intended posture:
network policy is the control point, so Claude access is something the policy grants
or denies rather than something Victoria can arrange for herself.

- The **host bridge is healthy** — called from the host it returns a real Claude
  answer (`http=200`), the launchd agent runs, certs verify, and the global
  `anthropic` OAuth is configured. Escalation works for a **native/host** Victoria.
- From the **sandbox** it is denied by the egress rules above (host-directed TLS is
  ssl-bumped; only `:12434` is permitted). Victoria falls back to the local model and
  says so; no silent failure.
- **Rejected: a filesystem side-channel** (Victoria writing request files into the
  mounted repo for a host watcher to execute). It would work, and it would be a
  **covert channel** — reaching Claude without appearing in network policy at all,
  defeating the very control this document is about. Not built, deliberately.
- **If escalation is ever wanted inside the sandbox**, the policy-respecting route is
  **`api.anthropic.com`** (already on the allowlist) with the credential
  **proxy-injected via `sbx secret`** — the same mechanism that hands the `claude`
  sandbox a `proxy-managed` sentinel instead of the real token. That keeps egress
  visible to network policy *and* keeps the credential out of the VM. Not
  implemented; recorded as the correct direction if the need arises.

Verify the allowlist is in force (from the host):

```bash
# a non-allowlisted host is DENIED (403 — this is the current, expected result):
sbx exec victoria -- curl -sS -m 6 -o /dev/null -w '%{http_code}\n' https://example.com
# allowlisted paths still work — chat + dashboard:
curl -4 -sS http://127.0.0.1:8001/health
```

**Model traffic deliberately bypasses the proxy.** The kit sets
`NO_PROXY`/`no_proxy` to include `host.docker.internal`, because sbx's own defaults
cover `localhost` and the gateway but not the host — so every local-model call was
being relayed through the inspecting proxy. Measured 2026-08-13: identical code and
model, questions spaced 20s apart — **2.0s/turn on the host vs 6.9-8.8s from inside
the sandbox**, fixed to 2.45-2.56s by the bypass. `:12434` is the one host port sbx
already permits directly, so this removes latency *and* a pointless interception
point; it does not widen what the sandbox can reach.

**Build-time egress caveat.** The kit installs its full dependency set at
sandbox-create time (apt → ffmpeg/PortAudio; `uv` → a managed CPython 3.11 from
GitHub; pip → PyPI; model/tooling pulls → Hugging Face). A strict *runtime*
allowlist that omits those build hosts would break creation, so the list below
includes them (`pypi.org`, `files.pythonhosted.org`, the **Ubuntu** apt mirrors,
`download.docker.com`, `*.githubusercontent.com` for GitHub *release assets*,
`**.hf.co`, …). Four of those were missing and a cold deploy genuinely failed on
2026-08-05 — see the cold-deploy gotcha in SANDBOX-DEPLOYMENT.md. For a tighter runtime-only
posture, **bake the dependencies into a custom base image** so the running
sandbox needs zero build-time egress, then trim the allowlist to runtime hosts.

### Problems Found & Fixed

1. **Wrong Kit Format** ✓ FIXED
   - Was: `allow: [...]` / `deny: [...]`
   - Now: `allowedDomains: [...]` / `deniedDomains: [...]`
   - This is the official kit spec format

2. **Incorrect DuckDuckGo Endpoint** ✓ FIXED
   - Wrong: `api.duckduckgo.com` (the Instant Answer API — not what `ddgs` uses)
   - Correct: `duckduckgo.com` + `*.duckduckgo.com` (the `ddgs` library hits html./lite./links.)
   - Victoria's `web_search` tool queries the API endpoint

3. **Model Runner host** ✓ CORRECTED
   - The sandbox reaches the host Model Runner at **`host.docker.internal:12434`**,
     NOT `localhost:12434` (localhost is the sandbox itself).
   - The allowlist entry is therefore `host.docker.internal`. **Superseded detail:**
     this used to note that no policy step was needed "while the org `NetworkAll`
     allow is active" — that rule is gone as of 2026-08-04 and the kit allowlist is
     now the enforced policy. The deploy script still creates no policy rules; the
     kit carries them.

4. **MCP Path References** ✓ FIXED
   - Old mcp.json had a hardcoded host path (e.g. `<your-home>/victoria-mcp-demo`)
   - Inside sandbox, paths must be relative to `/workspace`
   - Updated SANDBOX-DEPLOYMENT.md with corrected config

5. **Optional Services Not Configured** ✓ DOCUMENTED
   - ElevenLabs, Telegram not in default whitelist (they're optional)
   - Added instructions to enable them via `sbx policy allow`

### Security Decisions in the Kit

| Endpoint | Allowed | Reason | When Needed |
|----------|---------|--------|------------|
| `host.docker.internal` | ✓ | Docker Model Runner on the host (essential) | Always |
| `api.anthropic.com` | ✓ | Claude escalation | When you click "yes" to escalate (optional) |
| `duckduckgo.com` + `*.duckduckgo.com` | ✓ | Web search (built-in, ddgs) | `html.`/`lite.`/`links.` — NOT api.duckduckgo.com |
| `wttr.in` | ✓ | Weather tool + dashboard weather box | When asking about weather / dashboard on |
| `query1/2.finance.yahoo.com` | ✓ | Dashboard markets box (stock prices) | When the dashboard tracks stocks |
| `feeds.nbcnews.com`, `moxie.foxnews.com` | ✓ | Dashboard headlines box (NBC News / Fox RSS) | When the dashboard tracks news |
| `github.com` + `api.github.com` | ✓ | Skill import + GitHub MCP | When importing skills or using GitHub tool |
| `huggingface.co` | ✓ | Model downloads (setup phase) | During initial sandbox creation |
| `registry.npmjs.org` | ✓ | NPM packages (setup + runtime) | During dependency install |
| `pypi.org` | ✓ | Python packages (setup phase) | During pip install |
| `api.elevenlabs.io` | ✗ | Premium TTS (optional) | Only if `TTS_ENGINE=elevenlabs` |
| `api.telegram.org` | ✗ | Telegram bot (optional) | Only if running victoria-telegram |
| `chroma-onnx-models.s3.amazonaws.com` | ✓ | ChromaDB's default embedding model (all-MiniLM-L6-v2, ONNX) | First semantic-memory query. **Blocking it does NOT fail loudly** — Chroma retries the download every query, fails its SHA256 check on the proxy's 403 page, and returns nothing, so recall silently dies while `/health` still says `semantic_memory: true` (2026-08-08) |
| `ports/archive/security.ubuntu.com` (+`:80`) | ✓ | apt — ffmpeg + PortAudio for voice | Sandbox create. The base image moved to **Ubuntu**, so apt uses `ports.ubuntu.com`, NOT `deb.debian.org` |
| `download.docker.com` (+`:80`) | ✓ | The image ships a Docker CE apt repo | Sandbox create. Blocking it makes `apt-get update` exit non-zero, which silently skips the ffmpeg install |
| `*.githubusercontent.com` | ✓ | GitHub **release assets** — `uv`'s managed CPython 3.11 | Sandbox create. Missing it = 403 → no venv → creation aborts |
| `hf.co`, `**.hf.co`, `**.huggingface.co` | ✓ | Hugging Face model blobs, incl. the **Xet CDN** on rotating hosts | Whisper/voice model download. NOTE `*` matches ONE label — `*.hf.co` does not match `us.aws.cdn.hf.co`; hence `**` |
| `Everything else` | ✗ | Default deny — security first | N/A |

### Critical Setup Steps

**Before running `sbx run --kit /tmp/victoria-kit.zip -d victoria ~/sandboxes/victoria-ai ~/Obsidian/AI/AI-Victoria`:**

1. **Run the deployment checklist:**
   ```bash
   bash deploy-sandbox.sh
   ```
   This automatically creates the required policy rule: `sbx policy allow network localhost:12434`

2. **Verify Docker Model Runner is ready:**
   ```bash
   docker desktop enable model-runner --tcp=12434
   docker model pull ai/qwen2.5
   curl http://localhost:12434/engines/llama.cpp/v1/models
   ```

3. **Check the policy was created:**
   ```bash
   sbx policy list
   ```
   Should show a rule allowing `localhost:12434`.

### What Gets Isolated / What Gets Leaked

| Data | Location | Security Level |
|------|----------|---|
| API keys (Anthropic, GitHub, Telegram) | Injected as credentials from host | 🔐 High — never written to sandbox |
| Conversations/history | `/workspace/victoria-ai/data/victoria.db` | 🔐 High — encrypted if vault key is set |
| Downloaded models | `/workspace/victoria-ai/models/` | 🟡 Medium — cached on host |
| Skills | `/workspace/victoria-ai/skills/` | 🟡 Medium — stored as Markdown on host |
| Network traffic | External (whitelisted domains only) | 🔐 High — only approved endpoints reachable |
| Sandbox logs | `sbx logs <sandbox-id>` | 🟡 Medium — visible to user on host |
| Secrets in .env | Inside sandbox (if mounted) | 🔴 Low — readable by any process in sandbox |

**Recommendation:** Don't mount `.env` with secrets into the sandbox. Use credential injection instead (kit's `credentials:` block).

### Optional: Add Missing Endpoints

**For Premium Text-to-Speech (ElevenLabs):**
```bash
# Host: add network policy
sbx policy allow network api.elevenlabs.io

# Then set in .env or kit
TTS_ENGINE=elevenlabs
ELEVENLABS_API_KEY=sk_...
```

**For Telegram Bot Interface:**
```bash
# Host: add network policy
sbx policy allow network api.telegram.org

# Then set in .env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefghijklmnopqrstuvwxyz
```

**For Custom MCP Servers:**
Add domains as needed:
```bash
sbx policy allow network "api.your-company.com"
sbx policy allow network "*.github.io"
```

### MCP Fetch Server Security Trade-off

Your `mcp.json` includes a `fetch` server that can read any HTTP(S) URL. With the kit's network restrictions:
- It can only read from whitelisted domains
- It cannot make arbitrary external requests
- **Recommended:** Disable it for maximum security:
  ```json
  // Comment out or remove the fetch server
  // "fetch": { ... }
  ```

If you need it, either:
- Keep it disabled and use Victoria's built-in `web_search` tool instead
- Whitelist specific domains you trust: `sbx policy allow network "*.example.com"`
- Enable broad access (not recommended): `sbx policy allow network "*.com"`

### Testing Connectivity

Once the sandbox is running:

```bash
# Model Runner
curl http://localhost:12434/engines/llama.cpp/v1/models

# Claude API (if escalation is configured)
curl -I https://api.anthropic.com

# Weather tool
curl wttr.in/London

# Check blocked connections in logs
sbx logs <sandbox-id> | grep -i "blocked\|denied\|connection refused"
```

### Dependency vulnerabilities (SCA) — tracked

> **Scan:** `trivy fs --scanners vuln` over the resolved dependency tree, 2026-07-28.
> Victoria's installed packages returned **one** finding; everything else clean.

#### CVE-2026-45829 · `chromadb` — CRITICAL — TRACKED (no fix available)

- **Package:** `chromadb==1.5.9` (semantic memory). Advisory:
  <https://avd.aquasec.com/nvd/cve-2026-45829> — arbitrary code execution via
  **pre-authentication** code injection. Status: *affected*; **no patched version
  published** as of this scan, so there is nothing to bump to yet.
- **Not exploitable in Victoria today — and why:** the vulnerable surface is the
  ChromaDB **server** (`chroma run` — its HTTP API + auth layer). Victoria uses
  chromadb **embedded**: an in-process `PersistentClient` over `data/chromadb`.
  **No chroma server is started, bound, or published**, so the pre-auth network
  path does not exist. In the sandbox the process is hardware-isolated and only the
  HUD (`:8000` → `127.0.0.1:8001`) is published — no chroma port at all.
- **Invariant to keep (the mitigation):** embedded `PersistentClient` **only** —
  never `chroma run`, `chromadb.HttpClient`, or `Settings(chroma_server_*)`, and
  never publish a chroma port. If any of those change, this CVE becomes live.
- **Exit condition:** watch chromadb releases; the moment a fixed version ships,
  bump `chromadb` in `requirements.txt` and re-run the scan (below).

**Headroom evaluation (same scan).** Surfaced while vetting `headroomlabs-ai/headroom`
for adoption. Its *full* lock flags 11 (1 CRITICAL `chromadb` via an optional extra +
10 HIGH — stale-lock `gitpython`/`json-repair`, already fixed upstream); Rust `Cargo.lock`
was clean. A **real core-only `pip install headroom-ai`** (56 packages) rescanned at
**0 vulns** — so core-only adoption introduces **no new CVE exposure**; only its optional
extras pull the flagged packages.

**Re-run the SCA scan** (host + Docker):

```bash
mkdir -p /tmp/victoria-sca && .venv/bin/pip freeze > /tmp/victoria-sca/requirements.txt
docker run --rm -v /tmp/victoria-sca:/src aquasec/trivy:latest fs --scanners vuln /src
```

### Audit Checklist

- [ ] Kit uses `allowedDomains` / `deniedDomains` (not `allow` / `deny`)
- [ ] `localhost:12434` is in the allowed list
- [ ] `sbx policy allow network localhost:12434` has been run (deploy script does this)
- [ ] Docker Model Runner is enabled and accessible
- [ ] All optional endpoints are documented (ElevenLabs, Telegram)
- [ ] MCP fetch server decision made (enable or disable)
- [ ] Credentials are injected, not mounted in .env
- [ ] Network logs are monitored for blocked connections
- [ ] Test connectivity before deploying to production
- [ ] SCA scan of dependencies run (`trivy fs`); any unpatched CVEs tracked in "Dependency vulnerabilities (SCA)" above

### Next Steps

1. **Run the deployment checklist:**
   ```bash
   bash deploy-sandbox.sh
   ```

2. **Launch the sandbox:**
   ```bash
   sbx run --kit /tmp/victoria-kit.zip -d victoria ~/sandboxes/victoria-ai ~/Obsidian/AI/AI-Victoria
   ```

3. **Verify connectivity inside the sandbox:**
   ```bash
   # Inside sandbox shell
   curl http://localhost:12434/engines/llama.cpp/v1/models
   curl -I https://api.anthropic.com
   ```

4. **Test Victoria API:**
   ```bash
   curl -X POST http://localhost:8000/v1/chat \
     -H "Content-Type: application/json" \
     -d '{"message":"what time is it?","user_id":"test"}'
   ```

5. **Add optional domains as needed:**
   ```bash
   sbx policy allow network api.elevenlabs.io
   sbx policy allow network api.telegram.org
   ```

---

**Resources:**
- [Docker Sandbox Kit Reference](https://docs.docker.com/ai/sandboxes/customize/kits/)
- [Sandbox Security Model](https://docs.docker.com/ai/sandboxes/security/)
- [Network Governance](https://docs.docker.com/ai/sandboxes/governance/concepts/)
- [Credential Injection Best Practices](https://docs.docker.com/ai/sandboxes/security/credentials/)
