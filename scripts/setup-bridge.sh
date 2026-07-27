#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Victoria — Claude escalation, one-command "create once" setup.
#
#   ./scripts/setup-bridge.sh
#
# Sets up the Claude host bridge so Victoria (running in her sandbox) can escalate
# to Claude WITHOUT the Claude credential ever entering her VM. Run this ONCE on
# the host; it is idempotent (safe to re-run). It:
#
#   1. Generates the mTLS certs (CA + server + client) the bridge and Victoria use.
#   2. Creates a persistent GOVERNED claude sandbox ("victoria-claude") — the only
#      place `claude` actually runs. It uses sbx's built-in claude agent, so the
#      subscription credential lives in the sbx proxy / macOS Keychain, never here.
#   3. Installs the bridge as a launchd agent (auto-starts on login, self-restarts).
#   4. Writes ~/.victoria/bridge-env so ./deploy-sandbox.sh wires Victoria to it.
#
# After this, run ./deploy-sandbox.sh and Victoria's "Claude" backend routes over
# the bridge. See SANDBOX-DEPLOYMENT.md → "Claude escalation via the host bridge".
#
# Override via env: CLAUDE_SANDBOX, CERT_DIR, BRIDGE_PORT, CLAUDE_WS, BRIDGE_MODE,
#                   CLAUDE_TIMEOUT, LOG_FILE.  Pass --force to regenerate certs.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE="$HERE/claude-bridge.py"
TEMPLATE="$HERE/com.victoria.claude-bridge.plist.template"

CLAUDE_SANDBOX="${CLAUDE_SANDBOX:-victoria-claude}"
CERT_DIR="${CERT_DIR:-$HOME/.victoria/bridge-certs}"
BRIDGE_PORT="${BRIDGE_PORT:-8787}"
BRIDGE_MODE="${BRIDGE_MODE:-exec}"            # exec = stable sbx (no nightly); ssh = nightly
CLAUDE_TIMEOUT="${CLAUDE_TIMEOUT:-120}"
CLAUDE_WS="${CLAUDE_WS:-$HOME/sandboxes/${CLAUDE_SANDBOX}-ws}"  # must be under ~/sandboxes/**
LOG_FILE="${LOG_FILE:-$HOME/Library/Logs/victoria-claude-bridge.log}"
LABEL="com.victoria.claude-bridge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_FILE="$HOME/.victoria/bridge-env"
FORCE=0; [ "${1:-}" = "--force" ] && FORCE=1

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mNOTE:\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Preflight
command -v sbx      >/dev/null || fail "sbx not found — install: brew install docker/tap/sbx"
command -v openssl  >/dev/null || fail "openssl not found (needed to generate the mTLS certs)."
PYTHON="$(command -v python3 || true)"; [ -n "$PYTHON" ] || fail "python3 not found."
docker info >/dev/null 2>&1     || fail "Docker isn't running — start Docker Desktop, then re-run."
[ -f "$BRIDGE" ]   || fail "missing $BRIDGE"
[ -f "$TEMPLATE" ] || fail "missing $TEMPLATE"
say "sbx $(sbx version 2>&1 | head -1) · python3 $($PYTHON -V 2>&1 | awk '{print $2}') · Docker OK"

# 2. mTLS certs (idempotent — regenerate only with --force)
if [ "$FORCE" = 1 ] || [ ! -f "$CERT_DIR/server.crt" ]; then
  say "Generating mTLS certs -> $CERT_DIR"
  "$PYTHON" "$BRIDGE" --gen-certs "$CERT_DIR"
else
  say "mTLS certs already present at $CERT_DIR (use --force to regenerate)"
fi
for f in ca.crt server.crt server.key client.crt client.key; do
  [ -f "$CERT_DIR/$f" ] || fail "cert generation incomplete — missing $CERT_DIR/$f"
done

