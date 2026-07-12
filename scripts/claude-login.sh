#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Log this Mac into Claude so Victoria can escalate to it.
#
#   ~/victoria-ai/scripts/claude-login.sh
#
# Escalation uses your Claude *subscription* via the Claude Code CLI — no API
# key, no per-token billing. Two ways to authenticate:
#   1) Interactive subscription login — convenient for desktop use.
#   2) A long-lived token written to .env — best for an always-on server: it
#      doesn't silently expire and works regardless of how Victoria is launched.
# The local model works without any of this; this only enables escalation.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="${CLAUDE_CLI_COMMAND:-claude}"

if ! command -v "$CLI" >/dev/null 2>&1; then
  echo "Claude Code CLI ('$CLI') isn't on your PATH." >&2
  echo "Install it from https://claude.com/claude-code, then re-run this." >&2
  exit 1
fi

echo "Authenticate Claude for Victoria's escalation:"
echo "  1) Interactive subscription login"
echo "  2) Long-lived token → .env   (recommended for an always-on server)"
printf "Choose [1/2] (or q to quit): "
read -r choice

case "$choice" in
  1)
    echo "Launching '$CLI' — if it doesn't prompt to log in, type /login, then quit the CLI."
    "$CLI" || true
    echo "If you completed login, escalation is ready."
    ;;
  2)
    echo "Running '$CLI setup-token' — copy the token it prints."
    "$CLI" setup-token || true
    printf "\nPaste the token here (input hidden): "
    read -rs token; echo
    token="$(printf '%s' "$token" | tr -d '[:space:]')"
    if [ -z "$token" ]; then
      echo "No token entered — nothing changed." >&2
      exit 1
    fi
    touch "$DIR/.env"
    # Replace any existing line, then append the new one.
    grep -v '^CLAUDE_CLI_OAUTH_TOKEN=' "$DIR/.env" > "$DIR/.env.tmp" 2>/dev/null || true
    mv "$DIR/.env.tmp" "$DIR/.env"
    printf 'CLAUDE_CLI_OAUTH_TOKEN=%s\n' "$token" >> "$DIR/.env"
    echo "Saved CLAUDE_CLI_OAUTH_TOKEN to .env (git-ignored)."
    ;;
  *)
    echo "Cancelled."; exit 0 ;;
esac

echo "Done. Restart Victoria to pick it up:  scripts/start.sh"
