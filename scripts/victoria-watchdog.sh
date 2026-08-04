#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Victoria sandbox watchdog — keeps http://127.0.0.1:$HOST_PORT alive.
#
# Installed as a launchd agent by scripts/setup-watchdog.sh (see that script and
# com.victoria.watchdog.plist.template). Run it directly for a foreground test:
#
#   SBX_NAME=victoria HOST_PORT=8001 ./scripts/victoria-watchdog.sh
#
# WHY: the sbx kit's `startup` service fires ONCE at `sbx run` — it is not a boot
# service. Two things therefore leave :8001 dark with no self-recovery:
#   1. Mac reboot — Docker Desktop auto-starts, but the sandbox does not.
#   2. Docker recycling the sandbox's container mid-session — this kills uvicorn
#      AND the kit's in-VM `while true` supervisor (observed twice: the sandbox
#      still reads "running" while nothing listens, and the in-VM client IP has
#      changed, e.g. 172.17.0.8 -> 172.17.0.6, with no crash in the log).
# A HOST-side watchdog survives both, because it lives outside the VM.
#
# The repair is deliberately the CHEAP path, never a recreate: the sandbox's
# filesystem (the uv py3.11 venv + all deps) survives a recycle, so we only need
# to relaunch uvicorn — seconds, not the ~15-20 min cold `deploy-sandbox.sh`.
# `sbx exec` starts a stopped sandbox first, so one call covers "stopped after
# reboot" and "running but dead inside".
#
# NOT handled on purpose: a MISSING sandbox (removed via `sbx rm`/`sbx reset`).
# That needs a full kit rebuild + mounts, which is `deploy-sandbox.sh`'s job and
# is far too heavy to trigger unattended from a background agent — we log it
# loudly instead.
# ─────────────────────────────────────────────────────────────────────
# NOTE: `-u` only. Deliberately NO `-e` (a watchdog must never exit on a transient
# failure) and NO `pipefail`: with pipefail, a `cmd | grep -q` pipeline reports failure
# when grep exits on its first match and cmd then takes SIGPIPE — which made a liveness
# check claim the sandbox was missing and skip the repair entirely.
set -u

SBX_NAME="${SBX_NAME:-victoria}"
HOST_PORT="${HOST_PORT:-8001}"
REPO_STAGE="${REPO_STAGE:-$HOME/sandboxes/victoria-ai}"
CHECK_INTERVAL="${CHECK_INTERVAL:-30}"     # seconds between health polls
LOG_MAX_BYTES="${LOG_MAX_BYTES:-1048576}"  # self-rotate at ~1 MB (launchd won't)
LOG_FILE="${LOG_FILE:-$HOME/Library/Logs/victoria-watchdog.log}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# launchd points stdout/stderr at LOG_FILE and never truncates it. Rotate here so
# a long-lived agent can't fill the disk.
rotate_log_if_big() {
  [ -f "$LOG_FILE" ] || return 0
  local size
  size=$(wc -c < "$LOG_FILE" 2>/dev/null | tr -d ' ') || return 0
  [ "${size:-0}" -gt "$LOG_MAX_BYTES" ] 2>/dev/null && mv -f "$LOG_FILE" "$LOG_FILE.1"
  return 0
}

healthy() { curl -4 -fsS -m 5 -o /dev/null "http://127.0.0.1:${HOST_PORT}/health" 2>/dev/null; }

# Every `sbx`/`docker` call goes through this. The sbx CLI can wedge indefinitely
# (observed: an `sbx ls` and an `sbx exec` still stuck after >24h), and a watchdog that
# blocks in a CLI call silently stops watching — the worst failure mode available to it.
# macOS ships neither `timeout` nor `gtimeout`, hence the hand-rolled version; stdout
# goes to a file because the command has to run in the background to be killable.
# Returns 124 on timeout, like GNU timeout.
#
# Depth-first tree kill: `pkill -P` only reaps DIRECT children, which orphaned the
# grandchildren (verified) — and a watchdog that leaks a hung `sbx` every 30s would
# manufacture the very CLI wedge it exists to survive. Killing the process *group* is
# not an option: a non-interactive shell's background jobs share the parent's pgid, so
# that would take out the watchdog itself.
kill_tree() {
  local p="$1" c
  for c in $(pgrep -P "$p" 2>/dev/null); do kill_tree "$c"; done
  kill -9 "$p" 2>/dev/null
}

