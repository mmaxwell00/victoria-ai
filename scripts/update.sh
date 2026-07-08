#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Update Victoria — native macOS setup (uvicorn in a .venv).
#
#   ~/victoria-ai/scripts/update.sh
#
# Pulls the latest code, refreshes dependencies, restarts the server, and
# health-checks it. Your .env, data/ (memory + vault), and skills/ are left
# untouched. Safe to re-run.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
PORT="${VICTORIA_PORT:-8000}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

say "Updating Victoria in $DIR"
# Only block on modified *tracked* files (they could conflict with the pull).
# Untracked files — your own skills, mcp.json, etc. — are fine and left alone.
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "You have uncommitted changes to tracked files — commit or stash them first, then re-run." >&2
  exit 1
fi
git pull --ff-only origin main

if [ -x .venv/bin/pip ]; then
  say "Refreshing dependencies"
  # Mark's Mac enforces hashed pip installs; the repo's requirements aren't
  # hashed, so default the override off. Harmless on a stock Mac.
  PIP_REQUIRE_HASHES="${PIP_REQUIRE_HASHES:-false}" .venv/bin/pip install -q -r requirements.txt
else
  echo "No .venv found — create one first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

say "Restarting the server"
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
