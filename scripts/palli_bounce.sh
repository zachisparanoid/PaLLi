#!/usr/bin/env bash
# palli_bounce.sh [delay_seconds]
# Kill the running PaLLi agent and bring it back in the same tmux pane.
# Designed to be launched detached (setsid nohup ... &) so it survives the kill it performs.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="palli"
TARGET="${SESSION}:0.0"
PY="$ROOT/venv/bin/python"
LOG="$ROOT/logs/bounce.log"
DELAY="${1:-15}"
DRY="${DRY_RUN:-0}"

mkdir -p "$ROOT/logs"
log(){ printf '%s [bounce] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

# agent pids: cmdline mentions main.py AND cwd is the repo (won't touch Claude/other work)
find_pids(){
  local p a0 a1 cwd
  for p in $(pgrep -f 'main\.py' 2>/dev/null); do
    [ -r "/proc/$p/cmdline" ] || continue
    a0=$(tr '\0' '\n' < "/proc/$p/cmdline" | sed -n 1p)
    a1=$(tr '\0' '\n' < "/proc/$p/cmdline" | sed -n 2p)
    cwd=$(readlink "/proc/$p/cwd" 2>/dev/null)
    case "$a0" in
      *python*) [ "$a1" = "main.py" ] && [ "$cwd" = "$ROOT" ] && echo "$p" ;;
    esac
  done | sort -u
}

log "scheduled: delay=${DELAY}s target=${TARGET} dry_run=${DRY}"
sleep "$DELAY"

PIDS=$(find_pids | tr '\n' ' ' | sed 's/ *$//')
log "targets -> ${PIDS:-none}"

if [ "$DRY" = "1" ]; then
  log "DRY RUN: would terminate [${PIDS:-none}], then respawn in ${TARGET}"
  tmux has-session -t "$SESSION" 2>/dev/null && log "DRY RUN: session $SESSION present, pane ok" || log "DRY RUN: session $SESSION MISSING"
  log "dry run complete"; exit 0
fi

if [ -n "$PIDS" ]; then
  kill -TERM $PIDS 2>/dev/null
  for _ in $(seq 1 20); do
    alive=0
    for p in $PIDS; do kill -0 "$p" 2>/dev/null && alive=1; done
    [ "$alive" -eq 0 ] && break
    sleep 1
  done
  for p in $PIDS; do
    if kill -0 "$p" 2>/dev/null; then log "still up, SIGKILL $p"; kill -9 "$p" 2>/dev/null; fi
  done
  log "stopped: $PIDS"
else
  log "nothing running — proceeding to start"
fi

sleep 2

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  log "session $SESSION missing — creating it"
  tmux new-session -d -s "$SESSION" -c "$ROOT"
  sleep 1
fi

tmux send-keys -t "$TARGET" C-c 2>/dev/null
sleep 1
tmux send-keys -t "$TARGET" "cd $ROOT && $PY main.py" Enter
sleep 5

NEW=$(find_pids | tr '\n' ' ' | sed 's/ *$//')
if [ -n "$NEW" ]; then log "restarted ok -> $NEW"; else log "!! RESTART MAY HAVE FAILED — no agent pid found"; fi
log "=== done ==="