run_timeout() {                     # run_timeout <secs> <outfile> <cmd> [args...]
  local secs="$1" out="$2"; shift 2
  : > "$out"
  # stderr is merged into the capture file on purpose: sbx reports auth failures
  # ("401 Unauthorized … please sign in") on stderr, and we want to recognise those
  # rather than report them as a generic wedge.
  ( "$@" >"$out" 2>&1 ) & local pid=$!
  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$secs" ]; then
      kill_tree "$pid"
      wait "$pid" 2>/dev/null
      return 124
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid" 2>/dev/null
}

SCRATCH="${TMPDIR:-/tmp}/victoria-watchdog.$$"

# Convenience wrapper: run, capture stdout in $SBX_OUT, warn loudly on a wedge.
# NOTE the rc is captured straight after the call, NOT inside an `if` — after a false
# condition with no `else`, bash resets `$?` to 0, which silently disabled the timeout
# branch in the first version of this script.
sbx_t() {                           # sbx_t <secs> <args...>  -> stdout in $SBX_OUT
  local secs="$1"; shift
  SBX_OUT=""
  run_timeout "$secs" "$SCRATCH" sbx "$@"
  local rc=$?
  SBX_OUT="$(cat "$SCRATCH" 2>/dev/null)"
  if [ "$rc" = "124" ]; then
    log "  WARN: 'sbx $*' timed out after ${secs}s — sbx CLI may be wedged"
    log "        (check: ps -eo pid,etime,args | grep sbx  →  kill -9 any long-running ones)"
  elif [ "$rc" != "0" ] && sbx_signed_out; then
    # Nothing can be repaired while the CLI is unauthenticated, and this is NOT a
    # wedge — say so with the actual remedy instead of a misleading timeout warning.
    # (Seen live: Docker Desktop's session expired, which also recycled the
    # container and SIGTERMed the bridge. Victoria kept serving throughout — the
    # sandbox outlives the CLI session — but no repair was possible.)
    log "  ERROR: sbx is signed out of Docker — repairs are PAUSED until you run: sbx login"
  fi
  return "$rc"
}

# Auth failure looks like: "401 Unauthorized: user is not authenticated to Docker",
# "no valid user session found, please sign in to Docker to proceed".
sbx_signed_out() {
  printf '%s' "$SBX_OUT" | grep -qiE 'not authenticated|no valid user session|please sign in|401 unauthorized'
}

docker_ready() { run_timeout 20 "$SCRATCH.docker" docker info; }

# Capture-then-match rather than piping into `grep -q`: no early-exit SIGPIPE, and a
# hung/empty `sbx ls` reads as "unknown" instead of a confident "missing".
sandbox_exists() {
  sbx_t 30 ls || return 2            # 2 = couldn't tell (wedged), NOT "missing"
  printf '%s\n' "$SBX_OUT" | awk 'NR>1 {print $1}' | grep -qx "$SBX_NAME"
}

# The in-VM supervisor. Written into the repo mount (so it's visible inside the
# sandbox at the same absolute path) rather than baked into the kit, so the
# watchdog can repair a sandbox that was created before this script existed.
ensure_runner() {
  local runner="$REPO_STAGE/.victoria-run.sh"
  [ -d "$REPO_STAGE" ] || { log "ERROR: repo stage $REPO_STAGE missing — run ./deploy-sandbox.sh"; return 1; }
  cat > "$runner" <<EOF
#!/bin/sh
# Generated by victoria-watchdog.sh — keeps uvicorn up inside the sandbox.
cd "$REPO_STAGE" || exit 1
export PATH=/usr/local/share/npm-global/bin:\$PATH
while true; do
  /home/agent/venv/bin/python -m uvicorn victoria.main:app --host 0.0.0.0 --port 8000
  echo "[supervisor] uvicorn exited (\$?) — restarting in 3s"
  sleep 3
done
EOF
  chmod 755 "$runner"
}

# Liveness is decided by an in-sandbox HTTP probe, NOT by process matching.
# Reason (learned the hard way): `sbx exec <sbx> -- sh -lc '<cmd>'` gives the
# wrapper shell a cmdline that CONTAINS <cmd>, so a naive `pgrep -f "uvicorn
# victoria.main"` matches the wrapper itself and always reports ALIVE, and the
# matching `pkill -f` makes that shell SIGTERM ITSELF — killing the very process
# that was about to start the supervisor. Where a pattern is unavoidable below we
# use the bracket trick (`uvicorn[ ]victoria.main`), which cannot self-match.
# A cold sandbox start can be slow, so allow more than the `ls` budget here.
in_sandbox_health() {
  sbx_t 90 exec "$SBX_NAME" -- sh -lc \
    'curl -s -o /dev/null -w "%{http_code}" -m 5 http://127.0.0.1:8000/health' || return 1
  printf '%s\n' "$SBX_OUT" | tail -1
}

