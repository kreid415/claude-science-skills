"""Helpers for scanning SLURM clusters for available nodes and ranking
partitions by immediate availability / lowest wait. Pure parsing + ranking —
the actual remote scan (host.compute.*) is driven from the repl tool per
SKILL.md; feed its stdout to parse_scan()."""

# One command gathers per-partition node states + gres, plus pending-job counts
# (a wait-time proxy). Run it on each cluster via
#   host.compute.create(provider).call_command(SCAN_CMD, login_shell=True)
SCAN_CMD = (
    "echo '===SINFO==='; "
    "sinfo -h -o '%P|%t|%D|%G' 2>/dev/null; "
    "echo '===PENDING==='; "
    "squeue -h -t PD -o '%P' 2>/dev/null | sort | uniq -c"
)

# node states that can accept a fresh job right now (partial or full idle)
AVAILABLE_STATES = ("idle", "mix")


def gpus_from_gres(gres):
    """Extract total GPU count from a SLURM gres string like 'gpu:a100:4' or
    'gpu:4(S:0-1)'. Returns 0 when no gpu gres is present."""
    if not gres or gres in ("(null)", "N/A"):
        return 0
    total = 0
    for tok in gres.split(","):
        tok = tok.strip()
        if not tok.startswith("gpu"):
            continue
        # strip any '(...)' suffix, then take the trailing integer field
        head = tok.split("(")[0]
        parts = head.split(":")
        num = parts[-1]
        try:
            total += int(num)
        except ValueError:
            total += 1  # gpu present but count unparseable -> assume >=1
    return total


def parse_scan(stdout):
    """Parse SCAN_CMD output into a per-partition summary.

    Returns list of dicts, one per partition:
      {partition, avail_nodes, total_nodes, states:{state:nodes},
       gpus_per_node, has_gpu, pending_jobs}
    'partition' has any trailing '*' (default marker) stripped.
    """
    sin, pend = [], []
    section = None
    for line in stdout.splitlines():
        s = line.strip()
        if s == "===SINFO===":
            section = "sin"; continue
        if s == "===PENDING===":
            section = "pend"; continue
        if not s:
            continue
        (sin if section == "sin" else pend).append(s)

    parts = {}
    for row in sin:
        f = row.split("|")
        if len(f) < 3:
            continue
        p = f[0].rstrip("*")
        state = f[1].rstrip("*$~#").lower()  # drop trailing state flags
        try:
            n = int(f[2])
        except ValueError:
            continue
        gres = f[3] if len(f) > 3 else ""
        d = parts.setdefault(p, {"partition": p, "avail_nodes": 0,
                                 "total_nodes": 0, "states": {},
                                 "gpus_per_node": 0, "pending_jobs": 0})
        d["total_nodes"] += n
        d["states"][state] = d["states"].get(state, 0) + n
        if any(state.startswith(a) for a in AVAILABLE_STATES):
            d["avail_nodes"] += n
        g = gpus_from_gres(gres)
        if g > d["gpus_per_node"]:
            d["gpus_per_node"] = g

    for row in pend:
        toks = row.split()
        if len(toks) != 2:
            continue
        cnt, p = toks
        p = p.rstrip("*")
        try:
            c = int(cnt)
        except ValueError:
            continue
        if p in parts:
            parts[p]["pending_jobs"] += c

    out = []
    for d in parts.values():
        d["has_gpu"] = d["gpus_per_node"] > 0
        out.append(d)
    return out


def rank_partitions(scans, need_gpus=0, min_nodes=1):
    """Rank (provider, partition) candidates across clusters by immediate
    availability, then lowest wait.

    scans: dict {provider_name: parse_scan(stdout) list}
    need_gpus: minimum GPUs-per-node required (0 = CPU job, any partition ok)
    min_nodes: minimum available nodes wanted for the 'available' tier

    Returns list of candidate dicts sorted best-first:
      {provider, partition, avail_nodes, gpus_per_node, pending_jobs, tier}
    tier 'available' = has >= min_nodes idle/mix nodes now; 'queue' = meets the
    resource requirement but nothing free now (ordered by fewest pending).
    """
    cands = []
    for prov, plist in scans.items():
        for d in plist:
            if need_gpus and d["gpus_per_node"] < need_gpus:
                continue
            cands.append({"provider": prov, "partition": d["partition"],
                          "avail_nodes": d["avail_nodes"],
                          "gpus_per_node": d["gpus_per_node"],
                          "pending_jobs": d["pending_jobs"],
                          "total_nodes": d["total_nodes"]})
    for c in cands:
        c["tier"] = "available" if c["avail_nodes"] >= min_nodes else "queue"
    # available first (most free nodes, then fewest pending); then queue
    # (fewest pending, then most total capacity)
    cands.sort(key=lambda c: (
        0 if c["tier"] == "available" else 1,
        -c["avail_nodes"] if c["tier"] == "available" else c["pending_jobs"],
        c["pending_jobs"] if c["tier"] == "available" else -c["total_nodes"],
    ))
    return cands

