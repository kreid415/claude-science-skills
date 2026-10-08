---
name: job-watch
description: "Use when you are about to launch, or have just launched, any job expected to run longer than about 10 minutes (local process, nohup/setsid background run, SLURM sbatch) and the chat would otherwise poll it with repeated tool calls; also when a SLURM job is close to or has hit its time limit (TIMEOUT), ran out of memory, lost its node (NODE_FAIL/PREEMPTED), or a long local job crashed, stalled or was killed and must be re-run or resumed. A stdlib-only supervisor (jw.py) runs the job, watches it with no LLM calls, resubmits SLURM jobs with a longer --time / more --mem, asks restartable programs to checkpoint before the limit (USR1, exit 85 = continue), restarts stalled or killed local jobs, validates the output, and ends with one report line and an exit code. Replaces sleep-and-poll loops."
---

# job-watch

One supervisor, `scripts/jw.py` (Python 3.8+, stdlib only, no LLM), per long job. The chat launches it, then waits on a single completion event instead of polling.

## When to use
Any job longer than ~10 min, or any job whose end you would otherwise check repeatedly. Do not poll with the model; do not write `sleep N; squeue` loops. Short jobs (<10 min): run in the foreground.

## Install (once per host)
`mkdir -p $HOME/job-watch && cp scripts/jw.py $HOME/job-watch/` (stage it as a `submit_job` input on remote hosts). Needs only `python3` (3.8+); on SLURM hosts `sbatch squeue sacct scancel` on PATH.

## Commands
```
jw.py start  --state DIR --kind local|slurm (--cmd CMD | --script FILE | --attach-pid PID | --attach-job ID) [options]
jw.py watch  --state DIR [--interval 60] [--deadline-min 25]    # ticks until terminal or deadline
jw.py tick | status | stop --state DIR
```
`--state` is a directory on a filesystem the watcher can always reach (cluster: scratch; never a node-local /tmp). All progress lives in `state.json`; the watcher can die and a new one continues the same job (`start` is not repeated).

Common options: `--workdir`, `--expect FILE` (repeatable, must exist and be non-empty), `--validate CMD` (exit 0 = output valid), `--retries N` (plain failures, default 1), `--max-attempts N` (total segments, default 6; 500 in `--cycle` mode), `--tag`.
Local: `--stall-min M` (kill and retry if log/`--progress-file` stops growing), `--resume-cmd CMD`.
SLURM: `--time HH:MM:SS|max`, `--cycle`, `--max-cycles`, `--follow-file`, `--time-factor 1.5`, `--max-time`, `--mem 8G`, `--mem-factor 1.5`, `--max-mem`, `--sbatch-args "--partition=... --account=..."`, `--restartable`, `--signal-margin 600`, `--signal-mode sbatch|scancel|none`.

The command sees `JW_ATTEMPT` (1,2,...), `JW_RESUME` (0 first time, 1 on every re-run), `JW_SEGMENT`, `JW_STATE`.

## Exit codes (watch/tick)
0 done (validated) | 10 still running, deadline reached: run `watch` again | 20 failed | 30 needs a human | 40 stopped | 41 another watcher holds the lock | 2 usage/state error. The last stdout line is JSON with `report_line`; quote it.

## What it does on each outcome (SLURM)
| Outcome | Action |
|---|---|
| COMPLETED | run `--expect`/`--validate`; pass = done; fail = retry if retries remain, else failed |
| TIMEOUT | resubmit with `--time` x `--time-factor` (up to `--max-time`; default cap 4x). At the cap: continue unchanged only with `--restartable`, otherwise needs_human |
| OUT_OF_MEMORY | resubmit with `--mem` x `--mem-factor` (cap `--max-mem`, default 4x), else needs_human |
| NODE_FAIL / PREEMPTED / BOOT_FAIL / REQUEUED | resubmit unchanged |
| FAILED exit 85 | checkpointed on request: continue (no retry used) |
| FAILED other | retry `--retries` times, then failed |
| CANCELLED | someone else's decision: needs_human, never resubmit |
Limits: `--max-attempts` segments, then needs_human. Only job ids this state recorded are ever queried or cancelled (never `-u`, never account-wide: other agents share these accounts).

