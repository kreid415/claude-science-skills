---
name: cluster-rules
description: "Standing user rules for any cluster work: where conda environments vs data/caches/logs live ($HOME vs scratch), scratch purge handling, never touching other agents' jobs on shared SLURM accounts (JHPCE, DSAI, Rockfish), JHPCE efficiency sizing, and job approval per run plan with a budget checked by scripts/budget_check.py. Load before any cluster job, environment build, storage or path decision, or scheduler command (sbatch, scancel, squeue)."
---

# Cluster rules (user standing rules, all projects)

Load before cluster jobs, env builds, path/storage choices, or scheduler commands.
The one-line hard rules also sit in profile memory; this file holds the detail.

## 1. Storage (user rule, updated 2026-10-04; replaces the scratch-first version)

- ENVIRONMENTS live in $HOME: conda via `conda create -p` under $HOME or `CONDA_ENVS_PATH` under $HOME; Python venvs.
- DATA lives on cluster SCRATCH: job working dirs, all inputs/outputs/logs/checkpoints, TMPDIR, model/dataset caches (HF_HOME, TORCH_HOME, XDG_CACHE_HOME, MPLCONFIGDIR), plus package download caches (CONDA_PKGS_DIRS, pip cache) and container image caches (APPTAINER_CACHEDIR / SINGULARITY_CACHEDIR).
- $HOME also keeps small long-lived items: dotfiles, ssh config, git source checkouts, small config JSON.

## 2. Corollary (observed)

- Scratch is purged by file age and not backed up: harvest final results as artifacts; scratch is never the durable copy.
- Conda keeps packages' old file dates, so an env on scratch loses files within days (JHPCE fastscratch: envs built 2026-10-02 were gutted by 2026-10-04). Hence envs live in $HOME.
- $HOME is small and shared by several agent sessions (JHPCE 100 GB cap, ~91 GB used on 2026-10-04; DSAI 52 GB). Measure it before an env build; if the env will not fit with headroom, ask the user what to free; record each env's recipe in the host's compute_details.
- Scratch roots are usually /scratch/<group>/$USER: take the path from compute_details or one login-node probe.

## 3. Shared cluster accounts (observed)

- The user runs multiple independent agents on the same cluster accounts (JHPCE 'jhpce'/'kreid', DSAI, Rockfish). Their jobs are indistinguishable by name (operon-<uuid>) and cross-session messaging is impossible.
- NEVER issue account-wide scheduler commands (`scancel -u <user>`) or cancel jobs that merely look like duplicates. Cancel only job IDs this session itself submitted; verify against `host.compute.ledger()`.
- Precedent: on 2026-08-26 `scancel -u kreid` cancelled 13 of another agent's pending JHPCE jobs (no compute lost; the other agent resubmitted).

## 4. JHPCE efficiency rules (user standing rule, 2026-10-26, all projects)

Why: JHPCE billing weights are `CPU=1.0,Mem=1.0M` (1 unit per MB of requested memory, 1 per CPU), so requested memory sets fair-share priority; there is no usage budget, only priority (fair-share weight 100, age weight 1, decay half-life 7 days). JHPCE emailed the user on 2026-10-26 about 20% memory efficiency.

