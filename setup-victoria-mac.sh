#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Victoria AI — one-command installer for macOS
#
#   curl -fsSL https://raw.githubusercontent.com/mmaxwell00/victoria-ai/main/setup-victoria-mac.sh | bash
#
# Takes a fresh Mac to a running Victoria (web HUD, tools, skills, MCP,
# vault, browser voice, and optional Claude escalation) with the fewest
# possible manual steps. Everything it does is idempotent — safe to re-run.
#
# The only things macOS makes YOU do:
#   1. Type your password when Homebrew / Docker Desktop install
#   2. Click OK on the Xcode Command Line Tools + Docker first-run dialogs
#   3. (voice only) Approve the browser's microphone prompt
#
# Options:
#   --dir <path>       Install location            (default: ~/victoria-ai)
#   --model <name>     Local model to pull          (default: ai/qwen2.5:latest)
#   --claude-token <t> Claude Code OAuth token — enables cloud escalation
#                      (get one by running: claude setup-token)
#   --with-voice       Also set up the native voice runner (Python + Piper)
#   --no-browser       Don't open the web UI when done
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

DIR="$HOME/victoria-ai"
REPO="https://github.com/mmaxwell00/victoria-ai.git"
MODEL="ai/qwen2.5:latest"
CLAUDE_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}"
WITH_VOICE=0
OPEN_BROWSER=1
MODEL_RUNNER_TCP=12434

while [ $# -gt 0 ]; do
  case "$1" in
    --dir)          DIR="$2"; shift 2 ;;
    --model)        MODEL="$2"; shift 2 ;;
    --claude-token) CLAUDE_TOKEN="$2"; shift 2 ;;
    --with-voice)   WITH_VOICE=1; shift ;;
    --no-browser)   OPEN_BROWSER=0; shift ;;
    -h|--help)      grep '^#' "$0" | head -30; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)"; exit 1 ;;
  esac
done

info() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mNOTE:\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname)" = "Darwin" ] || fail "This installer is for macOS."

# ── 1. Xcode Command Line Tools (provides git) ──────────────────────
if ! xcode-select -p >/dev/null 2>&1; then
  info "Xcode Command Line Tools are missing — requesting install…"
  warn "A macOS dialog will appear: click 'Install' and I'll wait."
  xcode-select --install 2>/dev/null || true
  until xcode-select -p >/dev/null 2>&1; do sleep 10; done
fi

# ── 2. Homebrew ─────────────────────────────────────────────────────
if ! command -v brew >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/brew ]; then
  info "Installing Homebrew (you'll be asked for your password)…"
  NONINTERACTIVE=1 /bin/bash -c \
    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/tty
fi
command -v brew >/dev/null 2>&1 || eval "$(/opt/homebrew/bin/brew shellenv)"

# ── 3. Docker Desktop ───────────────────────────────────────────────
if [ ! -d /Applications/Docker.app ]; then
  info "Installing Docker Desktop (password may be requested)…"
  brew install --cask docker
fi
if ! docker info >/dev/null 2>&1; then
  info "Starting Docker Desktop…"
  warn "First launch shows a setup dialog — accept it and I'll wait (up to 5 min)."
  open -a Docker
  for _ in $(seq 1 150); do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
  docker info >/dev/null 2>&1 || fail "Docker didn't become ready. Finish its setup dialog, then re-run this script."
fi

# ── 4. Docker Model Runner ──────────────────────────────────────────
info "Enabling Docker Model Runner…"
if ! docker desktop enable model-runner --tcp="$MODEL_RUNNER_TCP" >/dev/null 2>&1; then
  warn "Couldn't enable Model Runner from the CLI (older Docker Desktop?)."
  warn "Enable it manually: Docker Desktop → Settings → AI → Enable Docker Model Runner (+ TCP port $MODEL_RUNNER_TCP), then re-run this script."
  exit 1
fi

models_json() {
  curl -sf --max-time 5 "http://localhost:$MODEL_RUNNER_TCP/engines/llama.cpp/v1/models" 2>/dev/null || echo ""
}

for _ in $(seq 1 15); do
  [ -n "$(models_json)" ] && break
  sleep 2
done
[ -n "$(models_json)" ] || fail "Model Runner isn't responding on port $MODEL_RUNNER_TCP."

# ── 5. Local model — reuse one if pulled, else pull the default ─────
extract_ids() { tr ',' '\n' | sed -n 's/.*"id":"\([^"]*\)".*/\1/p'; }
IDS="$(models_json | extract_ids)"
if [ -z "$IDS" ]; then
  info "Pulling local model $MODEL (a few GB — grab a cuppa)…"
  docker model pull "$MODEL"
  IDS="$(models_json | extract_ids)"
fi
# Prefer a model matching the requested name, else use the first available.
BASE="$(echo "$MODEL" | sed 's|.*/||; s|:.*||')"
MODEL_ID="$(echo "$IDS" | grep -i "$BASE" | head -1 || true)"
[ -n "$MODEL_ID" ] || MODEL_ID="$(echo "$IDS" | head -1)"
[ -n "$MODEL_ID" ] || fail "No model available in Model Runner after pull."
info "Using local model: $MODEL_ID"

# ── 6. Get Victoria ─────────────────────────────────────────────────
if [ -d "$DIR/.git" ]; then
  info "Updating existing Victoria at $DIR…"
  git -C "$DIR" fetch origin
  if [ -z "$(git -C "$DIR" status --porcelain)" ]; then
    git -C "$DIR" reset --hard origin/main
  else
    warn "Local changes detected in $DIR — leaving them untouched."
  fi
