#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Victoria — Docker Sandboxes (sbx) deployment (verified working, Phase 1)
#
#   ./deploy-sandbox.sh
#
# Runs Victoria as a persistent service inside an isolated Docker Sandbox,
# backed by the host's Docker Model Runner. See SANDBOX-DEPLOYMENT.md.
#
# Configurable (env or edit here):
#   SBX_NAME      sandbox name              (default: victoria)
#   REPO_STAGE    where the code is staged  (default: ~/sandboxes/victoria-ai)
#   VAULT_PATH    Obsidian vault to mount   (default: ~/Obsidian/AI/AI-Victoria)
#   HOST_PORT     host port for the HUD     (default: 8001)
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

SBX_NAME="${SBX_NAME:-victoria}"
REPO_STAGE="${REPO_STAGE:-$HOME/sandboxes/victoria-ai}"
VAULT_PATH="${VAULT_PATH:-$HOME/Obsidian/AI/AI-Victoria}"
HOST_PORT="${HOST_PORT:-8001}"
KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sbx"
KIT_ZIP="/tmp/${SBX_NAME}-kit.zip"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mNOTE:\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Tooling
command -v sbx >/dev/null || fail "sbx not found. Install: brew install docker/tap/sbx (macOS) / winget install Docker.sbx (Windows)"
docker info >/dev/null 2>&1 || fail "Docker isn't running — start Docker Desktop."
say "sbx $(sbx version 2>&1 | head -1) · Docker OK"

# 2. Host Docker Model Runner on :12434 (the sandbox reaches it via host.docker.internal)
if curl -fsS -m 4 http://localhost:12434/engines/llama.cpp/v1/models >/dev/null 2>&1; then
  say "Model Runner reachable on the host (:12434)"
else
  warn "Model Runner not reachable on :12434 — enable it: docker desktop enable model-runner --tcp=12434"
fi
docker model ls >/dev/null 2>&1 && [ "$(docker model ls | tail -n +2 | wc -l | tr -d ' ')" -gt 0 ] \
  || warn "No models pulled — e.g. docker model pull ai/qwen2.5"

# 3. Governance: the sandbox can only mount org-allowed roots. Code must live
#    under ~/sandboxes/**; the vault path needs its own filesystem-allow rule.
say "Mount policy note: code must be under ~/sandboxes/**; the vault ($VAULT_PATH) needs an org fs-allow rule."

# 4. Stage the code under the allowed root
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -d "$REPO_STAGE" ]; then
  say "Staging code -> $REPO_STAGE"
  git clone -q "$HERE" "$REPO_STAGE"
else
  say "Code already staged at $REPO_STAGE"
fi

# 4b. Stage the Piper voice model (TTS). It's large + gitignored, so it never
#     rides along in the clone — without it, /v1/tts 503s ("Piper model not
#     found") and Victoria can hear you but can't speak. Copy from a native
#     checkout if present (fast, local), else pull it from Hugging Face.
PIPER_ONNX="en_GB-jenny_dioco-medium.onnx"
if [ ! -f "$REPO_STAGE/models/$PIPER_ONNX" ]; then
  mkdir -p "$REPO_STAGE/models"
  if [ -f "$HERE/models/$PIPER_ONNX" ]; then
    say "Staging Piper voice model (copy from $HERE/models)"
    cp "$HERE/models/$PIPER_ONNX" "$HERE/models/$PIPER_ONNX.json" "$REPO_STAGE/models/"
  else
    say "Downloading Piper voice model -> $REPO_STAGE/models (Hugging Face)"
    PIPER_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/jenny_dioco/medium"
    curl -fL --progress-bar -o "$REPO_STAGE/models/$PIPER_ONNX"      "$PIPER_URL/$PIPER_ONNX" \
      && curl -fL --silent   -o "$REPO_STAGE/models/$PIPER_ONNX.json" "$PIPER_URL/$PIPER_ONNX.json" \
      || warn "Piper model download failed — TTS (spoken replies) will be unavailable until models/$PIPER_ONNX exists."
  fi
else
  say "Piper voice model already staged"
fi