# 3. Governed claude sandbox — the ONLY place `claude` runs. Built-in claude agent
#    ⇒ the subscription credential stays in the sbx proxy / Keychain, never in a VM.
mkdir -p "$CLAUDE_WS"
if sbx ls 2>/dev/null | grep -qw "$CLAUDE_SANDBOX"; then
  say "Governed claude sandbox '$CLAUDE_SANDBOX' already exists"
else
  say "Creating governed claude sandbox '$CLAUDE_SANDBOX' (workspace: $CLAUDE_WS)…"
  if ! out="$(sbx create --name "$CLAUDE_SANDBOX" claude "$CLAUDE_WS" 2>&1)"; then
    if printf '%s' "$out" | grep -qiE 'already exists|in use|duplicate'; then
      say "…'$CLAUDE_SANDBOX' already exists"
    else
      printf '%s\n' "$out" >&2
      fail "sbx create failed (see above)."
    fi
  fi
fi

# 4. launchd agent — auto-start on login, self-restart on exit
say "Installing launchd agent -> $PLIST"
mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG_FILE")"
LAUNCHD_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
sed -e "s#__PYTHON__#${PYTHON}#g" \
    -e "s#__BRIDGE_SCRIPT__#${BRIDGE}#g" \
    -e "s#__CERT_DIR__#${CERT_DIR}#g" \
    -e "s#__PATH__#${LAUNCHD_PATH}#g" \
    -e "s#__CLAUDE_BRIDGE_MODE__#${BRIDGE_MODE}#g" \
    -e "s#__CLAUDE_SANDBOX__#${CLAUDE_SANDBOX}#g" \
    -e "s#__BRIDGE_PORT__#${BRIDGE_PORT}#g" \
    -e "s#__CLAUDE_TIMEOUT__#${CLAUDE_TIMEOUT}#g" \
    -e "s#__LOG_FILE__#${LOG_FILE}#g" \
  "$TEMPLATE" > "$PLIST"
grep -q '__[A-Z_]*__' "$PLIST" && fail "plist substitution left a placeholder in $PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

# 5. Config for the deploy — deploy-sandbox.sh sources this to wire CLAUDE_BRIDGE_*
mkdir -p "$(dirname "$ENV_FILE")"
cat > "$ENV_FILE" <<ENV
# Written by scripts/setup-bridge.sh — consumed by ./deploy-sandbox.sh.
CLAUDE_BRIDGE_URL=https://host.docker.internal:${BRIDGE_PORT}/ask
CLAUDE_BRIDGE_CERT_DIR=${CERT_DIR}
ENV
say "Wrote bridge config -> $ENV_FILE"

# 6. Verify the bridge is listening (TLS handshake needs the client cert)
sleep 2
if printf 'Q\n' | openssl s_client -connect "127.0.0.1:${BRIDGE_PORT}" \
     -cert "$CERT_DIR/client.crt" -key "$CERT_DIR/client.key" -CAfile "$CERT_DIR/ca.crt" \
     >/dev/null 2>&1; then
  say "Bridge is up: https://127.0.0.1:${BRIDGE_PORT}/ask (mTLS) — mode=$BRIDGE_MODE"
else
  warn "Bridge not answering yet on :$BRIDGE_PORT. Check the log: tail -f $LOG_FILE"
fi

cat <<DONE

──────────────────────────────────────────────────────────────────────────────
$(say "Claude bridge setup complete.")

  Governed sandbox : $CLAUDE_SANDBOX  (claude runs only here)
  Bridge (launchd) : $LABEL  →  https://host.docker.internal:${BRIDGE_PORT}/ask
  Certs            : $CERT_DIR
  Log              : $LOG_FILE

ONE-TIME AUTH (if you haven't signed the claude agent in yet): the built-in
claude agent authenticates via your subscription. Run this once and complete the
browser login, then Ctrl-C:

    sbx run claude

NEXT: deploy Victoria — she'll auto-wire to the bridge from $ENV_FILE:

    ./deploy-sandbox.sh

Manage the bridge:
    launchctl unload $PLIST   # stop
    launchctl load -w $PLIST  # start
    tail -f $LOG_FILE         # logs
──────────────────────────────────────────────────────────────────────────────
DONE
