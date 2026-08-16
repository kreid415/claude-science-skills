---
name: science-daemon-ops
description: Diagnose and maintain a self-hosted Claude Science daemon on the user's own machine — the app process itself, not the science it runs. Use this whenever the user reports that the app is crashing, restarting or relaunching itself, hanging, feeling slow, losing its session, showing "session expired", refusing connections, or emitting repeated `channel N: open failed: connect failed: Connection refused` lines from ssh; also for routine upkeep — daemon health, resident-memory growth, database bloat or VACUUM, orphaned workspace directories, un-adopted data warnings, and correct SSH port forwarding for remote access. Trigger it even when the user never says "daemon" and even when they are sure the app crashed: most reported crashes are graceful self-update restarts, and this skill tells those apart from real faults using the daemon's own logs. Not for analysis work done inside the app, and not for remote cluster or SLURM job failures (that is remote-compute-ssh).
---

# Claude Science daemon operations

The self-hosted daemon is an ordinary long-lived local service: one process,
two loopback ports, one SQLite database, a log directory. Almost every
"Claude Science crashed" report resolves into one of four things, and the
daemon's own log tells you which within a minute. Work from evidence in that
log rather than from the symptom the user noticed — the symptom is usually
downstream of the cause.

## First: you cannot see the host

Your sandbox has no host process table and no view of the user's real data
directory (`request_host_access` on `~/.claude-science` is refused). So you
**cannot run diagnostics yourself** — you write the commands, the user pastes
back the output, you read it. Say this plainly rather than attempting probes
that will come back empty; an empty result from inside the sandbox proves
nothing about the host.

Ask for output from `scripts/claude-science-maintenance.sh status` when you can
(it collects everything at once). When the user prefers single commands, or the
script is not on their machine yet, the ladder in
`references/diagnostics.md` gives the individual commands and what each one
rules in or out.

## The four causes, and how to tell them apart

Read `references/log-signatures.md` for the verbatim log lines — the daemon is
unusually explicit about its own state transitions, and matching the exact
string is faster than reasoning about symptoms.

**1. A graceful restart (most common).** Look for `restarting daemon:
self-update`, `selfRestart handoff: successor bound and claimed the lockfile`,
and `gracefulExit(0) via SIGTERM`, followed ~10 s later by a new pid
`listening on 127.0.0.1:8000`. Exit code 0 and no exception means nothing
crashed. The user's symptom — refused connections, a dead tab, "session
expired" — is the ~10 second gap while no process held the port. Reloading the
tab with a fresh login URL is the entire fix.

**2. A watchdog respawn.** The daemon arms a stall watchdog whose action is
`respawn`, so a long enough event-loop stall ends in SIGTERM with no
exception — indistinguishable from case 1 at the exit line, but distinguished
by *no* accompanying self-update line and by `db_lock_hold` warnings in the
seconds before. This is a real problem; the cause is almost always oversized
sessions (see Root causes).

**3. Its own memory ceiling.** The daemon enforces a resident-set limit
independent of host RAM and writes `logs/health-report-<timestamp>.log` with
`trigger: rss_over_limit` when it trips. Read that file: it reports `rss`,
`heapUsed`, `rss_limit`, and `rss_fraction` (resident memory as a fraction of
host total). A small `rss_fraction` with a tripped limit means the ceiling is
the daemon's own, not the machine's — so an OOM killer is not involved.

**4. An actual fault.** A stack trace, a non-zero exit, a kernel OOM kill. Only
now are the app logs interesting line-by-line. Note that `dmesg` is often
unreadable from an unprivileged account (`Operation not permitted`); the health
report's `rss_fraction` substitutes for ruling out host memory exhaustion.

## Connection-refused symptoms: read the timing, not the message

`channel N: open failed: connect failed: Connection refused` is OpenSSH client
stderr. With `ssh -L`, the client accepts a local connection and asks the far
sshd to connect onward; when nothing is listening there, sshd refuses and the
client prints that line. One line is one attempt, and a repeated identical
channel number is a browser reconnect loop retrying about once a second.

The duration is the diagnosis:

- **A burst lasting ~10 seconds, then quiet** — a restart window (cause 1 or
  2). The local forward listener survives a daemon restart, so restarting ssh
  is unnecessary; reload the tab.
- **Refusals that never stop** — the forward itself is wrong. Two failure
  modes dominate: only one of the two required ports is forwarded, or the
  remote side of `-L` names the host's hostname / LAN IP / tailnet address
  while the daemon binds loopback only.

