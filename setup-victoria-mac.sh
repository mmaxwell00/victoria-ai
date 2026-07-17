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
# With no flags, it walks you through model / escalation / voice / knowledge base
# interactively (works even through `curl … | bash`, via /dev/tty). Any flag skips
# its prompt; a non-interactive run (no TTY) falls back to sensible defaults.
#
# Options:
#   --dir <path>       Install location            (default: ~/victoria-ai)
#   --model <name>     Local model to pull          (default: RAM-based pick)
#   --claude-token <t> Claude Code OAuth token — enables cloud escalation
#                      (get one by running: claude setup-token)
#   --with-voice       Also set up the native voice runner (Python + Piper)
#   --obsidian-vault <path>  Obsidian vault Victoria reads/searches/writes
#                      (default: ask, detecting your vaults; blank = skip)
#   --no-browser       Don't open the web UI when done
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

DIR="$HOME/victoria-ai"
REPO="https://github.com/mmaxwell00/victoria-ai.git"
MODEL=""   # empty = ask (or fall back to a RAM-based default); --model overrides
CLAUDE_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}"
WITH_VOICE=0
OPEN_BROWSER=1
MODEL_RUNNER_TCP=12434
OBSIDIAN_VAULT=""   # empty = ask (detect from Obsidian) or skip; --obsidian-vault overrides

while [ $# -gt 0 ]; do
  case "$1" in
    --dir)          DIR="$2"; shift 2 ;;
    --model)        MODEL="$2"; shift 2 ;;
    --claude-token) CLAUDE_TOKEN="$2"; shift 2 ;;
    --with-voice)   WITH_VOICE=1; shift ;;
    --obsidian-vault) OBSIDIAN_VAULT="$2"; shift 2 ;;
    --no-browser)   OPEN_BROWSER=0; shift ;;
    -h|--help)      grep '^#' "$0" | head -30; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)"; exit 1 ;;
  esac
done

info() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mNOTE:\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# macOS Keychain helpers — secrets live here, never in .env plaintext.
KC_ACCT="${USER:-victoria}"
kc_get() { security find-generic-password -a "$KC_ACCT" -s "$1" -w 2>/dev/null || true; }
kc_set() { security add-generic-password -U -a "$KC_ACCT" -s "$1" -w "$2" >/dev/null 2>&1; }

[ "$(uname)" = "Darwin" ] || fail "This installer is for macOS."

# ── 0. Interactive setup — ask unless a flag was passed / no TTY ─────
# Reads from /dev/tty so the prompts work even through `curl … | bash`.
RAM_GB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
if   [ "$RAM_GB" -ge 64 ]; then MODEL_REC="ai/qwen2.5:32k"
elif [ "$RAM_GB" -ge 16 ]; then MODEL_REC="ai/qwen2.5:latest"
else                            MODEL_REC="ai/llama3.2"
fi

# 0a. Local model (skipped if --model was given)
if [ -z "$MODEL" ] && [ -e /dev/tty ]; then
  printf '\n\033[1mLocal model\033[0m — %s GB RAM detected, recommended \033[1m%s\033[0m.\n' "$RAM_GB" "$MODEL_REC"
  printf 'Model id [Enter for %s]: ' "$MODEL_REC"
  read -r _ans < /dev/tty || _ans=""
  MODEL="${_ans:-$MODEL_REC}"
fi
[ -n "$MODEL" ] || MODEL="$MODEL_REC"   # non-interactive fallback

# 0b. Cloud escalation token (skipped if supplied via flag/env or a prior run)
[ -n "$CLAUDE_TOKEN" ] || CLAUDE_TOKEN="$(kc_get victoria-claude-token)"
if [ -z "$CLAUDE_TOKEN" ] && [ -e /dev/tty ]; then
  printf '\n\033[1mCloud escalation\033[0m (optional) — hand hard questions to Claude via your\n'
  printf 'subscription. Run \033[1mclaude setup-token\033[0m in another terminal and paste it here.\n'
  printf 'Token (or Enter to skip): '
  read -r -t 120 CLAUDE_TOKEN < /dev/tty || CLAUDE_TOKEN=""
fi

# 0c. Voice (skipped if --with-voice was given)
if [ "$WITH_VOICE" = 0 ] && [ -e /dev/tty ]; then
  printf '\n\033[1mVoice\033[0m (optional) — microphone input + spoken replies (adds Python + Piper).\n'
  printf 'Enable voice? [y/N]: '
  read -r _ans < /dev/tty || _ans=""
  case "$_ans" in [Yy]*) WITH_VOICE=1 ;; esac
fi