## Jobs designed to run to the wall and continue (cycle mode)
Many jobs are meant to use the full job time and then continue from a checkpoint as the end approaches. For these a time-limit hit is expected, not a failure, and the time must not grow. Use:
- `--cycle` with `--time max` (the partition limit, read with `sinfo` from `--sbatch-args "--partition=P"`) or an explicit `--time`. The program checkpoints on USR1 (sent `--signal-margin` seconds before the end; default 600) and exits; job-watch submits the next segment at the same `--time` with `JW_RESUME=1`. A TIMEOUT instead of a clean checkpoint is also continued. The segment limit is `--max-cycles` (default 500), not `--max-attempts`.
- Guards against a loop that makes no progress: needs_human when 3 consecutive segments end in under 25% of their limit without being asked to stop, or when `--progress-file` (repeatable, globs allowed, relative to `--workdir`) is unchanged across 2 consecutive cycles. Always pass `--progress-file` pointing at the checkpoint for long chains.
- If the program resubmits itself near the end, turn its own resubmission off and let job-watch submit every segment with `--cycle` (user decision 2026-10-08): one submitter, one record of job ids, and the no-progress guards apply. Never run both: a self-resubmitting program under `--cycle` gives two successors per segment. Fallback only when the self-resubmission cannot be disabled: have the program write the new job id to a file and pass `--follow-file FILE`; when a segment ends (COMPLETED but not valid, TIMEOUT, exit 85), job-watch tracks the declared successor instead of submitting another. If the final job's output validates, the chain ends `done`; a successor left queued by the program is not cancelled (cancel it yourself).
Validity still comes only from `--expect`/`--validate`; a cycle that exits 0 without valid output after a checkpoint request is continued, never reported done.

## Time-limit approach: checkpoint before the wall
Default `--signal-mode sbatch` adds `--signal=B:USR1@600`; the generated wrapper forwards USR1 to the program and turns a nonzero exit after the signal into exit 85. If the program checkpoints on USR1 and exits, the next segment starts with `JW_RESUME=1` and continues without waiting for a TIMEOUT. Use `--signal-mode scancel` on clusters that disallow `--signal`, or `none` for programs that cannot checkpoint (they are then resubmitted from scratch with a longer `--time` after TIMEOUT). A program is `--restartable` only if it resumes correctly from its own checkpoint when `JW_RESUME=1`.
Pad the first `--time` as the user's convention requires (2-3x the estimate); job-watch is the net for a wrong estimate, not a substitute for one.

## Launch templates
Local (this machine, scheduler none): put the watcher inside one `submit_job` on the local target so that one `compute_done` wakes the chat when the whole job (retries included) ends. Set `run_timeout_s` above the longest plausible run (host adds ~5 min of slack):
```
python3 $HOME/job-watch/jw.py start --state $PWD/jw_state --kind local --workdir $PWD --cmd "python train.py" --expect results.csv --stall-min 30 --retries 2
python3 $HOME/job-watch/jw.py watch --state $PWD/jw_state --interval 30 --deadline-min 1000000
```
SLURM from a login shell or a small controller job:
```
python3 $HOME/job-watch/jw.py start --state /scratch/<grp>/$USER/<proj>/jw_state --kind slurm --workdir /scratch/<grp>/$USER/<proj> \
  --cmd "$HOME/envs/x/bin/python run.py" --time 04:00:00 --max-time 24:00:00 --mem 16G --sbatch-args "--partition=<p> --account=<a>" --restartable --expect results.csv
python3 $HOME/job-watch/jw.py watch --state ... --interval 60 --deadline-min 25
```
Provider wait ceilings (e.g. DSAI harvest ceiling ~32 min): `watch --deadline-min 25`; on exit 10 run the same `watch` again from a new `submit_job`/`call_command` (state persists, one turn per ~25 min instead of per poll). On hosts without a ceiling use a large deadline so a single event covers the whole run.
Already-running job: `--attach-pid PID` (local, optionally `--exit-file`) or `--attach-job ID --script FILE` (SLURM; TIMEOUT then resubmits FILE).
Environment/data rule (user, 2026-10-04): software environments in `$HOME`, data/state on scratch; scratch is purged by age, so harvest results.

## Reporting
After `watch` returns, report only: `report_line`, exit code meaning, and the artifact/result location. Before saying results are valid, run the normal validity check (run-status-board for multi-unit runs; experiment-preflight before launching any new sweep; cluster-autoscout for partition choice). A `done` means the supplied `--expect/--validate` passed, nothing more.

## Not covered
Job arrays and dependency chains (supervise each unit with its own state, or use run-status-board for the array). Cross-host jobs. Programs that cannot resume: job-watch will rerun them from the beginning.

## Tests
`tests/job-watch_tests.py` (stdlib, ~1 min): real local processes (success, retry, stall kill, external SIGKILL, stop, attach, lock, deadline) and a fake SLURM driven by per-job plans (cycle mode, follow-file, `--time max`, TIMEOUT/OOM/NODE_FAIL/FAILED/CANCELLED/exit 85/USR1/validation/max attempts/own-ids-only/sbatch failure/attach). The generated sbatch wrapper's USR1 handling is run for real. Live-cluster validation is recorded in the repo README (JHPCE, 2026-10-08: USR1 checkpoint-continue and TIMEOUT resubmit both passed). On that cluster `--signal=B:USR1@90` arrived 25 s early; Slurm may signal up to 60 s early, so choose `--signal-margin` so the checkpoint still fits in that window.
