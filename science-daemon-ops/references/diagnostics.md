# Diagnostic ladder

Commands for the **user** to run on the machine hosting the daemon. Your
sandbox has no host process table and no read access to the real data
directory, so an empty result from inside the sandbox proves nothing — always
hand these over rather than attempting them.

Prefer `scripts/claude-science-maintenance.sh status`, which collects all of
this in one paste. Use the individual commands when the script is not on the
host yet, or when you want to narrow one specific question.

## Is it running, and on which ports?

```bash
pgrep -a -x claude-science
ss -ltnp | grep -E 'claude-science|:8000|:8001'
```

Match the executable **name** (`pgrep -x`), not the full command line: sandbox
helper processes (socat, and similar) carry the data-directory path in their
argv and a `pgrep -f` match returns those instead of the daemon.

Read the interface, not just the port. Everything bound to `127.0.0.1` is
unreachable from any other address, which is the single most common cause of a
permanently refused port forward. Ephemeral high ports belong to per-session
kernels and are renumbered on every restart — never pin a forward to one.

## How much memory, and for how long?

```bash
ps -o rss=,etime= -p "$(pgrep -x claude-science | head -1)"
ls -lt <data-dir>/logs/health-report-*.log 2>/dev/null | head
```

RSS is in kilobytes. Compare against the daemon's own ceiling (8192 MB on the
observed install) rather than host RAM. Any `health-report-*.log` file means
the ceiling was tripped at that timestamp; `references/log-signatures.md`
explains its fields.

## What is the restart history?

```bash
grep -h 'listening on' <data-dir>/logs/server-*.log | tail -20
grep -hE 'gracefulExit|selfRestart|self-update' <data-dir>/logs/server-*.log | tail -20
```

Pids and versions across restarts, then the reason for each stop. Gaps between
consecutive `listening on` lines are the down-windows to compare against the
timestamps of whatever the user reported.

## Is contention building?

```bash
CUR=$(ls -t <data-dir>/logs/server-*.log | head -1)
grep -c 'db_lock_hold' "$CUR"
grep -hoE 'held TxMutex [0-9]+ms' "$CUR" | grep -oE '[0-9]+' | sort -rn | head -5
grep -hoE 'frame_message[s]? [0-9]+ rows' "$CUR" | grep -oE '[0-9]+' | sort -rn | head -5
```

Worst lock holds in milliseconds and the largest session-hydration reads in
rows. Under `set -euo pipefail`, a zero-match `grep` exits 1 — append
`|| true` if you fold these into a script.

## Why did it die?

```bash
tail -100 <data-dir>/logs/server-<previous-run>.log
grep -inE 'fatal|uncaught|unhandled|panic|SIGSEGV|SIGKILL|out of memory|heap' \
  <data-dir>/logs/server-*.log | tail -20
journalctl --user -n 200 --no-pager | grep -i claude
dmesg -T | grep -iE 'oom|killed process'
```

The reason is at the end of the log that **stopped**, not the current one —
pick the file by mtime, not by the date in its name (filenames are
per-app-start). `dmesg` commonly fails with `Operation not permitted` on an
unprivileged account; use the health report's `rss_fraction` to rule out host
memory exhaustion instead, and `journalctl --user` for the service's view of
the exit.

## Is the forward itself wrong?

```bash
pgrep -af 'ssh .*-[LR]'
grep -nE 'LocalForward|RemoteForward|DynamicForward' ~/.ssh/config
ssh -v -N -L 8000:localhost:8000 <host>     # prints the forward's destination
```

The `-v` output names the destination it tried (`connect_to localhost port
8000: failed`), which is what you compare against the `ss` output. An
inherited `LocalForward` in `~/.ssh/config` aimed at a service that is not
running produces identical refusals with no connection to the app at all.

## Database identity and integrity

```bash
find <data-dir> -maxdepth 3 \( -name '*.db' -o -name '*.sqlite*' \) \
  -not -name '*-wal' -not -name '*-shm' -type f -exec du -h {} +
lsof -- <db-path>                                    # open? then do not VACUUM
sqlite3 <db-path> 'PRAGMA integrity_check'           # read-only, safe live
```

Expect more than one database file; the app's own is the largest, and a
snapshot directory (if maintenance has run) will contain decoys — exclude it
when identifying the primary. `lsof` is the authoritative liveness check; the
`-wal`/`-shm` sidecars are the fallback signal when `lsof` is unavailable.

To copy a database that is being actively written, use
`sqlite3 <db> "VACUUM INTO '<dest>'"` — plain `cp`/`rsync` can produce a torn
file that reports success and fails to open.