# 0d. Obsidian knowledge base (skipped if --obsidian-vault given / no TTY).
# Nothing is hard-coded: candidate vaults are read from Obsidian's own config,
# and the path is whatever you pick or type.
if [ -z "$OBSIDIAN_VAULT" ] && [ -e /dev/tty ]; then
  OBS_CFG="$HOME/Library/Application Support/obsidian/obsidian.json"
  DETECTED=""
  if [ -f "$OBS_CFG" ]; then
    DETECTED="$(grep -oE '"path":"[^"]*"' "$OBS_CFG" 2>/dev/null | sed 's/^"path":"//; s/"$//' || true)"
  fi
  printf '\n\033[1mObsidian knowledge base\033[0m (optional) — let Victoria read, search,\n'
  printf 'and write your notes. Point her at ONE Obsidian vault; its top-level\n'
  printf 'folders become areas she can target ("save to Personal", "search Docker").\n'
  if [ -n "$DETECTED" ]; then
    printf 'Detected vaults:\n'
    printf '%s\n' "$DETECTED" | awk '{printf "  %d. %s\n", NR, $0}'
    printf 'Pick a number, type a path, or Enter to skip: '
  else
    printf 'Path to your Obsidian vault (or Enter to skip): '
  fi
  _ans=""; read -r _ans < /dev/tty || _ans=""
  if [ -n "$_ans" ]; then
    case "$_ans" in
      *[!0-9]*) OBSIDIAN_VAULT="$_ans" ;;                                              # has a non-digit → a path
      *)        OBSIDIAN_VAULT="$(printf '%s\n' "$DETECTED" | sed -n "${_ans}p")" ;;    # all digits → Nth detected vault
    esac
  fi
  case "$OBSIDIAN_VAULT" in "~"*) OBSIDIAN_VAULT="$HOME${OBSIDIAN_VAULT#\~}" ;; esac
  if [ -n "$OBSIDIAN_VAULT" ] && [ ! -d "$OBSIDIAN_VAULT" ]; then
    warn "That folder isn't there yet — Victoria will pick it up once it exists."
  fi
fi

info "Setup → model: $MODEL · escalation: $([ -n "$CLAUDE_TOKEN" ] && echo on || echo off) · voice: $([ "$WITH_VOICE" = 1 ] && echo on || echo off) · notes: $([ -n "$OBSIDIAN_VAULT" ] && echo on || echo off)"

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

# ── 5. Local model — ensure the chosen model is pulled, then use it ──
extract_ids() { tr ',' '\n' | sed -n 's/.*"id":"\([^"]*\)".*/\1/p'; }
BASE="$(echo "$MODEL" | sed 's|.*/||; s|:.*||')"
IDS="$(models_json | extract_ids)"
if ! echo "$IDS" | grep -qi "$BASE"; then
  info "Pulling local model $MODEL (a few GB — grab a cuppa)…"
  docker model pull "$MODEL" || warn "Couldn't pull $MODEL; using whatever's available."
  IDS="$(models_json | extract_ids)"
fi
# Prefer the chosen model, else fall back to the first available.
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

# ── 7. Configuration ────────────────────────────────────────────────
# Non-secret settings go in .env; SECRETS go in the macOS Keychain and are
# injected into the container at launch (never written to disk in plaintext).
touch .env; chmod 600 .env
env_default() {  # append KEY=VALUE only if KEY not already present
  grep -q "^${1}=" .env || printf '%s=%s\n' "$1" "$2" >> .env
}
env_unset() {    # drop a KEY=... line from .env (used to migrate legacy secrets out)
  grep -q "^${1}=" .env 2>/dev/null && { grep -v "^${1}=" .env > .env.tmp && mv .env.tmp .env; } || true
}
env_default DEFAULT_LLM "docker"
env_default MODEL_RUNNER_MODEL "$MODEL_ID"

# Obsidian knowledge base — the vault path chosen in step 0 (or --obsidian-vault).
# Nothing is written when skipped, so the feature simply stays off.
if [ -n "$OBSIDIAN_VAULT" ]; then
  env_default OBSIDIAN_VAULT_PATH "$OBSIDIAN_VAULT"
  info "Knowledge base: $OBSIDIAN_VAULT"
fi

# Vault master key — kept in the Keychain (service: victoria-vault-key). Reuse an
# existing one, migrate a legacy key out of .env, or generate fresh. It encrypts
# data/vault.enc, so it must stay stable across rebuilds.
VAULT_KEY="$(kc_get victoria-vault-key)"
if [ -z "$VAULT_KEY" ]; then
  LEGACY="$(grep '^VICTORIA_VAULT_KEY=' .env 2>/dev/null | head -1 | cut -d= -f2-)"
  if [ -n "$LEGACY" ]; then
    VAULT_KEY="$LEGACY"; info "Migrating vault key from .env into the Keychain."
  else
    VAULT_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"; info "Generated a new vault key in the Keychain."
  fi
  kc_set victoria-vault-key "$VAULT_KEY"
fi
env_unset VICTORIA_VAULT_KEY
export VICTORIA_VAULT_KEY="$VAULT_KEY"

# Claude escalation token — collected interactively in step 0 (or supplied via
# --claude-token / CLAUDE_CODE_OAUTH_TOKEN / a prior run's Keychain entry).
[ -n "$CLAUDE_TOKEN" ] || CLAUDE_TOKEN="$(kc_get victoria-claude-token)"
env_unset CLAUDE_CODE_OAUTH_TOKEN
if [ -n "$CLAUDE_TOKEN" ]; then
  kc_set victoria-claude-token "$CLAUDE_TOKEN"
  export CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_TOKEN"
  env_default ESCALATION_ENABLED "true"
  info "Cloud escalation enabled (token stored in Keychain)."
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
# Inject secrets from the Keychain (they're never stored in .env) so compose
# can pass them into the container.
export VICTORIA_VAULT_KEY="\$(security find-generic-password -a "$KC_ACCT" -s victoria-vault-key -w 2>/dev/null || true)"
export CLAUDE_CODE_OAUTH_TOKEN="\$(security find-generic-password -a "$KC_ACCT" -s victoria-claude-token -w 2>/dev/null || true)"
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
