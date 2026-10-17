#!/usr/bin/env python3
"""agent_ledger.py  append-only log of CLI-agent runs (both engines, both modes), to measure whether the workflow works.
  agent_ledger.py summary                      counts by engine/mode, pass rates by rung, tokens, credits, escaped-defect rate
  agent_ledger.py list [N]                     last N entries (default 10)
  agent_ledger.py mark ID --clean|--defect [--note TEXT]   record the outcome after the result was used
  agent_ledger.py migrate [OLD_LEDGER]         one-time copy of ~/codex-offload/ledger.jsonl entries (idempotent by id; the old file is not touched)
Entries carry `engine` (codex|claude) and `mode` (loop|task). Old codex-offload entries are normalised on read: engine=codex, mode=loop,
and their old mode value (implement|tests_only) moves to `loop_mode`.
Ledger file: $CLI_SUBAGENTS_LEDGER or <this directory>/ledger.jsonl. agent_loop.py and agent_task.py append automatically."""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
OLD = os.path.expanduser("~/codex-offload/ledger.jsonl")

def path(): return os.environ.get("CLI_SUBAGENTS_LEDGER") or os.path.join(HERE, "ledger.jsonl")

def norm(r):
    if r.get("mode") in ("implement", "tests_only"): r["loop_mode"], r["mode"] = r["mode"], "loop"
    r.setdefault("engine", "codex"); r.setdefault("mode", "loop")
    return r

def read(p=None):
    p = p or path()
    return [norm(json.loads(l)) for l in open(p) if l.strip()] if os.path.exists(p) else []

def append(entry):
    entry.setdefault("ts", int(time.time()))
    with open(path(), "a") as f: f.write(json.dumps(entry) + "\n")

def write(rows):
    tmp = path() + ".tmp"
    with open(tmp, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    os.replace(tmp, path())

def mark(id_, state, note=""):
    rows = read(); hit = False
    for r in rows:
        if r.get("id") == id_:
            r["outcome_check"] = {"state": state, "note": note, "ts": int(time.time())}; hit = True
    if hit: write(rows)
    return hit

def migrate(old=None):
    have = {r.get("id") for r in read()}; new = [r for r in read(old or OLD) if r.get("id") not in have]
    for r in new: append(r)
    return len(new)

def summary():
    rows = read(); n = len(rows)
    acc = [r for r in rows if r.get("status") == "accepted"]
    by = {}
    for r in rows:
        k = f"{r['engine']}:{r.get('accepted_rung') or 'none'}"; by[k] = by.get(k, 0) + 1
    marked = [r for r in acc + [r for r in rows if r.get("status") == "DECLARED"] if r.get("outcome_check")]
    defects = [r for r in marked if r["outcome_check"]["state"] == "defect"]
    tok = sum((r.get("tokens") or {}).get("input_tokens", 0) + (r.get("tokens") or {}).get("output_tokens", 0) for r in rows)
    cred = sum(r.get("credit_delta") or 0 for r in rows)
    return {"entries": n, "by_engine_mode": {f"{e}:{m}": sum(1 for r in rows if (r["engine"], r["mode"]) == (e, m)) for e, m in sorted({(r["engine"], r["mode"]) for r in rows})},
            "accepted": len(acc), "status_counts": {s: sum(1 for r in rows if r.get("status") == s) for s in sorted({str(r.get("status")) for r in rows})},
            "loop_accepted_by_engine_rung": by, "mean_attempts": round(sum(r.get("attempts", 0) for r in rows) / n, 2) if n else None,
            "tokens_in_plus_out": tok, "credit_delta_sum": round(cred, 6), "marked": len(marked), "escaped_defects": len(defects),
            "escaped_defect_rate": round(len(defects) / len(marked), 3) if marked else None}

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("summary"); l = sp.add_parser("list"); l.add_argument("n", nargs="?", type=int, default=10)
    mg = sp.add_parser("migrate"); mg.add_argument("old", nargs="?")
    m = sp.add_parser("mark"); m.add_argument("id"); g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--clean", action="store_true"); g.add_argument("--defect", action="store_true"); m.add_argument("--note", default="")
    a = ap.parse_args()
    if a.cmd == "summary": print(json.dumps(summary(), indent=1)); return 0
    if a.cmd == "migrate": print(json.dumps({"migrated": migrate(a.old)})); return 0
    if a.cmd == "list":
        for r in read()[-a.n:]: print(json.dumps(r))
        return 0
    ok = mark(a.id, "defect" if a.defect else "clean", a.note); print(json.dumps({"marked": ok})); return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