def plan_fanout(cands, n_jobs, accounts=None):
    """Turn a ranked candidate list into a deterministic dispatch plan for
    n_jobs, round-robined across the least-contended targets. PLANS ONLY —
    it never submits; feed each entry to the remote-compute-ssh submit flow
    (which still shows the user a per-job approval card).

    cands:    output of rank_partitions() (best-first, each carrying 'tier').
    n_jobs:   how many jobs to place (>= 1).
    accounts: {provider_name: --account string}. A provider missing here gets
              account=None (caller must fill it before submit).

    Strategy (encodes the house rule):
      * Prefer the 'available' tier. Phase A fills idle node capacity
        round-robin across those partitions (avail_nodes slots), so jobs land
        and run now and load is spread rather than piled on one cluster.
      * Phase B places any overflow round-robin across the same available
        pool; those jobs will queue (immediate=False).
      * If NOTHING is in the available tier, fall back to the 'queue' tier
        (lowest-wait first, as ranked) and round-robin there.

    Returns a list of dispatch dicts, one per job, in submission order:
      {job, provider, partition, account, tier, immediate}
    where immediate=True means the job maps onto idle capacity right now, and
    tier reflects that ('available' when immediate else 'queue') even for a
    partition that is itself in the available tier but whose idle slots are
    already spoken for by earlier jobs in this plan.

    >>> cands = [
    ...   {"provider":"ssh:A","partition":"gpu","avail_nodes":2,
    ...    "gpus_per_node":4,"pending_jobs":0,"tier":"available","total_nodes":10},
    ...   {"provider":"ssh:B","partition":"gpu","avail_nodes":1,
    ...    "gpus_per_node":4,"pending_jobs":3,"tier":"available","total_nodes":8}]
    >>> accts = {"ssh:A":"acctA","ssh:B":"acctB"}
    >>> [(p["provider"], p["immediate"]) for p in plan_fanout(cands, 4, accts)]
    [('ssh:A', True), ('ssh:B', True), ('ssh:A', True), ('ssh:A', False)]
    >>> [(p["provider"], p["account"]) for p in plan_fanout(cands, 2, {"ssh:A":"acctA"})]
    [('ssh:A', 'acctA'), ('ssh:B', None)]
    >>> q = [{"provider":"ssh:A","partition":"gpu","avail_nodes":0,
    ...       "gpus_per_node":4,"pending_jobs":1,"tier":"queue","total_nodes":10}]
    >>> [p["immediate"] for p in plan_fanout(q, 2)]
    [False, False]
    """
    if n_jobs < 1:
        raise ValueError("n_jobs must be >= 1")
    accounts = accounts or {}
    avail = [c for c in cands if c.get("tier") == "available"]
    tier = "available" if avail else "queue"
    pool = avail if avail else [c for c in cands if c.get("tier") == "queue"]
    if not pool:
        raise ValueError("no candidate partitions to dispatch to")

    plan = []

    def _assign(c, immediate):
        plan.append({"job": len(plan), "provider": c["provider"],
                     "partition": c["partition"],
                     "account": accounts.get(c["provider"]),
                     "tier": "available" if immediate else "queue",
                     "immediate": immediate})

    # Phase A: fill idle capacity round-robin (available tier only)
    cap = [c["avail_nodes"] for c in pool] if tier == "available" else []
    if tier == "available":
        progressed = True
        while len(plan) < n_jobs and progressed:
            progressed = False
            for i, c in enumerate(pool):
                if len(plan) >= n_jobs:
                    break
                if cap[i] > 0:
                    cap[i] -= 1
                    _assign(c, True)
                    progressed = True

    # Phase B: overflow (available tier) or the whole plan (queue tier)
    i = 0
    while len(plan) < n_jobs:
        _assign(pool[i % len(pool)], False)
        i += 1
    return plan

