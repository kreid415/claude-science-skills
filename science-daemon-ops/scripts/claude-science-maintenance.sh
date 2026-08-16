#!/usr/bin/env bash
# claude-science-maintenance.sh — periodic health/maintenance for a local
# Claude Science daemon.  Read-only by default; every destructive step is
# gated behind an explicit subcommand and refuses to run against a live DB.
#
#   status    what is running, what is listening, heap/RSS, log tails
#   maintain  snapshot the DB, then WAL-checkpoint + VACUUM + ANALYZE it
#   orphans   LIST workspace dirs with no session row (never deletes)
#
# Nothing here deletes anything.  `orphans` prints paths for you to review.

set -euo pipefail

DATA_DIR="${CS_DATA_DIR:-$HOME/.claude-science}"
DB_PATH=""
DRY_RUN=0
FORCE=0
SNAP_DIR=""

die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
warn() { printf 'warn:  %s\n' "$*" >&2; }
run()  { if (( DRY_RUN )); then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }
hdr()  { printf '\n== %s ==\n' "$*"; }

usage() {
  cat <<'EOF'
usage: claude-science-maintenance.sh [-d DATA_DIR] [-b DB_PATH] [-o SNAP_DIR]
                                     [-n] [-f] <status|maintain|orphans>

  -d DATA_DIR   data root                    (default $HOME/.claude-science)
  -b DB_PATH    primary database             (default: largest *.db found)
  -o SNAP_DIR   where `maintain` writes its  (default DATA_DIR/db-snapshots)
                pre-vacuum snapshot
  -n            dry run: print, do not execute
  -f            proceed even if the daemon appears to hold the DB open
EOF
}

# ---------------------------------------------------------------- discovery

