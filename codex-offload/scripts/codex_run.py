#!/usr/bin/env python3
"""codex_run.py TASK.md WORKDIR [--sandbox workspace-write] [--min-5h 2] [--min-week 1] [--timeout 3600] [--model M] [--effort low|medium|high|xhigh|max|ultra]
Gate on Codex plan usage, run `codex exec`, record provenance. Policy: use Codex until a window is exhausted, then hand back to Claude.
Exit codes: 0 ran ok | 10 gated -> route to CLAUDE (see route.json) | 11 exec failed | 12 exec hit usage limit mid-run -> route CLAUDE.
Outputs in WORKDIR/.codex-offload/<run_id>/: route.json, usage_before.json, usage_after.json, events.jsonl, last_message.md, diff.patch, run.json"""
import argparse, hashlib, json, os, subprocess, sys, time, datetime, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))

def read_usage():
    r = subprocess.run([sys.executable, os.path.join(HERE, "codex_usage.py")], capture_output=True, text=True, timeout=90)
    try: return json.loads(r.stdout)
    except ValueError: return {"error": "unparseable usage output", "raw": r.stdout[-300:]}

def decide(u, min5, minw):
    if "error" in u: return {"route": "claude", "reason": "usage read failed: " + str(u["error"])[:200], "retry_at": None}
    if u.get("ordinary_usage_allowed") is False:
        return {"route": "claude", "reason": "ordinary_usage_allowed=false", "retry_at": None}
    by = {w["label"]: w for w in u["windows"]}
    # weekly exhaustion blocks until the weekly reset; 5h exhaustion blocks until the 5h reset
    for lab, floor in (("weekly", minw), ("5h", min5)):
        w = by.get(lab)
        if w and w["remaining_percent"] is not None and w["remaining_percent"] < floor:
            return {"route": "claude", "reason": f"{lab} window has {w['remaining_percent']}% left (< {floor}%)", "retry_at": w["resets_at"]}
    return {"route": "codex", "reason": "headroom: " + ", ".join(f"{w['label']} {w['remaining_percent']}% left" for w in u["windows"]), "retry_at": None}

def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True); return r.stdout

def main():
    a = argparse.ArgumentParser()
    a.add_argument("task"); a.add_argument("workdir")
    a.add_argument("--sandbox", default="workspace-write", choices=["read-only", "workspace-write", "danger-full-access"])
    a.add_argument("--min-5h", type=float, default=2); a.add_argument("--min-week", type=float, default=1)
    a.add_argument("--timeout", type=int, default=3600); a.add_argument("--model", default=None)
    a.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh", "max", "ultra"])
    a = a.parse_args()
    prompt = open(a.task).read()
    wd = os.path.abspath(a.workdir); os.makedirs(wd, exist_ok=True)
    run_id = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out = os.path.join(wd, ".codex-offload", run_id); os.makedirs(out, exist_ok=True)
    wj = lambda n, o: open(os.path.join(out, n), "w").write(json.dumps(o, indent=1))
    u0 = read_usage(); wj("usage_before.json", u0)
    d = decide(u0, a.min_5h, a.min_week); wj("route.json", d)
    print(json.dumps({"run_dir": out, **d}))
    if d["route"] != "codex": return 10
    is_git = bool(sh(["git", "rev-parse", "--is-inside-work-tree"], cwd=wd).strip())
    head0 = sh(["git", "rev-parse", "HEAD"], cwd=wd).strip() if is_git else None
    cmd = ["codex", "exec", "--json", "-s", a.sandbox, "-C", wd, "-o", os.path.join(out, "last_message.md")]
    if not is_git: cmd.append("--skip-git-repo-check")
    if a.model: cmd += ["-m", a.model]
    if a.effort: cmd += ["-c", f'model_reasoning_effort="{a.effort}"']
    cmd.append("-")
    t0 = time.time()
    with open(os.path.join(out, "events.jsonl"), "w") as ev:
        try:
            p = subprocess.run(cmd, input=prompt, stdout=ev, stderr=subprocess.PIPE, text=True, timeout=a.timeout)
            rc, err = p.returncode, p.stderr[-2000:]
        except subprocess.TimeoutExpired:
            rc, err = 124, "timeout"
    t1 = time.time()
    u1 = read_usage(); wj("usage_after.json", u1)
    if is_git:
        open(os.path.join(out, "diff.patch"), "w").write(sh(["git", "diff", head0 or "HEAD", "--", ".", ":(exclude).codex-offload"], cwd=wd) + "\n# untracked files (contents not in this patch):\n" + sh(["git", "ls-files", "--others", "--exclude-standard", "--", ".", ":(exclude).codex-offload"], cwd=wd))
    hit = rc != 0 and any(s in (err + open(os.path.join(out, "events.jsonl")).read()[-4000:]).lower() for s in ("usage limit", "rate limit", "usage_limit"))
    delta = {}
    if "windows" in u0 and "windows" in u1:
        b0 = {w["label"]: w["used_percent"] for w in u0["windows"]}; b1 = {w["label"]: w["used_percent"] for w in u1["windows"]}
        delta = {k: (b1[k] - b0[k]) for k in b0 if k in b1 and b0[k] is not None and b1[k] is not None}
    tok = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    for line in open(os.path.join(out, "events.jsonl")):
        try: ev_ = json.loads(line)
        except ValueError: continue
        if ev_.get("type") == "turn.completed":
            for k in tok: tok[k] += (ev_.get("usage") or {}).get(k, 0) or 0
    ver = sh(["codex", "--version"]).strip()
    rec = {"run_id": run_id, "codex_version": ver, "cmd": cmd, "task_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
           "workdir": wd, "git_head_before": head0, "exit_code": rc, "seconds": round(t1 - t0, 1), "used_percent_delta": delta,
           "hit_usage_limit": hit, "model": a.model or "(host default)", "effort": a.effort or "(host default)", "tokens": tok, "stderr_tail": err}
    wj("run.json", rec)
    print(json.dumps({k: rec[k] for k in ("run_id", "exit_code", "seconds", "used_percent_delta", "hit_usage_limit", "model", "tokens")}))
    return 12 if hit else (0 if rc == 0 else 11)

if __name__ == "__main__":
    sys.exit(main())
