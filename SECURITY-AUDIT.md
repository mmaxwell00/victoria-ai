## Victoria Sandbox — Network Security Audit

### Problems Found & Fixed

1. **Wrong Kit Format** ✓ FIXED
   - Was: `allow: [...]` / `deny: [...]`
   - Now: `allowedDomains: [...]` / `deniedDomains: [...]`
   - This is the official kit spec format

2. **Incorrect DuckDuckGo Endpoint** ✓ FIXED
   - Wrong: `api.duckduckgo.com` (the Instant Answer API — not what `ddgs` uses)
   - Correct: `duckduckgo.com` + `*.duckduckgo.com` (the `ddgs` library hits html./lite./links.)
   - Victoria's `web_search` tool queries the API endpoint

3. **Model Runner Access Incomplete** ✓ FIXED
   - Kit allows `localhost:12434`
   - **BUT** requires a host-level policy rule to work
   - Deploy script now creates this automatically

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
| `localhost:12434` | ✓ | Docker Model Runner (local, essential) | Always — requires `sbx policy allow` rule |
| `api.anthropic.com` | ✓ | Claude escalation | When you click "yes" to escalate (optional) |
| `duckduckgo.com` + `*.duckduckgo.com` | ✓ | Web search (built-in, ddgs) | `html.`/`lite.`/`links.` — NOT api.duckduckgo.com |
| `wttr.in` | ✓ | Weather tool (built-in) | When asking about weather |
| `github.com` + `api.github.com` | ✓ | Skill import + GitHub MCP | When importing skills or using GitHub tool |
| `huggingface.co` | ✓ | Model downloads (setup phase) | During initial sandbox creation |
| `registry.npmjs.org` | ✓ | NPM packages (setup + runtime) | During dependency install |
| `pypi.org` | ✓ | Python packages (setup phase) | During pip install |
| `api.elevenlabs.io` | ✗ | Premium TTS (optional) | Only if `TTS_ENGINE=elevenlabs` |
| `api.telegram.org` | ✗ | Telegram bot (optional) | Only if running victoria-telegram |
| Everything else | ✗ | Default deny — security first | N/A |

### Critical Setup Steps

**Before running `sbx run --kit ./sbx-kit.yaml`:**

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

### Next Steps

1. **Run the deployment checklist:**
   ```bash
   bash deploy-sandbox.sh
   ```

2. **Launch the sandbox:**
   ```bash
   sbx run --kit ./sbx-kit.yaml
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
