#!/usr/bin/env python3
"""Check a cluster submission against the user-approved run budget (cluster-rules section 5). Stdlib only.

  budget_check.py sacct-cmd --budget B.json                      print the sacct command for this plan's jobs
  budget_check.py check --budget B.json --sacct S.txt --cpus N --mem-gb X --time-h T
                  [--gpus G] [--tasks K] [--concurrent C] [--est-h E] [--measured-h M] [--now YYYY-MM-DD]
  budget_check.py record --budget B.json (--job ID --batch NAME | --ack TEXT)

Jobs are logged in B.jobs.jsonl next to the budget, never in the approved budget file itself.
check prints one JSON line {decision: allow|warn|refuse, reasons, usage}; exit 0 = allow or warn (submit; tell the
user on warn), 1 = refuse (do not submit; ask the user), 2 = bad input.
"""
import argparse, datetime, json, os, re, statistics, sys

FIELDS = "JobID,State,ExitCode,ReqCPUS,AllocCPUS,ReqMem,MaxRSS,ElapsedRaw,TotalCPU,ReqTRES,AllocTRES"
FAILED = {"FAILED", "OUT_OF_MEMORY", "NODE_FAIL", "TIMEOUT", "BOOT_FAIL", "DEADLINE"}
LIVE = {"PENDING", "RUNNING", "REQUEUED", "RESIZING", "SUSPENDED", "CONFIGURING", "COMPLETING"}
STANDING = {"max_cpus_held": 200, "max_job_time_h": 72}  # cluster-rules section 4
MIN_FINISHED_FOR_FRACTION = 5


def die(msg):
    print(json.dumps({"decision": "error", "reasons": [msg]})); sys.exit(2)


def load_budget(path):
    try: b = json.load(open(path))
    except (OSError, ValueError) as e: die(f"cannot read budget {path}: {e}")
    lim = b.get("limits")
    need = ["max_cpus_held", "max_gpus_held", "cpu_hours", "gpu_hours", "max_job_mem_gb", "max_job_time_h", "max_jobs"]
    if not b.get("approved") or not isinstance(lim, dict) or any(not isinstance(lim.get(k), (int, float)) for k in need):
        die(f"budget needs 'approved' (date + user's words) and numeric limits {need}")
    return b


def ledger_path(budget): return os.path.splitext(budget)[0] + ".jobs.jsonl"


def read_ledger(budget):
    p = ledger_path(budget)
    if not os.path.exists(p): return []
    try: return [json.loads(l) for l in open(p) if l.strip()]
    except ValueError as e: die(f"corrupt ledger {p}: {e}")


def mem_mb(s, cpus=1):
    """'4G', '4000M', '4Gn', '2Gc' (per CPU), '123456K' -> MB; '' -> None."""
    m = re.fullmatch(r"([\d.]+)([KMGT]?)([nc]?)", s or "")
    if not m: return None
    v = float(m.group(1)) * {"K": 1 / 1024, "": 1 / 1024 / 1024, "M": 1, "G": 1024, "T": 1024 ** 2}[m.group(2)]
    return v * (cpus if m.group(3) == "c" else 1)


