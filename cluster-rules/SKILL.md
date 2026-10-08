---
name: cluster-rules
description: "Standing user rules for any cluster work: where conda environments vs data/caches/logs live ($HOME vs scratch), scratch purge handling, and never touching other agents' jobs on shared SLURM accounts (JHPCE, DSAI, Rockfish). Load before any cluster job, environment build, storage or path decision, or scheduler command (sbatch, scancel, squeue)."
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
