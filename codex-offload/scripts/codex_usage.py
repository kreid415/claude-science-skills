#!/usr/bin/env python3
"""Read Codex ChatGPT-plan rate limits via `codex app-server` (JSON-RPC over stdio).
Prints one JSON object: windows[{label,used_percent,remaining_percent,window_mins,resets_at,resets_in_s}],
ordinary_usage_allowed, plan_type, codex_version. Exit 0 ok, 2 on protocol failure."""
import json, subprocess, sys, time, select, os

def rpc(proc, mid, method, params=None, timeout=30):
    msg = {"id": mid, "method": method}
    if params is not None: msg["params"] = params
    proc.stdin.write(json.dumps(msg) + "\n"); proc.stdin.flush()
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([proc.stdout], [], [], 1.0)
        if not r: continue
        line = proc.stdout.readline()
        if not line: break
        try: obj = json.loads(line)
        except ValueError: continue
        if obj.get("id") == mid:
            if "error" in obj: raise RuntimeError(json.dumps(obj["error"]))
            return obj.get("result")
    raise TimeoutError(method)

def main():
    p = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
    try:
        init = rpc(p, 1, "initialize", {"clientInfo": {"name": "codex_offload_usage", "title": "codex-offload", "version": "0.1"}})
        p.stdin.write(json.dumps({"method": "initialized"}) + "\n"); p.stdin.flush()
        res = rpc(p, 2, "account/rateLimits/read")
    finally:
        p.terminate()
    snap = res.get("rateLimits") or {}
    by = res.get("rateLimitsByLimitId") or {}
    if "codex" in by: snap = by["codex"]
    now = time.time(); wins = []
    for key in ("primary", "secondary"):
        w = snap.get(key)
        if not w: continue
        mins = w.get("windowDurationMins")
        label = "5h" if mins and mins <= 360 else ("weekly" if mins and mins >= 6000 else f"{mins}m")
        ra = w.get("resetsAt")
        wins.append({"slot": key, "label": label, "used_percent": w.get("usedPercent"),
                     "remaining_percent": None if w.get("usedPercent") is None else 100 - w["usedPercent"],
                     "window_mins": mins, "resets_at": ra, "resets_in_s": None if ra is None else int(ra - now)})
    out = {"windows": wins, "ordinary_usage_allowed": res.get("ordinaryUsageAllowed"),
           "plan_type": snap.get("planType"), "rate_limit_reached_type": snap.get("rateLimitReachedType"),
           "credits": snap.get("credits"), "limit_ids": list(by.keys()), "server": init if isinstance(init, dict) else None,
           "read_at": int(now)}
    print(json.dumps(out)); return 0

if __name__ == "__main__":
    try: sys.exit(main())
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"})); sys.exit(2)
