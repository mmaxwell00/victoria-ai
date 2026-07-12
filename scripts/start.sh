#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Start Victoria — native macOS setup (uvicorn in a .venv).
#
#   ~/victoria-ai/scripts/start.sh
#
# Self-heals the Docker Model Runner first (so the model dropdown is never
# empty after a Docker/Mac restart), then launches the server and health-checks
# it. Safe to re-run — restarts a running server.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
PORT="${VICTORIA_PORT:-8000}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# Make sure the local model backend is reachable (non-fatal — Claude/Ollama may
# still serve, and the app degrades gracefully).
"$DIR/scripts/ensure-model-runner.sh" || say "Model Runner not reachable — continuing anyway."

# Claude escalation auth preflight (non-blocking). The local model works without
# this; escalation to Claude just needs the CLI logged in. We only check that a
# credential is *present* (cheap, no token spend) — a stale one is caught at
# runtime with a clear error.
_claude_authed() {
  [ -n "${CLAUDE_CLI_OAUTH_TOKEN:-}" ] && return 0
  grep -qE '^CLAUDE_CLI_OAUTH_TOKEN=.+' "$DIR/.env" 2>/dev/null && return 0
  security find-generic-password -s "Claude Code-credentials" >/dev/null 2>&1 && return 0
  return 1
}
CLAUDE_BIN="${CLAUDE_CLI_COMMAND:-claude}"
if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
  say "Claude escalation: '$CLAUDE_BIN' not on PATH — Victoria runs locally; install the Claude Code CLI to enable it."
elif _claude_authed; then
  say "Claude escalation: credentials found ✓ (if it still 401s, refresh with scripts/claude-login.sh)"
else
  printf '\033[1;33m==>\033[0m Claude escalation not logged in (Victoria still runs locally).\n'
  printf '    To enable "ask Claude when the local model is stuck", do one of:\n'
  printf '      • claude                # log in with your Claude subscription (one-time)\n'
  printf '      • claude setup-token    # then add CLAUDE_CLI_OAUTH_TOKEN=... to .env (best for an always-on server)\n'
  printf '    Or just run: scripts/claude-login.sh\n'
fi

if [ ! -x .venv/bin/uvicorn ]; then
  echo "No .venv found — create one: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

say "Starting Victoria"
pkill -f "uvicorn victoria.main:app" 2>/dev/null || true
sleep 2
nohup .venv/bin/uvicorn victoria.main:app --host 0.0.0.0 --port "$PORT" > /tmp/victoria.log 2>&1 &

printf '==> Waiting for Victoria'
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
    echo
    say "Victoria is up → http://localhost:$PORT  (hard-refresh the tab: Cmd+Shift+R)"
    exit 0
  fi
  printf '.'; sleep 1
done
echo
echo "Victoria didn't come up — check: tail -20 /tmp/victoria.log" >&2
exit 1
