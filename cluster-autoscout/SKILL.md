---
name: cluster-autoscout
description: Scan every connected SSH/SLURM cluster for available nodes and pick the best partition+account for a job by immediate availability, then lowest wait. Use before dispatching remote GPU or CPU compute when you want the job to land on whichever cluster/partition is least contended right now, and to spread jobs across multiple clusters. Complements remote-compute-ssh (which covers the submit/harvest workflow once a target is chosen).
---

# cluster-autoscout

Pick the least-contended cluster+partition for a remote job, across ALL
connected SSH hosts, before submitting. This encodes the house rule:
**use the partition with the most idle nodes / least wait; if nothing is free,
target the lowest-wait partition for the required resource level; spread jobs
across clusters when needed.**

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

## Notes
- Node **state** is the availability signal: `idle` (fully free) and `mix`
  (partially free) can take a job now; `alloc`/`drain`/`maint`/`down` cannot.
- `pending_jobs` is a wait-time proxy per partition, used to break ties and to
  order the `queue` tier when nothing is free.
- Accounts and env activation are host facts — read them from `compute_details`,
  not from the scan.
- Scan is cheap (`sinfo`/`squeue`, seconds) — re-scan for each fresh fan-out
  since availability drifts.
