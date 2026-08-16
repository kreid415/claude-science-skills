---
name: cluster-autoscout
description: Scan every connected SSH/SLURM cluster for available nodes and pick the best partition+account for a job by immediate availability, then lowest wait. Use before dispatching remote GPU or CPU compute when you want the job to land on whichever cluster/partition is least contended right now, and to spread jobs across multiple clusters. Also covers placing job data, caches and conda environments on cluster scratch instead of home. Complements remote-compute-ssh (which covers the submit/harvest workflow once a target is chosen).
---

# cluster-autoscout

Pick the least-contended cluster+partition for a remote job, across ALL
connected SSH hosts, before submitting. This encodes the house rule:
**use the partition with the most idle nodes / least wait; if nothing is free,
target the lowest-wait partition for the required resource level; spread jobs
across clusters when needed. When you request the walltime, ask for more time
than you estimate — a Slurm `--time` kill loses the whole run (see `## Walltime`).
All actual computation runs on compute nodes via the scheduler, never on the
login node — the login node is only for the cheap scans below (see
`## Compute nodes, not login nodes`). Everything a job writes or caches —
inputs, outputs, tmp, pip/HF/torch caches, and conda environments themselves —
lives on cluster scratch, never in `$HOME` (see `## Scratch first`).**

This skill is the *targeting* layer. Once you have a target, `remote-compute-ssh`
covers the actual `submit_job` → `wait_for_notification` → `save_artifacts` flow.

## When to use

- Before any remote GPU/CPU dispatch where you'd otherwise hard-code a cluster.
- When one cluster is busy and another may be free.
- When fanning out many jobs — scan once, then distribute across the ranked
  candidates.

## kernel.py helpers (auto-loaded)

Loading this skill defines, in your python kernel:

- `SCAN_CMD` — one shell command that gathers per-partition node states + gres
  and pending-job counts. Run it on each cluster.
- `parse_scan(stdout)` → per-partition summary dicts
  `{partition, avail_nodes, total_nodes, states, gpus_per_node, has_gpu, pending_jobs}`.
- `rank_partitions(scans, need_gpus=0, min_nodes=1)` → ranked candidate list,
  best first, each `{provider, partition, avail_nodes, gpus_per_node, pending_jobs, tier}`.
  `tier` is `available` (idle/mix nodes now) or `queue` (meets the resource
  requirement but nothing free — ordered by fewest pending).
- `gpus_from_gres(gres)` → GPU count from a SLURM gres string.
- `scratch_env_preamble(scratch_root, conda_env=None)` → shell block that points
  `TMPDIR`, `CONDA_ENVS_PATH`, `CONDA_PKGS_DIRS`, `PIP_CACHE_DIR`, `HF_HOME`,
  `TORCH_HOME`, `XDG_CACHE_HOME`, `MPLCONFIGDIR` and the Apptainer cache dirs at
  scratch, and optionally activates a scratch-resident conda env. Paste it at the
  top of a `submit_job` command, after the `#SBATCH` lines (see `## Scratch first`).
- `plan_fanout(cands, n_jobs, accounts=None)` → deterministic dispatch **plan**
  for `n_jobs`: a list of `{job, provider, partition, account, tier, immediate}`,
  round-robined across the `available` tier (filling idle capacity first, then
  overflow to the queue), with `--account` filled in per provider. **Plans only —
  it does NOT submit.** Feed each entry to the `remote-compute-ssh` submit flow
  (which still shows a per-job approval card).

## Workflow

### 1. Enumerate connected clusters
Call the `list_compute` tool. Every `family: "ssh"` provider is a scan target.

