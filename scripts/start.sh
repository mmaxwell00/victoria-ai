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
