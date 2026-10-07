#!/usr/bin/env python3
"""codex_ledger.py  append-only log of offloaded tasks, to measure whether the workflow works.
  codex_ledger.py summary                      counts, pass rates by rung, tokens, credits, escaped-defect rate
  codex_ledger.py list [N]                     last N entries (default 10)
  codex_ledger.py mark ID --clean|--defect [--note TEXT]   record the outcome after the patch was applied and used
Ledger file: $CODEX_LEDGER or <this directory>/ledger.jsonl. codex_loop.py appends one entry per loop automatically."""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))

def path(): return os.environ.get("CODEX_LEDGER") or os.path.join(HERE, "ledger.jsonl")

def read():
    if not os.path.exists(path()): return []
    return [json.loads(l) for l in open(path()) if l.strip()]

def append(entry):
    entry.setdefault("ts", int(time.time()))
    with open(path(), "a") as f: f.write(json.dumps(entry) + "\n")

def mark(id_, state, note=""):
    rows = read(); hit = False
    for r in rows:
        if r.get("id") == id_:
            r["outcome_check"] = {"state": state, "note": note, "ts": int(time.time())}; hit = True
    if not hit: return False
    tmp = path() + ".tmp"
    with open(tmp, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    os.replace(tmp, path()); return True

def summary():
    rows = read(); n = len(rows)
    acc = [r for r in rows if r.get("status") == "accepted"]
    by = {}
    for r in rows:
        k = r.get("accepted_rung") or "none"; by[k] = by.get(k, 0) + 1
    marked = [r for r in acc if r.get("outcome_check")]
    defects = [r for r in marked if r["outcome_check"]["state"] == "defect"]
    tok = sum((r.get("tokens") or {}).get("input_tokens", 0) + (r.get("tokens") or {}).get("output_tokens", 0) for r in rows)
    cred = sum(r.get("credit_delta") or 0 for r in rows)
    return {"entries": n, "accepted": len(acc), "status_counts": {s: sum(1 for r in rows if r.get("status") == s) for s in sorted({r.get("status") for r in rows})},
            "accepted_by_rung": by, "mean_attempts": round(sum(r.get("attempts", 0) for r in rows) / n, 2) if n else None,
            "tokens_in_plus_out": tok, "credit_delta_sum": round(cred, 6),
            "accepted_marked": len(marked), "escaped_defects": len(defects),
            "escaped_defect_rate": round(len(defects) / len(marked), 3) if marked else None,
            "accepted_unmarked": len(acc) - len(marked)}

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("summary"); l = sp.add_parser("list"); l.add_argument("n", nargs="?", type=int, default=10)
    m = sp.add_parser("mark"); m.add_argument("id"); g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--clean", action="store_true"); g.add_argument("--defect", action="store_true"); m.add_argument("--note", default="")
    a = ap.parse_args()
    if a.cmd == "summary": print(json.dumps(summary(), indent=1)); return 0
    if a.cmd == "list":
        for r in read()[-a.n:]: print(json.dumps(r))
        return 0
    ok = mark(a.id, "defect" if a.defect else "clean", a.note); print(json.dumps({"marked": ok})); return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
