## Victoria Sandbox Kit — Deployment Guide

### What's Ready

✓ **sbx-kit.yaml** — Production sandbox kit with:
  - All dependencies (Python 3.11, ffmpeg, Node.js, portaudio)
  - Voice model download (Piper en_GB-jenny_dioco)
  - Environment configuration (Model Runner, data paths, escalation)
  - Credential injection (API keys never written to sandbox)
  - Whitelisted network domains (default deny, explicit allow)

✓ **deploy-sandbox.sh** — Pre-flight checklist that:
  - Verifies sbx CLI installed
  - Checks Docker Desktop and Model Runner
  - Confirms models are pulled
  - **Creates the required sbx policy rule** ← CRITICAL
  - Validates YAML syntax

✓ **SECURITY-AUDIT.md** — Detailed audit of:
  - Network rules (what's allowed and why)
  - Optional endpoints (ElevenLabs, Telegram)
  - MCP fetch security trade-offs
  - Data privacy (what stays on host)

### Security Implementation

**Network Model:**
- `allowedDomains` — explicit whitelist (Victoria's tools, Claude API, package managers)
- `deniedDomains` — block telemetry/analytics
- Default: **deny everything else**

**Endpoints Allowed:**
| Service | Domain | Reason |
|---------|--------|--------|
| Model Runner | `localhost:12434` | Local LLM (requires `sbx policy` on host) |
| Claude API | `api.anthropic.com` | Escalation |
| Web search | `duckduckgo.com`, `*.duckduckgo.com` | Built-in tool (ddgs) |
| Weather | `wttr.in` | Built-in tool + dashboard weather |
| Markets | `query1/2.finance.yahoo.com` | Dashboard stocks box |
| Headlines | `feeds.nbcnews.com`, `moxie.foxnews.com` | Dashboard news box (RSS) |
| GitHub | `github.com`, `api.github.com` | Skill import + MCP |
| Models | `huggingface.co` | Piper TTS + model downloads |
| Packages | `registry.npmjs.org`, `pypi.org` | Dependencies |

**Credentials (Injected from Host):**
- `ANTHROPIC_API_KEY` — Claude escalation
- `CLAUDE_CODE_OAUTH_TOKEN` — Alternative Claude auth
- `VICTORIA_VAULT_KEY` — Encrypted vault
- `TELEGRAM_BOT_TOKEN` — Telegram interface
- `GitHub_Victoria` — MCP GitHub server

None of these are written to `.env` or exposed inside the sandbox. They're proxied from the host.

### Pre-Deployment Checklist

1. **Run the checklist script:**
   ```bash
   bash deploy-sandbox.sh
   ```
   This verifies all prerequisites and **creates the required `sbx policy` rule**.

2. **Verify Docker Desktop:**
   - Docker running
   - Model Runner enabled (Settings → Features in development)
   - At least one model pulled: `docker model ls`

3. **Check Model Runner accessibility:**
   ```bash
   curl http://localhost:12434/engines/llama.cpp/v1/models
   ```

4. **Verify policy was created:**
   ```bash
   sbx policy list
   ```
   Should show a rule allowing `localhost:12434`.

### Deployment

```bash
# Launch the sandbox
sbx run --kit ./sbx-kit.yaml
```

This creates a microVM, installs dependencies (~5-10 min on first run), downloads the Piper model, and starts Victoria API on `:8000`.

### Inside the Sandbox — Verification

```bash
# Browser
http://localhost:8000

# Check Model Runner connectivity
curl http://localhost:12434/engines/llama.cpp/v1/models

# Test the chat API
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What time is it?",
    "user_id": "test"
  }'

# Check health
curl http://localhost:8000/health
```

### Optional: Enable Premium Features

**ElevenLabs Text-to-Speech:**
```bash
# On host
sbx policy allow network api.elevenlabs.io

# In .env
TTS_ENGINE=elevenlabs
ELEVENLABS_API_KEY=sk_...
```

**Telegram Bot Interface:**
```bash
# On host
sbx policy allow network api.telegram.org

# In .env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefghijklmnop
```

**Additional MCP Servers:**
If you add MCP servers to `mcp.json` that need external access:
```bash
# Example: custom API server
sbx policy allow network "api.your-domain.com"

# Example: broader web access (not recommended)
sbx policy allow network "*.github.io"
```

### Sandbox Lifecycle

**List all sandboxes:**
```bash
sbx list
```

**View logs:**
```bash
sbx logs <sandbox-id>
```

**Stop a sandbox:**
```bash
sbx stop <sandbox-id>
```

**Reuse a sandbox:**
Sandboxes persist — running `sbx run --kit ./sbx-kit.yaml` again will reuse the same VM unless mount paths change. To force a new one:
```bash
sbx rm <sandbox-id>
sbx run --kit ./sbx-kit.yaml
```

### Data Persistence

These directories persist on the host and survive sandbox lifecycle:

| Path | Content | Persists |
|------|---------|----------|
| `/workspace/victoria-ai/data/victoria.db` | Conversations, user profiles | ✓ Yes |
| `/workspace/victoria-ai/data/chromadb/` | Semantic memory | ✓ Yes |
| `/workspace/victoria-ai/models/` | Downloaded TTS model | ✓ Yes |
| `/workspace/victoria-ai/skills/` | Created/imported skills | ✓ Yes |

**Encryption:** If you set `VICTORIA_VAULT_KEY`, the vault (`data/vault.enc`) is encrypted at rest.

### Troubleshooting

**Model Runner connection refused:**
```bash
# On host
docker desktop enable model-runner --tcp=12434

# Or verify from sandbox
curl http://localhost:12434/engines/llama.cpp/v1/models
```

**Sandbox can't reach external APIs (e.g., Claude):**
- Check policy: `sbx policy list`
- Add domain: `sbx policy allow network api.anthropic.com`
- View logs: `sbx logs <sandbox-id> | grep -i "blocked\|denied"`

**Installation timeout or network errors:**
- Increase timeout: `sbx run --timeout 600 --kit ./sbx-kit.yaml`
- Check network: `curl https://huggingface.co` (Piper model download)

**Credentials not working (API key not injected):**
- Verify credentials in kit: `grep -A 5 "credentials:" sbx-kit.yaml`
- Check host env: `echo $ANTHROPIC_API_KEY` (if set globally)
- Check keychain (macOS): `security find-generic-password -l "Docker Sandbox" 2>/dev/null`

### MCP Configuration for Sandbox

Update `mcp.json` to use sandbox-relative paths:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
      "readOnly": true
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${vault:GitHub_Victoria}" },
      "readOnly": true
    },
    "fetch": {
      "command": "python",
      "args": ["-m", "mcp_server_fetch"],
      "readOnly": true
    }
  }
}
```

**Note on fetch server:** It can read any whitelisted URL but is restricted by network policy. Disable it in `mcp.json` if you want maximum security.

### Next Steps

1. Run: `bash deploy-sandbox.sh`
2. Launch: `sbx run --kit ./sbx-kit.yaml`
3. Test: `http://localhost:8000` in browser
4. Optional: `sbx policy allow network api.elevenlabs.io` (if using premium TTS)
5. Monitor: `sbx logs <sandbox-id>` for any errors

---

**Resources:**
- [Kit reference](https://docs.docker.com/ai/sandboxes/customize/kits/)
- [Sandbox security](https://docs.docker.com/ai/sandboxes/security/)
- [Network policies](https://docs.docker.com/ai/sandboxes/governance/concepts/)
