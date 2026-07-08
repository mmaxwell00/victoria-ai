#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Ensure the Docker Model Runner host-TCP endpoint is up (self-heal).
#
# Native Victoria reaches the local model over http://localhost:<port>. That
# host-TCP port can silently drop when Docker Desktop or the Mac restarts —
# the runner still runs internally, but the port isn't bound, so the model
# dropdown goes empty. This detects that and re-binds it (a plain re-enable,
# then a disable→enable cycle if needed). Idempotent; a no-op when already up.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${MODEL_RUNNER_TCP:-12434}"

# Prefer the port from .env's MODEL_RUNNER_URL if present.
if [ -f "$DIR/.env" ]; then
  url="$(grep -E '^MODEL_RUNNER_URL=' "$DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  p="$(printf '%s' "$url" | sed -n 's#.*://[^:/]*:\([0-9][0-9]*\).*#\1#p')"
  [ -n "$p" ] && PORT="$p"
fi

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
up()  { curl -sf -m 3 "http://localhost:$PORT/engines/llama.cpp/v1/models" >/dev/null 2>&1; }

# 0. Docker must be running.
if ! docker info >/dev/null 2>&1; then
  say "Starting Docker Desktop…"
  open -a Docker 2>/dev/null || true
  for _ in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then break; fi
    sleep 2
  done
  if ! docker info >/dev/null 2>&1; then
    echo "Docker isn't running — start Docker Desktop, then retry." >&2
    exit 1
  fi
fi

# 1. Already reachable? Nothing to do.
if up; then exit 0; fi

# 2. Plain re-enable.
say "Model Runner endpoint (:$PORT) is down — re-enabling…"
docker desktop enable model-runner --tcp="$PORT" >/dev/null 2>&1 || true
for _ in $(seq 1 5); do
  if up; then say "Model Runner is up on :$PORT."; exit 0; fi
  sleep 2
done

# 3. Force a disable→enable cycle (what actually re-binds a stuck port).
say "Re-binding the Model Runner TCP port…"
docker desktop disable model-runner >/dev/null 2>&1 || true
sleep 2
docker desktop enable model-runner --tcp="$PORT" >/dev/null 2>&1 || true
for _ in $(seq 1 15); do
  if up; then say "Model Runner is up on :$PORT."; exit 0; fi
  sleep 2
done

echo "Couldn't bring up the Model Runner on :$PORT." >&2
echo "Check Docker Desktop → Settings → AI → enable Model Runner + host TCP $PORT." >&2
exit 1