### 2. Scan each cluster (repl tool — host.compute is repl-only)
`SCAN_CMD` is defined in your python kernel by this skill, but `host.compute`
lives in the repl tool. Simplest path: paste the command inline in the repl
cell (it's short), or write `SCAN_CMD` to `./handoff/scan_cmd.txt` from a
python cell and read it in repl.

```python
# repl tool
scan_cmd = ("echo '===SINFO==='; sinfo -h -o '%P|%t|%D|%G' 2>/dev/null; "
            "echo '===PENDING==='; squeue -h -t PD -o '%P' 2>/dev/null | sort | uniq -c")
import json
out = {}
# providers come from the list_compute TOOL (step 1) — hard-code the ssh
# provider names it returned:
for prov in ["ssh:clusterA", "ssh:clusterB"]:
    c = host.compute.create(prov)
    # call_command runs on the LOGIN node — fine here: this is a cheap seconds-long
    # scan, not a workload. Never run compute this way (see below).
    r = c.call_command(scan_cmd, intent=f"scan {prov} partition availability", login_shell=True)
    out[prov] = r.get("stdout","")
    c.close()
json.dump(out, open("handoff/scans.json","w"))
```

### 3. Parse + rank (python tool)
```python
import json
scans_raw = json.load(open("handoff/scans.json"))
scans = {prov: parse_scan(txt) for prov, txt in scans_raw.items()}
cands = rank_partitions(scans, need_gpus=1, min_nodes=1)   # need_gpus=0 for CPU jobs
best = cands[0]
print(best)   # {provider, partition, gpus_per_node, avail_nodes, tier, ...}
```

### 4. Submit to the winner
Use `best["provider"]` and `best["partition"]` with the `remote-compute-ssh`
submit flow. Pull the correct `--account` from that provider's
`compute_details` doc (accounts are per-host, not discoverable by scan).
Set `--time` to a padded multiple of your runtime estimate, not the estimate
itself — see `## Walltime`.

For a fan-out, don't hand-roll the assignment loop — call `plan_fanout` to
get a deterministic, account-filled dispatch plan, then submit each entry.
**The agent still decides whether to fan out and how many jobs; `plan_fanout`
only does the mechanical round-robin + account lookup; and every `submit_job`
still shows the user an approval card.**

```python
# python tool — build the plan (accounts come from each provider's compute_details)
accounts = {"ssh:clusterA": "acctA", "ssh:clusterB": "acctB"}
plan = plan_fanout(cands, n_jobs=8, accounts=accounts)
for p in plan:
    print(p)   # {job, provider, partition, account, tier, immediate}
```

`immediate=True` entries map onto idle nodes now; `immediate=False` will queue.
If any `account` is `None`, fill it from that provider's `compute_details`
before submitting. Then walk `plan` and issue one `remote-compute-ssh`
`submit_job` per entry (each gated by user approval). Re-scan and re-plan for
each fresh fan-out — availability drifts.

## Walltime — request more time than you estimate

On Slurm, `--time` is a hard wall: the moment a job reaches it the scheduler
kills it (`CANCELLED ... DUE TO TIME LIMIT`) and everything the run had done is
lost. The house rule is to size the request against the worst case, not the
expected one:

- **Pad the estimate.** Request roughly **2–3× a confident runtime estimate**,
  and more when it's the first run of this tool on this host, when the estimate
  is really a guess, or when runtime scales with input size. Early exit is free
  — a job that finishes before its walltime releases the node immediately, so
  padding costs at most a slightly worse queue position, while under-requesting
  costs the entire allocation. Always err on the side of more time.
- **This is a sizing judgment, not a scan output.** `rank_partitions` /
  `plan_fanout` pick *where* a job lands; they do not set `--time`. You choose
  the walltime per job (with the user's approval on the submit card) — the scan
  never fills it in.
- **Match `timeout_seconds` to the padded walltime.** In the `remote-compute-ssh`
  submit flow, keep `timeout_seconds` (how long the local poller waits) at or
  above the `--time` you requested, so the harvest waits out the padded
  allocation instead of giving up while the job is still running.
- **On a walltime kill, resubmit larger.** If a job dies with
  `DUE TO TIME LIMIT`, the estimate was too tight — resubmit with a bigger
  multiple, not the same value.

PBS (`-l walltime=`) and LSF (`-W`) enforce their time limits the same way;
pad them the same way.

## Compute nodes, not login nodes

The whole point of targeting a partition is to land the workload on a **compute
node** via the scheduler. The login node is shared infrastructure for every
user on the cluster — running compute there crowds out other people's
interactive sessions and gets accounts throttled or suspended by admins.

The line is simple:

- **Login node — cheap orchestration only.** `c.call_command(...)` runs on the
  login node. Use it only for what this skill's scans and quick probes need:
  `sinfo`/`squeue` (the `SCAN_CMD` above), `which <tool>`, `module avail`,
  `conda env list`, `ls` of a scratch path — seconds-long, near-zero CPU/memory.
- **Compute node — all real work.** Every actual computation goes through
  `c.submit_job(...)` with scheduler directives (`#SBATCH ...`), so Slurm places
  it on a compute node. Never wrap a heavy command in `call_command` to skip the
  queue; that runs it on the login node. If a tool needs a build/compile step
  heavy enough to strain the login node, submit that as its own short job too.
- **`scheduler: none` hosts.** A bare host (`scheduler: none` in
  `compute_details`) has no compute-node isolation — a `submit_job` there
  direct-execs on the one machine. Flag that to the user before running anything
  heavy rather than silently loading the login/head node.

## Scratch first

**Default rule: on an HPC cluster, everything bulky lives on scratch, not in
`$HOME`.** Home directories are small, quota'd, often backed up (so writing
churn there is expensive for the site), and on some clusters not even mounted
the same way on compute nodes. Scratch is the large, fast, parallel filesystem
the cluster provides for exactly this. Put on scratch:

- **Job working directories** — inputs staged in, all outputs, logs, checkpoints.
- **`TMPDIR`** — never let a job default to `/tmp` on the node or to `$HOME`.
- **Conda environments and their package cache** — envs are tens of GB; build
  them under scratch via `CONDA_ENVS_PATH` / `CONDA_PKGS_DIRS` (or
  `conda create -p /path/on/scratch/envs/<name>`), and use `pip --cache-dir`
  under scratch too. Same for Python venvs (`python -m venv /scratch/.../venv`).
- **Model weights and dataset caches** — `HF_HOME`, `TORCH_HOME`,
  `XDG_CACHE_HOME`, `MPLCONFIGDIR`.
- **Container images** — `APPTAINER_CACHEDIR` / `SINGULARITY_CACHEDIR`, and the
  `.sif` files themselves.

Keep in `$HOME` only small, hand-written, long-lived things: dotfiles, SSH
config, git checkouts of source code, small config/registry JSON.

### Find the right scratch path first

Scratch layout is per-host and frequently **group-scoped** — `/scratch/$USER`
is often not writable while `/scratch/<group>/$USER` is. Read the host's
`compute_details` doc first; if it doesn't record a scratch root, probe once on
the login node (cheap, allowed) and then **write the answer back into
`compute_details`** so the next session doesn't re-derive it:

```
for d in /scratch /scratch4 /scratch16 /weka/scratch "$HOME"/scr*_* "$HOME"/scratch*; do
  [ -e "$d" ] && printf '%s -> %s\n' "$d" "$(readlink -f "$d")"
done
df -h "$HOME" | tail -1                     # how tight is home?
ls -d /scratch/*/$USER 2>/dev/null            # group-scoped candidates
```

Confirm the candidate is writable and has room (`[ -w "$d" ]`, `df -h "$d"`)
before committing a build to it.

### Use it in a job

```python
# python tool — build the preamble (helper is defined by this skill)
pre = scratch_env_preamble("/scratch/gsteino1/$USER/myproj",
                           conda_env="/scratch/gsteino1/$USER/myproj/conda/envs/analysis")
command = "#!/bin/bash\n#SBATCH --partition=cpu\n#SBATCH --time=12:00:00\n" + pre + "python run.py"
```

Then submit `command` through the `remote-compute-ssh` flow. Build the env
**once** as its own short job (a `conda create` + `pip install torch` blows past
the 60 s `call_command` cap), record its path in `compute_details`, and reuse it
across every later job instead of rebuilding.

### Scratch is not permanent

Most sites purge scratch on an age policy (commonly 30–90 days untouched) and
do not back it up. So:

- **Harvest what matters.** Pull final results back as artifacts
  (`save_artifacts`) rather than leaving them as the only copy on scratch.
- **Treat a scratch conda env as rebuildable**, not precious — keep the
  `conda create` / `pip install` recipe in `compute_details` so it can be
  reconstructed after a purge.
- Long-lived shared data belongs on the site's project/data filesystem
  (e.g. a `~/data_<group>` mount) if one exists, not scratch.

## Notes
- Node **state** is the availability signal: `idle` (fully free) and `mix`
  (partially free) can take a job now; `alloc`/`drain`/`maint`/`down` cannot.
- `pending_jobs` is a wait-time proxy per partition, used to break ties and to
  order the `queue` tier when nothing is free.
- Accounts and env activation are host facts — read them from `compute_details`,
  not from the scan.
- Scan is cheap (`sinfo`/`squeue`, seconds) — re-scan for each fresh fan-out
  since availability drifts.
- Walltime is a per-job sizing choice, not part of targeting — pad it
  generously (see `## Walltime`); a Slurm `--time` kill loses the whole run.
- Compute always runs on a compute node via `submit_job`; the login node
  (`call_command`) is only for the cheap scans/probes here (see
  `## Compute nodes, not login nodes`).
- Data, caches and conda envs go on **scratch**, not `$HOME` — resolve the
  host's scratch root from `compute_details` (it is usually group-scoped) and
  emit the env block with `scratch_env_preamble` (see `## Scratch first`).
- Scratch is purged and unbacked: harvest results to artifacts, and keep the
  env build recipe in `compute_details` so a purged env can be rebuilt.
