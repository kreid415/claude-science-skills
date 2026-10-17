#!/usr/bin/env python3
"""agent_run.py TASK WORKDIR --engine {codex,claude} [--sandbox read-only|workspace-write] [--timeout 3600] [--model M] [--effort E]
One gated attempt of a CLI agent (`codex exec` or `claude -p`) with provenance. Used by agent_loop.py (one call per rung) and agent_task.py.
codex: gated on live ChatGPT-plan usage (codex_usage.py); claude: no usage gate (the CLI exposes no remaining-usage reading we are allowed to use).
Exit codes: 0 ran ok | 10 gated -> do the task elsewhere (see route.json) | 11 exec failed | 12 hit a usage limit mid-run | 2 bad input.
Outputs in WORKDIR/.cli-subagents/<run_id>/: route.json, usage_before.json, usage_after.json, events.jsonl, last_message.md, diff.patch, run.json.
Never passes danger-full-access, --dangerously-skip-permissions or bypass modes: access is read-only or workspace-write only."""
import argparse, datetime, hashlib, json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)  # PYTHONSAFEPATH=1 hides the script dir
EFFORTS = {"codex": ["low", "medium", "high", "xhigh", "max", "ultra"], "claude": ["low", "medium", "high", "xhigh", "max"]}
PERMISSION_MODES = ["dontAsk", "acceptEdits", "plan"]  # never bypassPermissions / auto / manual
CLAUDE_TOOLS = {"read-only": "Read,Glob,Grep",
                "workspace-write": "Read,Glob,Grep,Edit,Write,Bash(python3 *),Bash(python *),Bash(pytest *),Bash(git *),Bash(ls *),Bash(cat *),Bash(mkdir *),Bash(diff *)"}
TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
LIMIT_WORDS = ("usage limit", "rate limit", "usage_limit", "limit reached", "quota")
GIT_EXCLUDE = [":(exclude).cli-subagents", ":(exclude).codex-offload"]

def bin_path(engine):
    """CLAUDE_BIN / CODEX_BIN override (tests), then PATH, then ~/.local/bin (non-login shells lack it)."""
    return os.environ.get(engine.upper() + "_BIN") or shutil.which(engine) or os.path.expanduser("~/.local/bin/" + engine)

def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True); return r.stdout

def read_usage(engine):
    if engine == "claude": return {"gate": "none"}
    r = subprocess.run([sys.executable, os.path.join(HERE, "codex_usage.py")], capture_output=True, text=True, timeout=90)
    try: return json.loads(r.stdout)
    except ValueError: return {"error": "unparseable usage output", "raw": r.stdout[-300:]}

def credits_ok(u):
    """Credits are used freely (user decision): usable if Codex reports unlimited credits or a credit balance. No balance threshold."""
    c = u.get("credits") or {}
    try: bal = float(c.get("balance"))
    except (TypeError, ValueError): bal = None
    return bool(c.get("unlimited") or c.get("hasCredits")), bal

def decide(engine, u, min5, minw):
    """route 'run' or 'fallback'. codex: use included windows first, then credits, fall back only when neither is available.
    claude: no gate; a usage-limit error mid-run exits 12."""
    if engine == "claude": return {"route": "run", "reason": "gate none: claude CLI reports no usage we may read", "retry_at": None, "on_credits": False}
    if "error" in u: return {"route": "fallback", "reason": "usage read failed: " + str(u["error"])[:200], "retry_at": None, "on_credits": False}
    has_credit, bal = credits_ok(u)
    by = {w["label"]: w for w in u["windows"]}
    low = [(lab, by[lab]) for lab, floor in (("weekly", minw), ("5h", min5)) if by.get(lab) and by[lab]["remaining_percent"] is not None and by[lab]["remaining_percent"] < floor]
    blocked = u.get("ordinary_usage_allowed") is False
    if low or blocked:
        if has_credit:
            why = "included usage exhausted" if blocked else "window below floor (" + ", ".join(f"{l} {w['remaining_percent']}%" for l, w in low) + ")"
            return {"route": "run", "reason": f"{why}; running on credits (balance {bal})", "retry_at": None, "on_credits": True, "credit_balance": bal}
        if blocked: return {"route": "fallback", "reason": "ordinary_usage_allowed=false and the account has no credits", "retry_at": None, "on_credits": False}
        lab, w = low[0]
        return {"route": "fallback", "reason": f"{lab} window has {w['remaining_percent']}% left and the account has no credits", "retry_at": w["resets_at"], "on_credits": False}
    return {"route": "run", "reason": "headroom: " + ", ".join(f"{w['label']} {w['remaining_percent']}% left" for w in u["windows"]), "retry_at": None, "on_credits": False}

def credit_delta(u0, u1):
    def bal(u):
        try: return float((u.get("credits") or {}).get("balance"))
        except (TypeError, ValueError): return None
    b0, b1 = bal(u0), bal(u1)
    return {"balance_before": b0, "balance_after": b1, "delta": None if b0 is None or b1 is None else round(b1 - b0, 6)}