def secs(s):
    """sacct TotalCPU '[D-][HH:]MM:SS[.mmm]' -> seconds."""
    d, _, rest = s.rpartition("-") if "-" in s else ("0", "", s)
    parts = [float(x) for x in rest.split(":")]
    while len(parts) < 3: parts.insert(0, 0.0)
    return int(d or 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def gpus(tres):
    m = re.search(r"gres/gpu(?::[^=,]+)?=(\d+)", tres or "")
    return int(m.group(1)) if m else 0


def n_pending(jobid):
    """Pending array row '123_[1-100%10]' -> (tasks, tasks that can run at once); plain row -> (1, 1)."""
    m = re.search(r"\[([^\]]+)\]", jobid)
    if not m: return 1, 1
    spec, _, thr = m.group(1).partition("%")
    n = 0
    for part in spec.split(","):
        a, _, z = part.partition("-")
        n += int(z) - int(a) + 1 if z else 1
    return n, (min(n, int(thr)) if thr else n)


def parse_sacct(text, ledger):
    """-> list of tasks {parent, batch, state, exit, cpus, gpus, req_mb, rss_mb, elapsed, cpu_s, n}."""
    batch_of = {str(r["job"]): r["batch"] for r in ledger if "job" in r}
    tasks, steps = {}, {}
    for line in text.splitlines():
        if not line.strip(): continue
        f = line.split("|")
        if len(f) != 11: die(f"sacct line has {len(f)} fields, expected 11 ({FIELDS}): {line[:120]}")
        jid, state, ex, reqc, allc, reqm, rss, el, tcpu, reqt, allt = f
        if "." in jid:  # step line: keep the largest MaxRSS per task
            key = jid.split(".")[0]
            steps[key] = max(steps.get(key, 0), mem_mb(rss) or 0); continue
        parent = re.split(r"[_+]", jid)[0]
        if parent not in batch_of: die(f"sacct returned job {jid}, which is not in this plan's ledger")
        state = state.split()[0]
        cpus = int(allc or 0) or int(reqc or 0)
        tasks[jid] = dict(parent=parent, batch=batch_of[parent], state=state, exit=ex, cpus=cpus,
                          gpus=gpus(allt) or gpus(reqt), req_mb=mem_mb(reqm, cpus), rss_mb=None,
                          elapsed=int(el or 0), cpu_s=secs(tcpu) if tcpu else 0.0,
                          n=n_pending(jid) if state == "PENDING" else (1, 1))
    for k, t in tasks.items(): t["rss_mb"] = steps.get(k)
    return list(tasks.values())


def batch_order(ledger):
    """Batches after the last user acknowledgement, in ledger order; 'helper*' batches excluded."""
    last_ack = max([i for i, r in enumerate(ledger) if "ack" in r], default=-1)
    seen = []
    for r in ledger[last_ack + 1:]:
        if "job" in r and not r["batch"].startswith("helper") and r["batch"] not in seen: seen.append(r["batch"])
    return seen


def stops(budget, tasks, ledger):
    """Stop conditions over finished work since the last acknowledgement."""
    out, timeout_ok = [], budget.get("timeout_expected", False)
    failed = lambda t: t["state"] in FAILED and not (timeout_ok and t["state"] == "TIMEOUT")
    sig_by_batch, miss_by_batch = [], []
    for b in batch_order(ledger):
        bt = [t for t in tasks if t["batch"] == b and t["state"] not in LIVE and not t["state"].startswith("CANCELLED")]
        if not bt: continue
        bad = [t for t in bt if failed(t)]
        if len(bt) >= MIN_FINISHED_FOR_FRACTION and len(bad) / len(bt) > budget.get("max_fail_fraction", 0.2):
            out.append(f"batch '{b}': {len(bad)}/{len(bt)} finished tasks failed (limit {budget.get('max_fail_fraction', 0.2):.0%})")
        if bad: sig_by_batch.append((b, {(t["state"], t["exit"]) for t in bad}))
        ok = [t for t in bt if t["state"] == "COMPLETED" and t["elapsed"] > 0]
        if ok:
            miss = set()
            mem = [t["rss_mb"] / t["req_mb"] for t in ok if t["rss_mb"] is not None and t["req_mb"]]
            cpu = [t["cpu_s"] / (t["cpus"] * t["elapsed"]) for t in ok if t["cpus"]]
            if mem and statistics.median(mem) < 0.70: miss.add("memory used/requested < 70%")
            if cpu and statistics.median(cpu) < 0.85: miss.add("CPU busy/allocated < 85%")
            miss_by_batch.append((b, miss))
    if len(sig_by_batch) >= 2:
        same = sig_by_batch[-1][1] & sig_by_batch[-2][1]
        if same: out.append(f"same failure {sorted(same)} in batches '{sig_by_batch[-2][0]}' and '{sig_by_batch[-1][0]}'")
    if len(miss_by_batch) >= 2:
        same = miss_by_batch[-1][1] & miss_by_batch[-2][1]
        if same: out.append(f"efficiency miss {sorted(same)} in batches '{miss_by_batch[-2][0]}' and '{miss_by_batch[-1][0]}'")
    return out


def check(a):
    b = load_budget(a.budget); lim = b["limits"]; ledger = read_ledger(a.budget)
    try: text = open(a.sacct).read()
    except OSError as e: die(f"cannot read sacct output {a.sacct}: {e}")
    if any("job" in r for r in ledger) and not text.strip():
        die("sacct output is empty but the ledger lists jobs: rerun the sacct-cmd command")
    tasks = parse_sacct(text, ledger)
    live = [t for t in tasks if t["state"] in LIVE]
    used_cpu_h = sum(t["cpus"] * t["elapsed"] for t in tasks) / 3600
    used_gpu_h = sum(t["gpus"] * t["elapsed"] for t in tasks) / 3600
    held_cpus = sum(t["cpus"] * t["n"][1] for t in live); held_gpus = sum(t["gpus"] * t["n"][1] for t in live)
    n_jobs = sum(t["n"][0] for t in tasks)
    conc = min(a.concurrent or a.tasks, a.tasks)
    est_h = a.est_h if a.est_h is not None else a.time_h / 2  # --time is 2x the planned run time (section 4)
    new_cpu_h, new_gpu_h = a.cpus * a.tasks * est_h, a.gpus * a.tasks * est_h
    refuse, warn = [], []
    today = a.now or datetime.date.today().isoformat()
    if b.get("expires") and today > b["expires"]: refuse.append(f"budget expired on {b['expires']}")
    if a.gpus and lim["max_gpus_held"] == 0: refuse.append("GPU work is not in this budget: new GPU work needs the user's approval")
    if a.mem_gb > lim["max_job_mem_gb"]: refuse.append(f"memory {a.mem_gb} GB > per-job limit {lim['max_job_mem_gb']} GB")
    tmax = min(lim["max_job_time_h"], STANDING["max_job_time_h"])
    if a.time_h > tmax: refuse.append(f"time limit {a.time_h} h > per-job limit {tmax} h")
    if a.measured_h is not None and a.time_h > max(2 * a.measured_h, b.get("time_floor_h", 4)):
        refuse.append(f"time limit {a.time_h} h > 2 x measured {a.measured_h} h (section 4 rule)")
    cmax = min(lim["max_cpus_held"], STANDING["max_cpus_held"])
    if held_cpus + a.cpus * conc > cmax: refuse.append(f"CPUs held would be {held_cpus + a.cpus * conc} > {cmax}")
    if held_gpus + a.gpus * conc > lim["max_gpus_held"]: refuse.append(f"GPUs held would be {held_gpus + a.gpus * conc} > {lim['max_gpus_held']}")
    if n_jobs + a.tasks > lim["max_jobs"]: refuse.append(f"jobs would be {n_jobs + a.tasks} > {lim['max_jobs']}")
    for name, used, new, cap in (("CPU-hours", used_cpu_h, new_cpu_h, lim["cpu_hours"]), ("GPU-hours", used_gpu_h, new_gpu_h, lim["gpu_hours"])):
        if not new and not used: continue
        if used + new > cap: refuse.append(f"{name}: used {used:.1f} + this request {new:.1f} > budget {cap}")
        elif used + new >= b.get("warn_fraction", 0.8) * cap: warn.append(f"{name}: used {used:.1f} + this request {new:.1f} = {(used + new) / cap:.0%} of budget {cap}")
    refuse += stops(b, tasks, ledger)
    decision = "refuse" if refuse else "warn" if warn else "allow"
    print(json.dumps({"decision": decision, "plan": b.get("plan"), "reasons": refuse + warn,
                      "usage": {"cpu_hours": round(used_cpu_h, 2), "gpu_hours": round(used_gpu_h, 2), "cpus_held": held_cpus,
                                "gpus_held": held_gpus, "jobs": n_jobs}}))
    return 1 if refuse else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    for c in ("sacct-cmd", "check", "record"): s.add_parser(c).add_argument("--budget", required=True)
    c = s.choices["check"]
    c.add_argument("--sacct", required=True); c.add_argument("--cpus", type=int, required=True)
    c.add_argument("--mem-gb", type=float, required=True); c.add_argument("--time-h", type=float, required=True)
    c.add_argument("--gpus", type=int, default=0); c.add_argument("--tasks", type=int, default=1)
    c.add_argument("--concurrent", type=int); c.add_argument("--est-h", type=float)
    c.add_argument("--measured-h", type=float); c.add_argument("--now")
    r = s.choices["record"]
    r.add_argument("--job"); r.add_argument("--batch"); r.add_argument("--ack")
    a = p.parse_args(argv)
    if a.cmd == "check": return check(a)
    load_budget(a.budget)
    if a.cmd == "sacct-cmd":
        ids = sorted({str(x["job"]) for x in read_ledger(a.budget) if "job" in x})
        if not ids: print("# no jobs recorded yet: pass an empty file as --sacct"); return 0
        print(f"sacct -n -P -j {','.join(ids)} --format={FIELDS}"); return 0
    if bool(a.ack) == bool(a.job and a.batch): die("record needs --job and --batch, or --ack")
    if a.job and not re.fullmatch(r"\d+", a.job): die(f"job id must be the numeric Slurm id, got {a.job!r}")
    row = {"ack": a.ack} if a.ack else {"job": a.job, "batch": a.batch}
    row["at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(ledger_path(a.budget), "a") as f: f.write(json.dumps(row) + "\n")
    print(json.dumps(row)); return 0


if __name__ == "__main__":  # pragma: no cover  # script entry; main() is tested directly
    sys.exit(main())
