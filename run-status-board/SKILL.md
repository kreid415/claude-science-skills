---
name: run-status-board
description: "Use when the user asks about a long-running remote or cluster job: 'what is the status', 'are results done?', 'how is it going', 'when do you expect the experiments to conclude / how long until results', or before YOU say a run, wave, array, shard or seed sweep is finished, hung, timed out, walled or stuck. Builds ONE status board (status_board.md + status_board.json) from the expected unit manifest, output files that pass a validity check, and sacct/squeue text, with per-unit states (done_valid, done_invalid, running, pending, failed, missing), a throughput ETA with an interval (refused when too few units finished) and explicit timezones. Also use when a .done marker or exit code 0 is being trusted, when SLURM shows PENDING / TIMEOUT / OUT_OF_MEMORY array tasks, when comparing a log timestamp with a clock, or when resuming a run after a context reset. Every status answer must quote the board and its timestamp."
---

# run-status-board

One status artifact per long run. It is computed only from three sources and is the only thing you quote when asked about progress:

1. the **expected manifest** (every unit that must exist: shard, config row, seed),
2. **output files actually present and valid** (judged by a check you supply, never by a marker or exit code),
3. **scheduler accounting text** (`sacct`/`squeue`), captured with a cluster-clock stamp.

Why: past runs reported "complete" from `.done` markers on shards with failed or OOM'd seeds, read PENDING array tasks as timed out, and misread cluster clocks as hangs. All came from answering from memory or a single weak signal. The board makes the denominator, the validity test and the snapshot time explicit.

Helpers load from `kernel.py` (prefix `rs_`). They raise `ValueError` / `RuntimeError` / `TypeError` with an `RS_...` code and an actionable message; do not wrap them in try/except that substitutes a default.

## Workflow

1. **Manifest.** Load the launcher's manifest (list of dicts or DataFrame) of ALL expected units. Never build it from files found. The unit key must encode every swept parameter (dataset, config, seed), or outputs overwrite each other. Add a column holding the scheduler id (`'5001_17'`, array job id + task) or job name per unit.
2. **Scheduler text (repl kernel, read-only).** Run `sacct` and `squeue` on the cluster through the remote-compute tool and write the stdout to `./handoff/`. Load `remote-compute-ssh` for exact calls and `compute_details` for the provider's login/activation notes. Query **explicit job ids only**.
   ```python
   # repl tool
   c = host.compute.create("<provider>")          # name from list_compute
   ids = ",".join(JOB_IDS)                        # explicit ids, e.g. ["5001", "5002"]
   cap = 'echo "#CAPTURED $(date +%Y-%m-%dT%H:%M:%S%z)"; '
   r = c.call_command(cap + f"sacct -P -j {ids} --format=JobID,JobName,State,ExitCode,Elapsed,Start,End,NodeList,ReqMem,MaxRSS,Timelimit,AllocCPUS",
                      intent="read-only job accounting for status board", login_shell=True)
   r.raise_for_status(); open("handoff/sacct.txt", "w").write(r.stdout)
   r = c.call_command(cap + f"squeue -j {ids} -o '%i|%j|%T|%M|%l|%V|%S|%R' 2>&1", intent="read-only queue state", login_shell=True)
   open("handoff/squeue.txt", "w").write(r.stdout)   # exit != 0 with 'Invalid job id' = nothing live; skip squeue
   ```
   The `#CAPTURED` line is mandatory: sacct times carry no offset, and the stamp gives both the cluster timezone and the snapshot age. Keep the sacct `--format` header (no `-n`, no `-X`; steps carry MaxRSS and OOM state).
3. **Parse (python kernel).** `sa = rs_parse_sacct(open("handoff/sacct.txt").read())`; `sq = rs_parse_squeue(...)`. Check `sa["state_counts"]` and `sa["warnings"]`.
4. **Output check.** Write `check(row)` returning `None` when nothing was written, else `(valid, reason, {"found": n, "expected": m, "finished_at": iso_with_offset})`. It must count expected seeds/files, require finite values and zero failed rows. See "Pairing with a completion gate".
5. **Board.** `b = rs_board(manifest, "unit", check, [sa, sq], started_at=..., sched_key="task")`. Strict mode raises on empty/duplicate manifests, unmapped scheduler rows, undated or stale (>15 min) snapshots, and checks that raise.
6. **ETA.** `eta = rs_eta(b, history=rs_history(root), strict=False)`. It refuses (and says why) below 5 timestamped completions.
7. **Save.** `rs_save(b, root, eta)`, then `save_artifacts(["status_board.md", "status_board.json"], version_of={...})` so the board is one artifact history (pass the earlier artifact ids). Commit the manifest and check script with the experiment code.
8. **Answer** with `b["headline"]`, the `generated_at` and scheduler `captured_at` times, the ETA line from the board, and the `needs_action` units. Nothing else about progress.

## Helpers