def report_line(engine, u, limits):
    """One line for the user about remaining usage. claude: only what the CLI itself emitted (rate-limit events), else 'not reported by CLI'."""
    if engine == "codex":
        import codex_usage
        return codex_usage.format_line(u or {"error": "no usage read"})
    if not limits: return "Claude (school) remaining: not reported by CLI"
    import codex_usage
    wins = {}
    for ev in limits: wins.update((ev.get("rate_limit_info") or {}).get("unifiedWindows") or {})
    if not wins: return "Claude (school) rate-limit event from CLI (no windows): " + json.dumps(limits[-1].get("rate_limit_info") or limits[-1])[:200]
    name = {"five_hour": "5h", "seven_day": "weekly"}
    parts = [f"{name.get(k, k)} used {round(float(v['utilization']) * 100, 1)}% (resets in {codex_usage._dur(v['resetsAt'] - time.time())})" for k, v in wins.items() if v.get("utilization") is not None]
    return "Claude (school) usage from CLI (utilization read as fraction of limit; undocumented, last run's event): " + ", ".join(parts) + ", status " + str((limits[-1].get("rate_limit_info") or {}).get("status"))

def build_cmd(engine, wd, last_msg, access, model=None, effort=None, schema=None, resume=None, allowed=None, perm="dontAsk", is_git=True):
    """schema: codex -> path to a JSON schema file; claude -> the schema as a JSON string. Prompt is always sent on stdin."""
    b = bin_path(engine)
    if engine == "codex":
        cmd = [b, "exec"] + (["resume"] if resume else []) + ["--json", "-o", last_msg]
        cmd += ["-c", f'sandbox_mode="{access}"'] if resume else ["-s", access, "-C", wd]  # `exec resume` has no -s/-C; cwd is set by the caller
        if not is_git: cmd.append("--skip-git-repo-check")
        if model: cmd += ["-m", model]
        if effort: cmd += ["-c", f'model_reasoning_effort="{effort}"']
        if schema: cmd += ["--output-schema", schema]
        if resume: cmd.append(resume)
        return cmd + ["-"]
    cmd = [b, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", perm, "--allowedTools", allowed or CLAUDE_TOOLS[access]]
    if model: cmd += ["--model", model]
    if effort: cmd += ["--effort", effort]
    if schema: cmd += ["--json-schema", schema]
    if resume: cmd += ["--resume", resume]
    return cmd

def parse_events(engine, path):
    """-> dict(session_id, tokens, message, structured, error, model, limits, cost_usd). Unknown events are ignored."""
    o = {"session_id": None, "tokens": dict.fromkeys(TOKEN_KEYS, 0), "message": None, "structured": None, "error": False, "model": None, "limits": [], "cost_usd": None, "denials": None}
    for line in open(path):
        try: ev = json.loads(line)
        except ValueError: continue
        t = ev.get("type") or ""
        if engine == "codex":
            if t == "thread.started": o["session_id"] = ev.get("thread_id")
            if t == "turn.completed":
                for k in TOKEN_KEYS: o["tokens"][k] += (ev.get("usage") or {}).get(k, 0) or 0
        else:
            if t == "system" and ev.get("subtype") == "init": o["session_id"] = ev.get("session_id") or o["session_id"]; o["model"] = ev.get("model")
            if "rate_limit" in t: o["limits"].append(ev)
            if t == "result":
                u = ev.get("usage") or {}
                o.update(session_id=ev.get("session_id") or o["session_id"], message=ev.get("result"), structured=ev.get("structured_output"), error=bool(ev.get("is_error")), cost_usd=ev.get("total_cost_usd"), denials=ev.get("permission_denials"))
                o["tokens"].update(input_tokens=u.get("input_tokens", 0) or 0, cached_input_tokens=u.get("cache_read_input_tokens", 0) or 0, output_tokens=u.get("output_tokens", 0) or 0)
                o["tokens"]["cache_creation_input_tokens"] = u.get("cache_creation_input_tokens", 0) or 0
    return o

def run(engine, prompt, wd, out, access="workspace-write", model=None, effort=None, timeout=3600, resume=None, schema=None,
        allowed=None, perm="dontAsk", min5=2, minw=1):
    """One gated attempt. Returns (exit_code, rec); rec is also written to out/run.json (unless gated)."""
    if effort and effort not in EFFORTS[engine]: raise ValueError(f"effort {effort!r} not valid for {engine}: {EFFORTS[engine]}")
    if perm not in PERMISSION_MODES: raise ValueError(f"permission mode {perm!r} not allowed: {PERMISSION_MODES}")
    os.makedirs(out, exist_ok=True)
    wj = lambda n, o: open(os.path.join(out, n), "w").write(json.dumps(o, indent=1))
    u0 = read_usage(engine); wj("usage_before.json", u0)
    d = decide(engine, u0, min5, minw); wj("route.json", d)
    if d["route"] != "run": return 10, {"run_dir": out, **d}
    is_git = bool(sh(["git", "rev-parse", "--is-inside-work-tree"], cwd=wd).strip())
    head0 = sh(["git", "rev-parse", "HEAD"], cwd=wd).strip() if is_git else None
    last_msg = os.path.join(out, "last_message.md")
    sch = None
    if schema:
        sch = json.dumps(schema)
        if engine == "codex": sch = os.path.join(out, "schema.json"); wj("schema.json", schema)
    cmd = build_cmd(engine, wd, last_msg, access, model, effort, sch, resume, allowed, perm, is_git)
    t0 = time.time(); ev_path = os.path.join(out, "events.jsonl")
    with open(ev_path, "w") as ev:
        try:
            p = subprocess.run(cmd, cwd=wd, input=prompt, stdout=ev, stderr=subprocess.PIPE, text=True, timeout=timeout)
            rc, err = p.returncode, p.stderr[-2000:]
        except subprocess.TimeoutExpired:
            rc, err = 124, "timeout"
    t1 = time.time()
    u1 = read_usage(engine); wj("usage_after.json", u1)
    git = {"is_git": is_git, "head_before": head0}
    if is_git:
        spec = ["--", "."] + GIT_EXCLUDE
        patch = sh(["git", "diff", head0 or "HEAD"] + spec, cwd=wd)
        untracked = sh(["git", "ls-files", "--others", "--exclude-standard"] + spec, cwd=wd)
        open(os.path.join(out, "diff.patch"), "w").write(patch + "\n# untracked files (contents not in this patch):\n" + untracked)
        git.update(diff_stat=sh(["git", "diff", "--stat", head0 or "HEAD"] + spec, cwd=wd).strip(), untracked=untracked.split())
    o = parse_events(engine, ev_path)
    if engine == "claude" and o["message"] is not None: open(last_msg, "w").write(o["message"])
    scan = err + (o["message"] or "" if engine == "claude" else open(ev_path).read()[-4000:])
    hit = (rc != 0 or o["error"]) and any(s in scan.lower() for s in LIMIT_WORDS)
    delta = {}
    if "windows" in u0 and "windows" in u1:
        b0 = {w["label"]: w["used_percent"] for w in u0["windows"]}; b1 = {w["label"]: w["used_percent"] for w in u1["windows"]}
        delta = {k: (b1[k] - b0[k]) for k in b0 if k in b1 and b0[k] is not None and b1[k] is not None}
    rec = {"engine": engine, "cli_version": sh([bin_path(engine), "--version"]).strip(), "cmd": cmd, "task_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
           "workdir": wd, "git": git, "exit_code": rc, "seconds": round(t1 - t0, 1), "used_percent_delta": delta, "hit_usage_limit": hit,
           "on_credits": d.get("on_credits"), "credits": credit_delta(u0, u1) if engine == "codex" else None,
           "model": o["model"] or model or "(host default)", "effort": effort or "(host default)", "session_id": o["session_id"], "tokens": o["tokens"],
           "cost_usd_reported": o["cost_usd"], "permission_denials": o["denials"], "rate_limit_events": o["limits"], "engine_reported_error": o["error"], "structured_output": o["structured"],
           "usage_report": report_line(engine, u1, o["limits"]), "usage_before": u0, "usage_after": u1,
           "final_message": o["message"] if engine == "claude" else (open(last_msg).read() if os.path.exists(last_msg) else None), "stderr_tail": err, "run_dir": out}
    wj("run.json", rec)
    return (12 if hit else (0 if rc == 0 and not o["error"] else 11)), rec

def main():
    a = argparse.ArgumentParser()
    a.add_argument("task"); a.add_argument("workdir"); a.add_argument("--engine", default="codex", choices=["codex", "claude"])
    a.add_argument("--sandbox", default="workspace-write", choices=["read-only", "workspace-write"])
    a.add_argument("--min-5h", type=float, default=2); a.add_argument("--min-week", type=float, default=1)
    a.add_argument("--timeout", type=int, default=3600); a.add_argument("--model", default=None); a.add_argument("--effort", default=None)
    a = a.parse_args()
    wd = os.path.abspath(a.workdir); os.makedirs(wd, exist_ok=True)
    out = os.path.join(wd, ".cli-subagents", datetime.datetime.now().strftime("%Y%m%dT%H%M%S") + f"_{os.getpid()}")
    try: rc, rec = run(a.engine, open(a.task).read(), wd, out, a.sandbox, a.model, a.effort, a.timeout, min5=a.min_5h, minw=a.min_week)
    except ValueError as e: print(json.dumps({"error": str(e)})); return 2
    if rc == 10: print(json.dumps(rec)); return rc
    print(json.dumps({"run_dir": out, "route": "run"})); print(json.dumps({k: rec[k] for k in ("engine", "exit_code", "seconds", "used_percent_delta", "hit_usage_limit", "model", "tokens")}))
    return rc

if __name__ == "__main__":
    sys.exit(main())