1. Size every request from measurement: memory = measured peak + a stated margin; CPUs = threads actually used; `--time` = 2 x planned run time (min 4 h, max 72 h). First runs use a short pilot to measure.
2. Slurm CPU counting: on nodes where Slurm counts a core as one CPU (SelectTypeParameters CR_CORE_MEMORY; check `scontrol show node` ThreadsPerCore and the OS thread_siblings lists), asking N CPUs gives N whole cores (2N threads). Request half the CPUs there, or use all threads, so no cores sit idle.
3. CPU work: throttled job arrays of small one-method bundles (each task requests exactly its method's CPUs, memory and time), not long multi-method worklist chains. Hold at most 200 shared CPUs; account limits MaxSubmitPU 10000, max_array_tasks 10000.
4. GPU work may chain (5-card cap, several fits per card) without idle capacity: CPUs and memory only for the fits on the card; a free slot takes the next fit of any method that fits; output validation off the slot's critical path; card drain/re-check only after card-like (CUDA) failures; chain ends when its work runs out.
5. Helper jobs (controllers, watchers, pollers) request at most 1 CPU and 1 GB.
6. Never resubmit an error that will repeat (e.g. a start-up check failure).
7. Before any large submission, run a canary job: one fit of every method on every dataset family.
8. After each batch, check efficiency from sacct/sstat/nvidia-smi: memory used/requested >= 70% (<1% out-of-memory), CPU busy/allocated >= 85%, GPU busy >= 70% while a GPU job runs, time limit <= 2 x elapsed. Fix the cause of a miss before the next batch.
9. Login node (jhpce01): JHPCE says never run jobs there; scans of many files, copies of large files, compiles and analyses go through `srun` or `sbatch`. Light read-only commands (squeue, sacct, a few small files) are fine.
10. Fastscratch is for files under 30 days; JHPCE removes older files 'without exception'. Do not plan long-lived inputs there without the user's decision.

## 5. Approval per run plan with a budget, not per job (user rule, 2026-10-31; replaces "ask before every submit", 2026-10-08)

Why: per-job approval requests slowed work that needs many jobs in a row. Control now comes from a user-approved budget that a script checks before every submission, plus automatic stops that send the decision back to the user.

1. **One approval per run plan.** Before a wave, the user approves a plan: the goal, what is run (methods x datasets), and a budget file committed next to it (e.g. `experiments/<run>/BUDGET.json`):
   ```json
   {"plan": "scoring-release", "approved": "2026-10-31 user: 'agree'", "expires": "2026-11-14",
    "limits": {"max_cpus_held": 50, "max_gpus_held": 0, "cpu_hours": 5000, "gpu_hours": 0,
               "max_job_mem_gb": 16, "max_job_time_h": 12, "max_jobs": 2000},
    "warn_fraction": 0.8, "max_fail_fraction": 0.2, "timeout_expected": false, "time_floor_h": 4}
   ```
   `approved` quotes the user's own words; a coordinator or sub-agent cannot approve a budget. Size `cpu_hours`/`gpu_hours` from the quick test run (measured hours x planned tasks x about 1.5). Never raise limits after approval: a larger budget is a new approval. `timeout_expected: true` for jobs that run to the time limit and continue from a checkpoint.
2. **Before every submission under a plan**, run `scripts/budget_check.py` (stdlib; in this skill's directory, e.g. `SKILL_DIR/scripts/budget_check.py`):
   ```
   python budget_check.py sacct-cmd --budget BUDGET.json            # prints the sacct command for this plan's jobs
   # run that command on the login node (light, read-only) and save its output to sacct.txt (empty file if no jobs yet)
   python budget_check.py check --budget BUDGET.json --sacct sacct.txt --cpus 4 --mem-gb 8 --time-h 6 \
          [--tasks 200 --concurrent 10] [--gpus 1] [--est-h 3] [--measured-h 2.5]
   python budget_check.py record --budget BUDGET.json --job 36764143 --batch wave3   # after each submit
   python budget_check.py record --budget BUDGET.json --ack "user 2026-11-02: continue with memory 12 GB"
   ```
   `check` prints one JSON line. `allow`: submit, then `record`. `warn` (80% of the hour budget reached): submit, record, and tell the user. `refuse` (exit 1): do not submit; show the user the reasons and ask. After the user says continue, `record --ack` with their words: stop conditions then look only at later batches. Exit 2 means bad input (fix it, never bypass). Jobs go in `BUDGET.jobs.jsonl`; the budget file itself is never edited by the script.
3. **Submitted without asking, in any chat** (still within section 4 and the 200-CPU cap):
   - helper jobs (≤1 CPU, ≤1 GB); record them under a batch name starting `helper` so efficiency checks skip them;
   - small check or test jobs (≤2 CPUs, ≤4 GB, ≤4 h);
   - the quick test run before a large submission (CPU only; GPU test runs are new GPU work);
   - within an approved budget: resubmissions after an expected time-limit stop, reruns of failed tasks after a fix that does not change what is measured (memory raised within the per-job limit, nodes excluded, including `scontrol update ExcNodeList` on this plan's own pending job ids).
4. **Automatic stops** (`check` refuses): hour budget would pass 100%; more than 20% of a finished batch failed (at least 5 finished tasks; cancelled tasks ignored); the same failure (state, exit code) in the two most recent batches with failures; the same efficiency miss (median memory used/requested < 70%, or CPU busy/allocated < 85%) in two consecutive batches; a request over the plan's per-job, held-CPU/GPU or job-count limits, or over the standing limits (200 CPUs, 72 h, time limit over 2 x measured run time); budget expired. GPU busy (≥ 70%) is not in sacct: check it by hand per section 4 item 8 and stop after two misses.
5. **Always ask the user**: a new plan or a larger budget; new GPU work (budgets carry `max_gpus_held: 0` unless the user approved GPUs); any change to what is run, measured or analysed (new method, dataset, metric or parameter); any action on job ids not in this session's `host.compute.ledger()`; any submission over section 3's small-job sizes with no approved budget covering it.
6. **Report after each batch** in five lines: jobs run, hours used against the budget (from `check`'s `usage`), efficiency, failures, fixes. Keep the run-status board current.
7. **Controller jobs** that run `sbatch` themselves (job-watch): check the controller's whole planned work (`--tasks` x per-task request) before submitting it, and `record` the child job ids as they appear.
8. The app shows its own approval request for each `submit_job` unless the user has set "Always allow" for that cluster (agent code cannot set it). To keep those requests few, prefer job arrays and controller jobs over many single submissions.
