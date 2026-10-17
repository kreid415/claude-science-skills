#!/usr/bin/env python3
"""Read Codex ChatGPT-plan rate limits via `codex app-server` (JSON-RPC over stdio). `--line` prints one report line (5h, weekly, credits remaining).
Prints one JSON object: windows[{label,used_percent,remaining_percent,window_mins,resets_at,resets_in_s}],
ordinary_usage_allowed, plan_type, codex_version. Exit 0 ok, 2 on protocol failure."""
import json, subprocess, sys, time, select, os

def rpc(proc, mid, method, params=None, timeout=None):
    timeout = timeout or float(os.environ.get("CODEX_USAGE_TIMEOUT", "45"))
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

def read_once():
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
    return out

def read(attempts=3):
    """read_once() with retries: the app-server request sometimes times out transiently (seen once on the host)."""
    last = None
    for _ in range(attempts):
        try: return read_once()
        except (TimeoutError, RuntimeError) as e: last = e
    raise last

def _dur(sec):
    if sec is None: return "?"
    sec = max(int(sec), 0); d, r = divmod(sec, 86400); h, r = divmod(r, 3600); m = r // 60
    return f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")

def format_line(u):
    """One line for the user: remaining 5h, weekly and credits."""
    if "error" in u: return "Codex remaining: USAGE READ FAILED (" + str(u["error"])[:120] + ")"
    by = {w["label"]: w for w in u.get("windows", [])}
    parts = []
    for lab, name in (("5h", "5h"), ("weekly", "weekly")):
        w = by.get(lab)
        parts.append(f"{name} {w['remaining_percent']}% (resets in {_dur(w['resets_in_s'])})" if w else f"{name} n/a")
    c = u.get("credits") or {}
    if c.get("unlimited"): parts.append("credits unlimited")
    elif c.get("balance") is not None: parts.append(f"credits {c['balance']}")
    else: parts.append("credits none")
    return "Codex remaining: " + ", ".join(parts)

def main():
    try: out = read()
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"}
    if "--line" in sys.argv: print(format_line(out))
    else: print(json.dumps(out))
    return 2 if "error" in out else 0

if __name__ == "__main__":
    sys.exit(main())
