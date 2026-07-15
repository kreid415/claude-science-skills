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
