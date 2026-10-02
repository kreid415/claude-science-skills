# Edge cases

## Getting scheduler text
- Run in the `repl` kernel (`host.compute`); `python`/`r` kernels cannot reach the cluster. Write stdout to `./handoff/*.txt`, parse in `python`.
- Query by explicit ids (`sacct -P -j 5001,5002`). An array id returns every task, including pending ranges (`5001_[32-43%4]`), which the parser expands.
- If sacct returns nothing for ids you submitted: wrong cluster/account, accounting disabled, or typo. The parser raises `RS_EMPTY`; do not treat it as "no jobs".
- `squeue -j` on ids that have all left the queue prints an invalid-job-id message and exits non-zero. That means nothing is live; use sacct alone.
- Record `exit_code`/`stderr` of the remote command; a non-zero sacct exit is a failed query, not an empty queue.

## Time
- sacct/squeue times are cluster-local with no offset. The `#CAPTURED $(date +%Y-%m-%dT%H:%M:%S%z)` line supplies the offset; `tz=` is only a cross-check (conflicts raise `RS_TZ_CONFLICT`).
- IANA names (`America/New_York`) are accepted but ambiguous during DST changes; prefer the fixed offset from the stamp.
- Log timestamps (python logging, tqdm, file mtimes via `stat`) are in whatever zone wrote them; convert with `rs_since(ts, captured_at, tz=<that zone's offset>)`.
- A "hang" claim needs: last-write age from `rs_since`, the job state RUNNING in the same snapshot, and a stalled output count between two board snapshots.

## States
- Running: RUNNING, COMPLETING, SUSPENDED, RESIZING, SIGNALING, STAGE_OUT, CONFIGURING, STOPPED. Pending: PENDING, REQUEUED, REQUEUE_HOLD, REQUEUE_FED, RESV_DEL_HOLD. Failed: FAILED, OUT_OF_MEMORY, TIMEOUT, CANCELLED (`CANCELLED by <uid>` keeps the canceller), NODE_FAIL, BOOT_FAIL, DEADLINE, PREEMPTED, REVOKED, SPECIAL_EXIT. Anything else is `unknown`: strict mode raises `RS_STATE_UNKNOWN`; add it to the constants after reading `man sacct`.
- A parent row `COMPLETED` with a `.batch` step `OUT_OF_MEMORY` (cgroup kill of a child) becomes OUT_OF_MEMORY with an `oom_in_step` note.
- `COMPLETED` with ExitCode `N:M` nonzero is recorded as FAILED with a note.
- `TIMEOUT` with Elapsed under 90% of Timelimit gets `timeout_suspect`: some clusters report an account/association CPU-minute cap kill as TIMEOUT. Check job reason and `sacctmgr show assoc` limits for the account before raising walltimes. Without a Timelimit column the parser says it cannot tell.
- `oom_suspect`: MaxRSS at or above 95% of ReqMem with a failure or SIGKILL-type signal (9, 125, 137). ReqMem per-CPU (`c`) is multiplied by AllocCPUS.
- Pending with a reason outside {Resources, Priority, BeginTime, None} is `reason_blocking` (association limits, held jobs, dependency never satisfied, node unavailable). It stays `pending` but is counted in the headline.

## Requeues and retries
- sacct shows the latest attempt per id by default; the parser keeps the later start if duplicates appear. A live squeue row outranks any older terminal row for the same unit.
- A resubmitted unit gets a new job id: update the manifest's id column, or use `sched_key` as a callable that maps both ids to the unit.

## Manifest and outputs
- The manifest is the denominator. If you add units mid-run, rebuild from the launcher's manifest and note it in the notebook; a board with a shrinking denominator hides losses.
- Output names must encode every swept parameter; if two units can resolve to the same path, the check cannot tell them apart. Fix the naming before trusting any board.
- Check `finished_at` values carry an offset (naive values raise). For `done_valid`, finish time falls back to the scheduler End of a COMPLETED job.
- `found`/`expected` in the check detail are summed into `subunits` (e.g. seeds present across all checked outputs).

## ETA
- Gaps between consecutive completions are resampled (2000 draws, fixed seed) and summed over the outstanding count. Equal-length units finishing in waves yield a conservative-looking band; heavy-tailed unit cost yields a wide one.
- Failed and `done_invalid` units are excluded from remaining work and reported as needing resubmission; their reruns will extend the true end time.
- A stall warning appears when the time since the last completion exceeds 3x the median gap. It is a prompt to inspect running units, not a diagnosis.
