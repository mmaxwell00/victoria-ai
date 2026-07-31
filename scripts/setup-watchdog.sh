#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Install the Victoria sandbox watchdog as a launchd agent (idempotent).
#
#   ./scripts/setup-watchdog.sh            # install + start
#   ./scripts/setup-watchdog.sh --status   # is it loaded? is Victoria up?
#   ./scripts/setup-watchdog.sh --uninstall
#
# Makes Victoria's sandbox survive a Mac reboot AND Docker recycling the
# sandbox's container — neither of which the sbx kit's one-shot `startup`
# service can recover from. See scripts/victoria-watchdog.sh for the why.
#
# Configurable (env): SBX_NAME, HOST_PORT, REPO_STAGE, CHECK_INTERVAL
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

LABEL="com.victoria.watchdog"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$HERE/$LABEL.plist.template"
SCRIPT="$HERE/victoria-watchdog.sh"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_FILE="$HOME/Library/Logs/victoria-watchdog.log"

SBX_NAME="${SBX_NAME:-victoria}"
HOST_PORT="${HOST_PORT:-8001}"
REPO_STAGE="${REPO_STAGE:-$HOME/sandboxes/victoria-ai}"
CHECK_INTERVAL="${CHECK_INTERVAL:-30}"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mNOTE:\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
  --status)
    # `launchctl list <label>` returns nonzero when absent — no pipe, so this can't
    # trip the `grep -q` + pipefail SIGPIPE race (which reported NOT loaded while the
    # agent was demonstrably running).
    if launchctl list "$LABEL" >/dev/null 2>&1; then
      say "watchdog LOADED: $(launchctl list | awk -v l="$LABEL" '$3 == l {print "pid="$1" last-exit="$2}')"
    else
      warn "watchdog NOT loaded"
    fi
    if curl -4 -fsS -m 5 -o /dev/null "http://127.0.0.1:${HOST_PORT}/health" 2>/dev/null; then
      say "Victoria is UP on http://127.0.0.1:${HOST_PORT}"
    else
      warn "Victoria is DOWN on :${HOST_PORT} (watchdog should repair within ~${CHECK_INTERVAL}s)"
    fi
    [ -f "$LOG_FILE" ] && { say "last log lines:"; tail -5 "$LOG_FILE"; }
    exit 0 ;;
  --uninstall)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    say "watchdog uninstalled (Victoria itself is untouched and still running)"
    exit 0 ;;
esac

[ -f "$TEMPLATE" ] || fail "missing template: $TEMPLATE"
[ -f "$SCRIPT" ]   || fail "missing script: $SCRIPT"
chmod 755 "$SCRIPT"
command -v sbx >/dev/null || fail "sbx not found — install it before the watchdog is useful"

# launchd agents get a minimal PATH; bake in the dirs holding sbx/docker/curl.
SBX_DIR="$(dirname "$(command -v sbx)")"
DOCKER_DIR="$(dirname "$(command -v docker 2>/dev/null || echo /usr/local/bin/docker)")"
AGENT_PATH="$SBX_DIR:$DOCKER_DIR:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$LOG_FILE")"
sed -e "s#__WATCHDOG_SCRIPT__#${SCRIPT}#g" \
    -e "s#__PATH__#${AGENT_PATH}#g" \
    -e "s#__SBX_NAME__#${SBX_NAME}#g" \
    -e "s#__HOST_PORT__#${HOST_PORT}#g" \
    -e "s#__REPO_STAGE__#${REPO_STAGE}#g" \
    -e "s#__CHECK_INTERVAL__#${CHECK_INTERVAL}#g" \
    -e "s#__LOG_FILE__#${LOG_FILE}#g" \
  "$TEMPLATE" > "$PLIST"
grep -q '__[A-Z_]*__' "$PLIST" && fail "plist substitution left a placeholder in $PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

say "installed $LABEL (RunAtLoad + KeepAlive, polling every ${CHECK_INTERVAL}s)"
say "  sandbox=$SBX_NAME  port=$HOST_PORT  stage=$REPO_STAGE"
say "  log:    tail -f $LOG_FILE"
say "  status: ./scripts/setup-watchdog.sh --status"
say "  remove: ./scripts/setup-watchdog.sh --uninstall"
warn "Victoria now returns automatically after a reboot or a container recycle."
warn "It will NOT rebuild a DELETED sandbox — that's still ./deploy-sandbox.sh."