# 5. Pack + run the kit (agent name must equal the kit name). sbx mounts a host
#    path at that same absolute path inside the sandbox, so the kit's repo/vault
#    paths are host-specific. The committed spec.yaml keeps them as placeholders
#    (__VICTORIA_REPO__ / __VICTORIA_VAULT__); fill them into a throwaway build
#    copy here so the repo stays username-agnostic.
# Claude escalation token (optional, NEVER committed). Prefer $CLAUDE_CODE_OAUTH_TOKEN,
# else an untracked local file. Empty → escalation stays off (local model answers).
CLAUDE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}"
if [ -z "$CLAUDE_OAUTH_TOKEN" ] && [ -f "$HOME/.victoria/claude-oauth-token" ]; then
  CLAUDE_OAUTH_TOKEN="$(tr -d '\r\n' < "$HOME/.victoria/claude-oauth-token")"
fi
if [ -n "$CLAUDE_OAUTH_TOKEN" ]; then
  say "Claude escalation: enabled (subscription token found)"
else
  warn 'Claude escalation: OFF — no token ($CLAUDE_CODE_OAUTH_TOKEN env or ~/.victoria/claude-oauth-token). Local model still answers.'
fi

BUILD_DIR="$(mktemp -d)"; trap 'rm -rf "$BUILD_DIR"' EXIT
cp -R "$KIT_DIR/." "$BUILD_DIR/"
sed -e "s#__VICTORIA_REPO__#${REPO_STAGE}#g" \
    -e "s#__VICTORIA_VAULT__#${VAULT_PATH}#g" \
    -e "s#__CLAUDE_OAUTH_TOKEN__#${CLAUDE_OAUTH_TOKEN}#g" \
  "$KIT_DIR/spec.yaml" > "$BUILD_DIR/spec.yaml"
if grep -qE "__(VICTORIA_REPO|VICTORIA_VAULT|CLAUDE_OAUTH_TOKEN)__" "$BUILD_DIR/spec.yaml"; then
  fail "Placeholder substitution failed in $BUILD_DIR/spec.yaml"
fi
say "Packing kit -> $KIT_ZIP  (repo=$REPO_STAGE)"
sbx kit pack "$BUILD_DIR" -o "$KIT_ZIP" >/dev/null
sbx rm "$SBX_NAME" --force >/dev/null 2>&1 || true
say "Launching sandbox '$SBX_NAME' (installs deps, starts uvicorn)…"
MOUNTS=("$REPO_STAGE"); [ -d "$VAULT_PATH" ] && MOUNTS+=("$VAULT_PATH") || warn "Vault $VAULT_PATH not found — running without the knowledge base."
sbx run --kit "$KIT_ZIP" --name "$SBX_NAME" -d "$SBX_NAME" "${MOUNTS[@]}"

# 6. Publish the HUD (IPv4 — the in-sandbox service is IPv4-only)
say "Publishing HUD -> http://127.0.0.1:${HOST_PORT}"
sbx ports "$SBX_NAME" --publish "127.0.0.1:${HOST_PORT}:8000" >/dev/null 2>&1 || true

# 7. Wait for readiness. A cold first boot installs the FULL dependency set
#    (torch, faster-whisper, chromadb, …) with no wheel cache, then the startup
#    service waits for the app to import before launching uvicorn — which can run
#    well past 8 min on a slow/loaded host. Allow generous headroom (~20 min,
#    matching the kit's ~20-min import gate); the loop breaks as soon as /health
#    answers, so a fast machine isn't penalised.
for _ in $(seq 1 240); do
  curl -4 -fsS -m 3 "http://127.0.0.1:${HOST_PORT}/health" >/dev/null 2>&1 && break || sleep 5
done
if curl -4 -fsS -m 4 "http://127.0.0.1:${HOST_PORT}/health" >/dev/null 2>&1; then
  say "Victoria is up: http://127.0.0.1:${HOST_PORT}   (use 127.0.0.1, not localhost)"
else
  warn "Not reachable yet. Check: sbx exec $SBX_NAME -- tail /tmp/victoria.log"
fi