find_db() {
  [[ -n "$DB_PATH" ]] && { [[ -f "$DB_PATH" ]] || die "no such DB: $DB_PATH"; return; }
  local -a cands=()
  while IFS= read -r f; do cands+=("$f"); done < <(
    find "$DATA_DIR" -maxdepth 3 \
         -type d -name 'db-snapshots' -prune -o \
         \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) \
         -not -name '*-wal' -not -name '*-shm' -not -name '*-journal' \
         -type f -print 2>/dev/null | sort
  )
  (( ${#cands[@]} )) || die "no database found under $DATA_DIR — pass -b explicitly"
  if (( ${#cands[@]} > 1 )); then
    printf 'database candidates under %s:\n' "$DATA_DIR" >&2
    du -h "${cands[@]}" >&2
  fi
  # largest file wins; the app DB dwarfs auxiliary ones
  DB_PATH=$(du -b "${cands[@]}" | sort -rn | head -1 | cut -f2)
}

# Is anything holding the DB open?  lsof is authoritative; the -wal/-shm
# sidecars are the fallback signal when lsof is unavailable.
db_is_live() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -- "$DB_PATH" >/dev/null 2>&1 && return 0
    return 1
  fi
  warn "lsof not installed — falling back to sidecar detection"
  [[ -e "${DB_PATH}-wal" || -e "${DB_PATH}-shm" ]]
}

find_unit() {
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl --user list-units --all --plain --no-legend 'claude-science*' 2>/dev/null \
    | awk 'NR==1 {print $1}'
}

# ------------------------------------------------------------------- status

cmd_status() {
  hdr "process"
  # -x matches the executable NAME exactly, so this script, its shell, and the
  # sandbox's socat helpers (whose cmdlines contain the data-dir path) are all
  # excluded.  A -f match would pick those up instead of the daemon.
  pgrep -a -x claude-science || echo "(no daemon process)"

  hdr "listening sockets"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null | grep -E 'claude-science|:8000|:8001' || echo "(none)"
  else
    warn "ss not found"
  fi

  hdr "resident memory"
  local pid
  pid=$(pgrep -x claude-science | head -1 || true)
  if [[ -n "$pid" ]]; then
    # RSS in kB -> MiB, plus elapsed time.  The daemon's own ceiling is 8 GiB.
    ps -o rss=,etime=,pid= -p "$pid" \
      | awk '{printf "rss=%.0f MiB  uptime=%s  pid=%s\n", $1/1024, $2, $3}'
    echo "(daemon self-imposed ceiling: 8192 MiB — restart before you reach it)"
  else
    echo "(not running)"
  fi

  hdr "database"
  find_db
  for f in "$DB_PATH" "${DB_PATH}-wal" "${DB_PATH}-shm"; do
    [[ -e "$f" ]] && ls -lh "$f"
  done
  db_is_live && echo "state: OPEN (daemon holds it)" || echo "state: closed"

  hdr "health reports (RSS-limit trips)"
  ls -lt "$DATA_DIR"/logs/health-report-*.log 2>/dev/null | head -5 \
    || echo "(none — good)"

  hdr "restart history (last 10 daemon binds)"
  { grep -h 'listening on' "$DATA_DIR"/logs/server-*.log 2>/dev/null || true; } \
    | tail -10 | grep . || echo "(no server logs)"

  hdr "stall / respawn evidence in current log"
  local cur
  cur=$(ls -t "$DATA_DIR"/logs/server-*.log 2>/dev/null | head -1 || true)
  if [[ -n "$cur" ]]; then
    printf 'log: %s\n' "$cur"
    printf 'lock-hold warnings: %s\n' "$(grep -cE 'db_lock_hold' "$cur" || true)"
    printf 'watchdog fired (respawns): %s\n' \
      "$(grep -ciE 'watchdog (fired|tripped|triggered)|respawning' "$cur" || true)"
    printf 'graceful exits: %s\n' \
      "$(grep -cE 'gracefulExit' "$cur" || true)"
    printf 'self-updates: %s\n' \
      "$(grep -cE 'restarting daemon: self-update' "$cur" || true)"
    local worst
    worst=$(grep -hoE 'held TxMutex [0-9]+ms' "$cur" 2>/dev/null \
            | grep -oE '[0-9]+' | sort -rn | head -3 | paste -sd, - || true)
    printf 'worst lock holds (ms): %s\n' "${worst:-none}"
    local rows
    rows=$(grep -hoE 'frame_message[s]? [0-9]+ rows' "$cur" 2>/dev/null \
           | grep -oE '[0-9]+' | sort -rn | head -3 | paste -sd, - || true)
    printf 'largest hydrate reads (rows): %s\n' "${rows:-none}"
  fi
}

# ----------------------------------------------------------------- maintain
# VACUUM rewrites the file and needs an exclusive lock, so the daemon must be
# stopped.  VACUUM INTO, by contrast, is safe on a live DB — that is what we
# use for the pre-flight snapshot.

cmd_maintain() {
  find_db
  command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 not installed"
  SNAP_DIR="${SNAP_DIR:-$DATA_DIR/db-snapshots}"

  if db_is_live && ! (( FORCE )); then
    local unit; unit=$(find_unit || true)
    cat >&2 <<EOF
error: the daemon still holds $DB_PATH open.  VACUUM needs it closed.
       stop it first, then re-run:
EOF
    [[ -n "$unit" ]] && printf '         systemctl --user stop %s\n' "$unit" >&2 \
                     || printf '         (quit the app, or stop its service)\n' >&2
    exit 1
  fi

  hdr "snapshot (safe even on a live DB)"
  local snap="$SNAP_DIR/$(date +%Y%m%d_%H%M%S)-$(basename "$DB_PATH")"
  run mkdir -p "$SNAP_DIR"
  run sqlite3 "$DB_PATH" "VACUUM INTO '$snap'"
  if ! (( DRY_RUN )); then
    sqlite3 "$snap" 'PRAGMA integrity_check' | head -1 | sed 's/^/snapshot integrity: /'
    ls -lh "$snap"
  fi

  hdr "size before"
  (( DRY_RUN )) || du -h "$DB_PATH"

  hdr "checkpoint + vacuum + analyze"
  run sqlite3 "$DB_PATH" 'PRAGMA wal_checkpoint(TRUNCATE);'
  run sqlite3 "$DB_PATH" 'VACUUM;'
  run sqlite3 "$DB_PATH" 'ANALYZE;'
  run sqlite3 "$DB_PATH" 'PRAGMA optimize;'

  hdr "size after"
  (( DRY_RUN )) || du -h "$DB_PATH"

  hdr "integrity"
  (( DRY_RUN )) || sqlite3 "$DB_PATH" 'PRAGMA integrity_check' | head -1

  printf '\nsnapshot kept at: %s\n' "$snap"
  printf 'restart the daemon now.\n'
}

# ------------------------------------------------------------------ orphans
# The daemon reports workspace dirs with no session row and never sweeps them
# ("operator action").  This LISTS them with sizes; deletion is yours.

cmd_orphans() {
  find_db
  command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 not installed"

  local wsroot
  wsroot=$(find "$DATA_DIR" -maxdepth 4 -type d -name workspaces 2>/dev/null | head -1)
  [[ -n "$wsroot" ]] || die "no workspaces/ directory found under $DATA_DIR"
  printf 'workspace root: %s\n' "$wsroot"

  # Frame ids known to the DB.  Read-only; safe while the daemon runs.
  local ids
  ids=$(sqlite3 -readonly "$DB_PATH" 'SELECT id FROM frames' 2>/dev/null) \
    || die "could not read frames table from $DB_PATH"

  hdr "workspace dirs with no matching session row"
  local n=0
  while IFS= read -r d; do
    local base; base=$(basename "$d")
    if ! grep -qxF "$base" <<<"$ids"; then
      du -sh "$d"
      n=$((n+1))
    fi
  done < <(find "$wsroot" -mindepth 1 -maxdepth 1 -type d | sort)
  printf '\n%d orphaned workspace dir(s).\n' "$n"
  cat <<'EOF'
These are scratch dirs; saved artifacts live in the artifact store, not here.
Review the list, then remove what you recognise as dead — e.g.
  rm -rf <path>
Do this only with the daemon stopped, and only for dirs you have reviewed.
EOF
}

# ---------------------------------------------------------------------- main

while getopts ':d:b:o:nfh' opt; do
  case "$opt" in
    d) DATA_DIR=$OPTARG ;;
    b) DB_PATH=$OPTARG ;;
    o) SNAP_DIR=$OPTARG ;;
    n) DRY_RUN=1 ;;
    f) FORCE=1 ;;
    h) usage; exit 0 ;;
    :) die "option -$OPTARG requires an argument" ;;
    ?) die "unknown option -$OPTARG" ;;
  esac
done
shift $((OPTIND - 1))

[[ -d "$DATA_DIR" ]] || die "data dir not found: $DATA_DIR"

case "${1:-status}" in
  status)   cmd_status ;;
  maintain) cmd_maintain ;;
  orphans)  cmd_orphans ;;
  *)        usage; exit 2 ;;
esac