else
  info "Cloning Victoria into $DIR…"
  git clone "$REPO" "$DIR"
fi
cd "$DIR"

# ── 7. Configuration (.env) — only ever fills in missing keys ───────
touch .env
env_default() {  # env_default KEY VALUE — append only if KEY not present
  grep -q "^${1}=" .env || printf '%s=%s\n' "$1" "$2" >> .env
}
env_default DEFAULT_LLM "docker"
env_default MODEL_RUNNER_MODEL "$MODEL_ID"
# Stable vault key: without it, secrets die with the container (no macOS
# Keychain inside Docker).
env_default VICTORIA_VAULT_KEY "$(openssl rand -base64 32 | tr '+/' '-_')"

# Escalation: works out of the box if a Claude Code token is provided.
if [ -z "$CLAUDE_TOKEN" ] && [ -t 0 ] || { [ -z "$CLAUDE_TOKEN" ] && [ -e /dev/tty ]; }; then
  if ! grep -q "^CLAUDE_CODE_OAUTH_TOKEN=" .env && ! grep -q "^ESCALATION_ENABLED=" .env; then
    printf '\n\033[1mOptional:\033[0m Victoria can escalate hard questions to Claude (uses your\n'
    printf 'Claude subscription). To enable, run \033[1mclaude setup-token\033[0m in another\n'
    printf 'terminal and paste the token here.\n'
    printf 'Token (or press Enter to skip): '
    read -r -t 120 CLAUDE_TOKEN < /dev/tty || CLAUDE_TOKEN=""
    echo
  fi
fi
if [ -n "$CLAUDE_TOKEN" ]; then
  env_default CLAUDE_CODE_OAUTH_TOKEN "$CLAUDE_TOKEN"
  env_default ESCALATION_ENABLED "true"
  info "Cloud escalation enabled."
else
  env_default ESCALATION_ENABLED "false"
  info "Cloud escalation off (re-run with --claude-token to enable later)."
fi

mkdir -p data models

# ── 8. Build & launch ───────────────────────────────────────────────
info "Building the Victoria image…"
docker compose build victoria-api
info "Starting Victoria…"
docker compose up -d victoria-api

printf '==> Waiting for Victoria to wake up'
READY=0
for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then READY=1; break; fi
  printf '.'
  sleep 2
done
echo
[ "$READY" = 1 ] || fail "Victoria didn't respond — check: docker compose logs victoria-api"

# ── 9. The `victoria` command ───────────────────────────────────────
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/victoria" <<CMD
#!/usr/bin/env bash
# victoria — start/stop/manage Victoria AI (generated by setup-victoria-mac.sh)
set -euo pipefail
DIR="$DIR"
URL="http://localhost:8000"
cd "\$DIR"
case "\${1:-start}" in
  start)
    if ! docker info >/dev/null 2>&1; then
      echo "==> Starting Docker Desktop…"; open -a Docker
      for _ in \$(seq 1 60); do sleep 2; docker info >/dev/null 2>&1 && break; done
    fi
    docker compose up -d victoria-api
    printf '==> Waiting for Victoria'
    for _ in \$(seq 1 30); do
      curl -sf "\$URL/health" >/dev/null 2>&1 && { echo; echo "==> Ready — \$URL"; open "\$URL"; exit 0; }
      printf '.'; sleep 1
    done
    echo; echo "Victoria didn't respond — try: victoria logs"; exit 1 ;;
  stop)   docker compose down; echo "Stopped (data kept in \$DIR/data)" ;;
  status) curl -sf "\$URL/health" && echo || echo "Victoria is not running" ;;
  logs)   docker compose logs -f victoria-api ;;
  update) git pull --ff-only && docker compose build victoria-api && docker compose up -d victoria-api \
          && echo "Updated — hard-refresh the browser tab (Cmd+Shift+R)" ;;
  *) echo "Usage: victoria [start|stop|status|logs|update]"; exit 1 ;;
esac
CMD
chmod +x "$HOME/.local/bin/victoria"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) warn "Add ~/.local/bin to your PATH to use the 'victoria' command:"
     warn '  echo '\''export PATH="$HOME/.local/bin:$PATH"'\'' >> ~/.zshrc' ;;
esac

# ── 10. Optional native voice runner ────────────────────────────────
if [ "$WITH_VOICE" = 1 ]; then
  info "Setting up native voice (Python, PortAudio, Piper)…"
  brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
  for pkg in portaudio ffmpeg; do
    brew list "$pkg" >/dev/null 2>&1 || brew install "$pkg"
  done
  [ -x .venv/bin/python ] || "$(brew --prefix python@3.12)/libexec/bin/python3" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
  if [ ! -f models/en_GB-jenny_dioco-medium.onnx ]; then
    PIPER="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/jenny_dioco/medium"
    curl -fL --progress-bar -o models/en_GB-jenny_dioco-medium.onnx "$PIPER/en_GB-jenny_dioco-medium.onnx"
    curl -fL --silent -o models/en_GB-jenny_dioco-medium.onnx.json "$PIPER/en_GB-jenny_dioco-medium.onnx.json"
  fi
  info "Voice runner ready: .venv/bin/python scripts/run_voice.py"
fi

# ── Done ────────────────────────────────────────────────────────────
echo
info "Victoria is running → http://localhost:8000"
info "Manage her with: victoria [start|stop|status|logs|update]"
[ "$OPEN_BROWSER" = 1 ] && open http://localhost:8000 || true