repair() {
  log "REPAIR: :$HOST_PORT is down — attempting cheap in-place restart"

  if ! docker_ready; then
    log "  Docker not ready yet (still booting?) — will retry"
    return 1
  fi
  # 0 = exists, 1 = genuinely missing, 2 = couldn't tell (wedged CLI). Only 1 is a
  # "give up and tell the human" case; 2 must retry, never claim the sandbox is gone.
  sandbox_exists; local ex=$?
  if [ "$ex" = "1" ]; then
    log "  ERROR: sandbox '$SBX_NAME' does not exist. A watchdog will not rebuild it"
    log "         (needs a kit pack + mounts). Run: ./deploy-sandbox.sh"
    return 1
  elif [ "$ex" = "2" ]; then
    # sbx_t already logged the specific cause (timeout/wedge vs signed out).
    log "  could not list sandboxes — will retry next cycle"
    return 1
  fi
  ensure_runner || return 1

  # `sbx exec` starts the sandbox first if it is stopped, so this probe also covers
  # the "stopped after reboot" case.
  local inner
  inner="$(in_sandbox_health)"
  log "  in-sandbox /health = ${inner:-unreachable}"

  if [ "$inner" = "200" ]; then
    # The app is fine; only the host-side mapping is broken (a container recycle
    # drops it). Re-publishing alone fixes this — do NOT touch the running app.
    log "  app is healthy inside — republishing the host port mapping"
    sbx_t 45 ports "$SBX_NAME" --unpublish "127.0.0.1:${HOST_PORT}:8000"
    sbx_t 45 ports "$SBX_NAME" --publish   "127.0.0.1:${HOST_PORT}:8000"
  else
    # Verify deps survived. If the venv is gone the sandbox was rebuilt from
    # scratch and only a full deploy can fix it — say so rather than thrash.
    # Distinguish "import failed" from "couldn't run the check at all" (rc 124).
    sbx_t 90 exec "$SBX_NAME" -- sh -lc '/home/agent/venv/bin/python -c "import uvicorn, victoria.main"'
    local vrc=$?
    if [ "$vrc" != "0" ]; then
      if [ "$vrc" = "124" ]; then
        log "  could not verify the venv (sbx wedged?) — will retry next cycle"
      else
        log "  ERROR: py3.11 venv/deps missing inside the sandbox — run ./deploy-sandbox.sh"
      fi
      return 1
    fi
    # TWO SEPARATE exec calls, deliberately. The cleanup patterns must not appear
    # literally in their own command line, and the runner's PATH contains the text
    # "victoria-run" — so a combined command would match itself and SIGTERM the
    # wrapper shell before it ever reached setsid (this bit us twice). Keeping the
    # kill and the launch in different exec calls makes self-matching impossible.
    sbx_t 60 exec "$SBX_NAME" -- sh -lc \
      "pkill -f 'uvicorn[ ]victoria.main' 2>/dev/null; pkill -f 'victoria[-]run[.]sh' 2>/dev/null; exit 0"
    # setsid + nohup so the supervisor is re-parented to init and outlives this
    # `sbx exec` session (a plain `&` job dies with the exec).
    sbx_t 60 exec "$SBX_NAME" -- sh -lc \
      "setsid nohup sh '$REPO_STAGE/.victoria-run.sh' >> /tmp/victoria.log 2>&1 </dev/null & sleep 2; exit 0"
    log "  supervised uvicorn relaunched"
    sbx_t 45 ports "$SBX_NAME" --publish "127.0.0.1:${HOST_PORT}:8000"
  fi
  log "  port 127.0.0.1:${HOST_PORT} -> 8000 asserted"

  # App import is slow (chromadb/torch); give it generous headroom.
  local i
  for i in $(seq 1 60); do
    healthy && { log "  RECOVERED: http://127.0.0.1:${HOST_PORT} is serving"; return 0; }
    sleep 5
  done
  log "  still down after ~5 min — check: sbx exec $SBX_NAME -- tail -30 /tmp/victoria.log"
  return 1
}

trap 'rm -f "$SCRATCH" "$SCRATCH.docker"' EXIT INT TERM

log "watchdog start (sandbox=$SBX_NAME port=$HOST_PORT interval=${CHECK_INTERVAL}s stage=$REPO_STAGE)"
was_healthy=""
while true; do
  rotate_log_if_big
  if healthy; then
    # Only log transitions, so an idle watchdog stays quiet in the log.
    [ "$was_healthy" = "yes" ] || log "OK: http://127.0.0.1:${HOST_PORT} healthy"
    was_healthy="yes"
  else
    was_healthy="no"
    repair
  fi
  sleep "$CHECK_INTERVAL"
done