| helper | purpose |
|---|---|
| `rs_parse_sacct(text, tz=None, strict=True)` | rows per job/array task; folds `.batch/.extern/.N` steps; expands `123_[32-43%4]`; OOM-in-step, `COMPLETED` with nonzero exit, TIMEOUT-vs-Timelimit and OOM-suspect notes; ISO times with offset |
| `rs_parse_squeue(text, tz=None, strict=True)` | live jobs; pending reasons; `reason_blocking` for limit-type reasons |
| `rs_board(manifest_rows, unit_key, output_check, sched_rows=None, started_at=None, sched_key=None, now=None, max_sched_age_s=900, allow_presence_only=False, strict=True)` | per-unit state, totals, headline, anomalies, `needs_action` |
| `rs_eta(board, history=None, min_done=5, interval=0.8, seed=0, now=None, strict=True)` | bootstrap throughput ETA band; `refused` + reason when data are thin |
| `rs_render(board, eta=None)` / `rs_save(board, root, eta=None)` / `rs_history(root)` | markdown, md+json+`status_history.jsonl`, history reload |
| `rs_since(ts, now, tz=None)` | age of a log/file timestamp with explicit timezones; raises on negative age |
| `rs_ts`, `rs_tz`, `rs_dur`, `rs_mem_mb`, `rs_jobid`, `rs_state`, `rs_state_category`, `rs_norm_check` | building blocks |

`sched_key`: `None` (scheduler JobName equals unit key), a manifest column holding the job id (`'5001_17'`) or job name, or a callable `sched_row -> unit key`. Array tasks share one JobName, so use the job-id column for arrays.

## Unit states

| state | meaning |
|---|---|
| `done_valid` | output present and passes the check (a scheduler failure on top is reported as a `conflict` note) |
| `done_invalid` | output present, fails the check, no live job and no scheduler failure: exit-0 or marker "done" that is not done |
| `running` / `pending` | scheduler shows a live job (a live row outranks older terminal rows; partial output while running stays `running`) |
| `failed` | scheduler terminal failure (FAILED, OUT_OF_MEMORY, TIMEOUT, CANCELLED, NODE_FAIL, ...) and no valid output |
| `missing` | no valid output, no scheduler record or scheduler COMPLETED with nothing written (an anomaly) |

Totals always sum to the manifest size. "Done" means `done_valid == n_units`.

## Rules for status answers

- Say "done/complete/verified" only when `totals.done_valid == n_units`. Otherwise give the headline fraction and name the states.
- Say a task "timed out/walled" only when its scheduler state is TIMEOUT and `timeout_suspect` is absent. Empty logs on an array task mean nothing: check PENDING and its reason (a limit reason such as an association cap means it will not start until cleared).
- Say "hung/stuck" only after `rs_since(last_log_time, captured_at, tz)` plus the running job's state support it; a negative or surprising age is a timezone error first.
- Give an ETA only from `rs_eta`; when refused, say "not enough completions yet" and report throughput so far.
- Quote `generated_at` and `captured_at`. If the board is older than the question or the user changed something, rebuild first.
- Do not infer progress from sibling units, earlier waves, or your memory of the last check.

## Pairing with a completion gate

`rs_board` delegates validity to your `check`. Use `fl_completion_gate` from the companion skill `fail-loud-pipelines` (load both skills); do not rely on a `.done` marker:

```python
def check(row):
    exp = expected_for(row)                        # exact relative paths for this unit (every seed/config), no globs
    unit_dir = dir_for(row)
    if not os.path.isdir(unit_dir):
        return None                                # not started -> state comes from the scheduler
    g = fl_completion_gate(exp, unit_dir, check_finite=True, strict=False)
    bad = {k: g[k] for k in ("missing", "empty", "nonfinite", "unreadable", "failed_rows") if g[k]}
    return (bool(g["ok"]), "; ".join(f"{k}={v[:3]}" for k, v in bad.items()), {"expected": g["n_expected"]})
```
A glob template as `output_check` proves existence only; it needs `allow_presence_only=True` and the board then prints PRESENCE ONLY.

## Safety

Scheduler access is read-only and scoped to ids you submitted (`-j ids`). Never run account-wide scheduler commands (no `scancel -u`, no cancelling "duplicate-looking" jobs): other agents share the account. Cancel only explicit job ids the user approved.

## Provenance

The checks in this skill come from a review of real failures in AI-assisted research sessions. `tests/` in the repository holds synthetic fixtures that reproduce each failure pattern: known-bad inputs must fail, known-good inputs must pass.

## Limits

- ETA assumes constant concurrency and equal-cost units; wide variance in unit cost widens the band, it does not remove the bias. Not a substitute for queue-wait knowledge.
- `rs_parse_*` target Slurm. Other schedulers need a new parser that emits the same row fields (`job_id, job_name, state, category, exit_code, elapsed_s, start, end, reason, notes`).
- sacct history may have aged out; pass `history=` so earlier completion times still feed the ETA.
- More in `references/edge_cases.md` (state vocabulary, array syntax, DST, requeues).
