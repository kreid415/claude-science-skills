# Log signatures

Verbatim lines from a self-hosted Claude Science daemon, grouped by what they
mean. Grep for the exact string — the daemon is explicit about its own state
transitions, so matching text is faster and more reliable than inferring from
symptoms. Values shown (pids, ports, versions, millisecond figures) are from
one observed install on 2026-08-08; treat the *shape* as stable and the numbers
as examples.

Logs live in `<data-dir>/logs/`. Filenames look date-stamped
(`server-20260806.log`) but are **per-app-start**, not per-day — so a file's
mtime is when that run *ended*. A run that stopped days after its filename date
is normal, and the gap between one file's last write and the next file's first
write is a restart window.

## Contents

- [Graceful restart and self-update](#graceful-restart-and-self-update)
- [Normal boot sequence](#normal-boot-sequence)
- [Memory-limit trips](#memory-limit-trips)
- [Lock contention and stalls](#lock-contention-and-stalls)
- [Housekeeping notices](#housekeeping-notices)
- [Benign noise](#benign-noise)
- [What a real fault looks like](#what-a-real-fault-looks-like)

## Graceful restart and self-update

```
[claude-science] restarting daemon: self-update → a8cf9eae
[claude-science] selfRestart handoff: successor bound and claimed the lockfile (pid 1235955) — exiting
[daemon] gracefulExit(0) via SIGTERM
```

Exit code 0, SIGTERM, no exception: designed behaviour, not a crash. The
`self-update` line names the reason; a `gracefulExit(0)` **without** it is
either a user-initiated stop or a watchdog respawn (see below). This sequence
is what makes an app appear to "relaunch itself after being quit".

Version numbers on either side of the gap tell you which: an update shows
different versions in the two `listening on` lines, a watchdog respawn shows
the same version.

## Normal boot sequence

```
[claude-science] data dir: /home/<user>/.claude-science (ext4)
[migrate] 0ms total · 0 stmts · 0 slow (≥100ms)
[daemon] boot phases (2775ms total, pre-bind): create-services:ensure-profile 1315ms, sandbox-init 977ms, …
[daemon] sandbox origin: http://localhost:8001/mcp_apps
[daemon] warming 24 built-in MCP connectors...
[daemon] stall watchdog armed (effective action: respawn)
[daemon] listening on 127.0.0.1:8000 (pid 1238063, version 0.1.27)
  Web UI → http://localhost:8000/?nonce=<64 hex chars>
  Remote? Forward both ports: 8000 (app) and 8001 (sandbox content)
  This link is a one-time password, not a bookmark: it logs one
  browser tab in, then expires (3 min). The tab stays logged in
  until the daemon restarts.
  Seeing "session expired", or need a fresh link?  claude-science url
[daemon] MCP warmup complete (4389ms)
```

Three things to extract:

- **`listening on`** — grep all logs for this to get the full restart history
  with pids and versions. Consecutive lines with a gap between them are your
  down-windows; measure them against the timestamps of any refused-connection
  bursts the user reported.
- **`stall watchdog armed`** — printed at *every* boot. It means the watchdog
  exists, **not** that it fired. Do not count these as respawns.
- **`Remote? Forward both ports`** — the daemon's own forwarding requirement.
  Confirm the actual port numbers from `ss -ltnp` rather than assuming.

## Memory-limit trips

`logs/health-report-<ISO timestamp>.log`:

```
Operon daemon health report — 2026-08-05T23:38:59.226Z
trigger: rss_over_limit
pid=63250 version=0.1.25 uptime=901175s platform=linux arch=x64 active_frames=1

== process.memoryUsage ==
rss=8273.3 MB  heapUsed=5839.1 MB  heapTotal=2011.6 MB  external=2184.6 MB
host_total=96501.5 MB  rss_limit=8192.0 MB  rss_fraction=0.086

== event loop ==
lag_max=6ms  lag_mean=1.8ms  samples=60  (probe every 500ms)
```

The existence of this file is itself the alert. Read it as follows:

- `rss` vs `rss_limit` — the ceiling is the **daemon's own**, independent of
  host RAM.
- `rss_fraction` — resident memory as a fraction of `host_total`. A small
  fraction (0.086 here, on a 96 GB host) rules out an OOM killer, which matters
  because `dmesg` is usually unreadable from an unprivileged account
  (`dmesg: read kernel buffer failed: Operation not permitted`).
- `uptime` — seconds. 901175 s is 10.4 days, i.e. heap climbed over sustained
  operation rather than spiking.
- `lag_max` / `lag_mean` — a healthy event loop here means the memory problem
  had not yet become a responsiveness problem.
- `heap census: skipped — JS heap … exceeds the … pre-walk guard` — expected at
  this size; the numeric counters are the measurement.

Companion signature in the same period, a listener leak:

```
MaxListenersExceededWarning: Possible EventTarget memory leak detected.
11 close listeners added to [WriteStream]. MaxListeners is undefined.
  emitter: WriteStream { fd: 94, bytesWritten: 2259737, … }
```

Repeated `once()` registrations on a stream that is never released. Not fatal
on its own; contributes to the growth above. `count:` rises over time.

## Lock contention and stalls

```
[perf] db_lock_hold: stmt held TxMutex 4142ms — op=insert into "execution_log" …
[perf] db_lock_wait: 3901ms queued behind TxMutex — op=insert into "execution_log" …
[perf][ro] db_large_result: select "msg_json" from "frame_messages" … 6287 rows 334.2ms
[perf][ro] db_large_result: select "msg_json" from "frame_messages" … 6222 rows 791.4ms
[perf][ro] db_lock_hold: stmt held TxMutex 1112ms — op=select "id", "parent_frame_id", …
[perf] db_lock_hold: batch drain held TxMutex 403ms — 1 group(s): a3771020
[SAVE_TIMING] 418e2055 total=1463ms (ser=105.0ms db=1358ms) msgs=1083 delta[1080..1082)
```

One mutex serializes all database access, so a long `db_lock_hold` produces
matching `db_lock_wait` lines for everything queued behind it. The
`frame_messages` reads are session hydration: the row count is the session's
message count, so `6287 rows` is a session with ~6287 messages being
re-serialized on every hydrate. That row count is the number to quote when
explaining why session size matters — it converts a behavioural
recommendation into a measurement.

`[SAVE_TIMING]` gives per-save cost with the frame id and message count;
`msgs=` climbing into the thousands on one frame is the same story from the
write side.

## Housekeeping notices

```
[claude-science] note: /home/<user>/.claude-science/operon-cli.db has un-adopted data;
  merge into this org with: claude-science import /home/<user>/.claude-science
```

A second database in the data root holds data the current org cannot see.
Printed at every boot until resolved. Use the command the log prints; snapshot
first.

```
{"msg":"[workspace-backlog] reconciled 28 workspace dir(s): swept 4 expired,
  re-armed 7 timer(s), left 21 with no frame row (operator action — never auto-swept)"}
```

`left N with no frame row` counts orphaned scratch directories the daemon will
never remove by design. Artifacts are in the artifact store, not these
directories.

```
[compute] concurrent cells wrote to the workspace for frame <id> —
  files_written/auto-view attribution dropped for this cell
```

Backgrounded cells raced in one workspace. Data is fine; file provenance for
that cell is lost.

## Benign noise

Mention these only to pre-empt worry:

```
[pricing] No pricing for model 'claude-sonnet-5', using Sonnet fallback ($3/$15 per MTok)
[MCPPool] '<server>' subprocess is dead but the connection was never marked closed — evicting stale warm entry
[dependency-extraction] filterInputsForArtifacts hit max_tokens (outputs=1, budget=650) — per-output attributions may be lost to truncation
[conda] scanEnvsBatched nonzero (-1):
socat[HTTP]: … W exiting on signal 15
[Prune] Available tools: 29
```

Cost display, self-healing connector eviction, incomplete-but-not-wrong
lineage attribution, a shutdown-time env scan, sandbox helper processes being
torn down with the daemon, and routine tool-list pruning.

## What a real fault looks like

None of the above. Look for a stack trace with application frames, a non-zero
exit code, `uncaughtException` / `unhandledRejection`, `SIGSEGV`, `SIGKILL`, or
a kernel `Killed process … out of memory`. If the log simply stops mid-stream
with no exit line at all, suspect an external kill — and since `dmesg` is
typically unreadable, fall back to `journalctl --user` for the unit's own view
of how the process died.