The daemon prints its own forwarding requirement at every boot (`Remote?
Forward both ports: …`). Confirm the actual ports from `ss -ltnp` rather than
assuming the defaults, then have the user forward both with `localhost` as the
remote side:

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -L 8000:localhost:8000 -L 8001:localhost:8001 <host>
```

`ExitOnForwardFailure` matters here: without it a broken forward hangs
silently, which is how these reports usually reach you in the first place.

Two traps worth naming before the user hits them: the per-kernel ephemeral
ports in `ss` output are renumbered on every restart, so a forward or
bookmarked URL pinned to one is refused permanently even while the app is
healthy; and the login URL carries a one-time nonce that expires when the
daemon restarts, so `claude-science url` is part of the fix, not an extra step.

If the host is reachable by a remote desktop (NoMachine, VNC, RDP), no tunnel
is needed for the UI at all — and any ssh client still emitting refusals is
probably one the app spawned for cluster compute, writing to the stderr that
launched the daemon. Check `~/.ssh/config` for
`LocalForward`/`RemoteForward`/`DynamicForward` entries pointing at services
that are not running.

## Root causes worth fixing, in order of leverage

**Oversized sessions.** All database access serializes through one global
transaction mutex, and message hydration re-reads every message row in a
session under that lock. A session with thousands of messages turns each
hydrate into a multi-hundred-millisecond hold; long holds queue everything
behind them and can end in a watchdog respawn. `status` reports the largest
hydrate reads in rows and the worst lock holds in ms, so this is measurable
rather than speculative. The fix is behavioural and entirely in the user's
hands: start new sessions at task boundaries — topic change, new dataset,
abandoned approach — because continuity comes from artifacts and memory, not
from session length.

**Heap growth with uptime.** Resident memory climbs over days of continuous
operation (a listener leak on a log write stream contributes), eventually
tripping the daemon's own ceiling at an arbitrary moment. A deliberate weekly
restart costs ~10 seconds; a ceiling trip costs whatever was in flight.

**Database bloat.** Shorter B-trees and current statistics mean shorter lock
holds, so a monthly checkpoint + VACUUM + ANALYZE directly reduces the
contention above.

**Concurrent writers in one workspace.** `concurrent cells wrote to the
workspace … attribution dropped` means backgrounded cells raced; harmless to
data, but it costs file provenance. Sequence them.

## Maintenance

`scripts/claude-science-maintenance.sh` is read-only by default and refuses
destructive work while the daemon holds the database open. Give the user the
path and the subcommand; never paste its contents into chat.

The skill directory is mounted read-only, so the file has no execute bit —
invoke it through `bash`, or have the user copy it somewhere convenient
(`cp <skill-dir>/scripts/claude-science-maintenance.sh ~/bin/ && chmod +x …`)
if they will run it on a schedule.

```bash
bash claude-science-maintenance.sh status      # safe at any time
bash claude-science-maintenance.sh -n maintain # dry run — prints, changes nothing
bash claude-science-maintenance.sh maintain    # requires the daemon stopped
bash claude-science-maintenance.sh orphans     # lists only, never deletes
```

It defaults to `$HOME/.claude-science`; pass `-d <dir>` for a different data
root, and `-b <path>` if database auto-detection picks the wrong file.

`status` is the report to ask for first: liveness, listening sockets, resident
memory against the ceiling, the last ten daemon binds, lock-hold counts, worst
holds in ms, largest hydrate reads in rows, and any health reports. Rising
numbers across successive runs are the early warning that a respawn is coming.

`maintain` writes a `VACUUM INTO` snapshot and integrity-checks it *before*
touching the original. `VACUUM INTO` is consistent even on a database being
actively written, which makes it the right primitive for backups too — a
backup does not require quitting the app, though a full `VACUUM` does require
an exclusive lock and therefore a stopped daemon.

Suggested cadence: `status` and a restart weekly; `maintain` monthly with the
daemon stopped; `orphans` when disk use is a question.

## Housekeeping items the boot log flags

**Un-adopted data.** A note naming a second database file in the data root
whose contents the current org cannot see, together with the
`claude-science import` command that merges it. Have the user snapshot first,
then run the command the log printed — do not invent the invocation.

**Orphaned workspaces.** The workspace reconcile line reports directories with
no matching session row, marked `operator action — never auto-swept`. These are
scratch directories; saved artifacts live in the artifact store, not there, so
they are safe to clear once reviewed. Run `orphans` to list them with sizes,
have the user review, then remove with the daemon stopped. Never remove them
on the user's behalf.

## Reporting back

Lead with whether it crashed, because that is the user's actual question and
the answer is usually no. Then give the timeline you read from the log
(timestamps, pids, versions), the immediate fix, and only then the underlying
issue worth changing. Quote the log lines you relied on — the user pasted them
and can check your reading. Resist prescribing a restart or a VACUUM before you
have evidence of which of the four causes you are looking at; three of them
need different fixes and one needs nothing at all.
