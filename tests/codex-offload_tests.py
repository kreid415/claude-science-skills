"""Tests for codex-offload. Run: python tests/codex-offload_tests.py   (stdlib only)
1. decide(): gate routes to Claude at/below the floor, when ordinary usage is disallowed, or when the usage read failed.
2. codex_usage.py end to end against a stub `codex app-server` speaking JSON-RPC: window labels come from
   windowDurationMins (swapped primary/secondary must still label correctly).
3. codex_run.py end to end against a stub `codex`: model and effort flags, token totals, and the usage gate."""
import importlib.util, json, os, stat, subprocess, sys, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codex-offload", "scripts"))
spec = importlib.util.spec_from_file_location("codex_run", os.path.join(ROOT, "codex_run.py"))
cr = importlib.util.module_from_spec(spec); spec.loader.exec_module(cr)
fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)
def U(five, week, allowed=True):
    w = lambda lab, mins, rem: {"label": lab, "window_mins": mins, "remaining_percent": rem, "used_percent": 100 - rem, "resets_at": 1}
    return {"windows": [w("5h", 300, five), w("weekly", 10080, week)], "ordinary_usage_allowed": allowed}
check("headroom routes to codex", cr.decide(U(50, 50), 2, 1)["route"] == "codex")
check("5h below floor routes to claude", cr.decide(U(1, 50), 2, 1)["route"] == "claude")
check("weekly below floor routes to claude", cr.decide(U(50, 0), 2, 1)["route"] == "claude")
check("exactly at floor still codex", cr.decide(U(2, 1), 2, 1)["route"] == "codex")
check("ordinary_usage_allowed false routes to claude", cr.decide(U(50, 50, False), 2, 1)["route"] == "claude")
check("usage read error routes to claude", cr.decide({"error": "x"}, 2, 1)["route"] == "claude")
check("weekly exhaustion reports weekly reset", cr.decide(U(50, 0), 2, 1)["retry_at"] == 1)

stub = """#!/usr/bin/env python3
import sys, json
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize":
        print(json.dumps({"id": m["id"], "result": {"userAgent": "stub", "codexHome": "/x"}}), flush=True)
    elif m.get("method") == "account/rateLimits/read":
        # weekly window deliberately in the primary slot
        snap = {"limitId": "codex", "primary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 4102444800},
                "secondary": {"usedPercent": 90, "windowDurationMins": 300, "resetsAt": 4102444800}, "planType": "plus"}
        print(json.dumps({"id": m["id"], "result": {"ordinaryUsageAllowed": True, "rateLimits": snap, "rateLimitsByLimitId": {"codex": snap}}}), flush=True)
"""
d = tempfile.mkdtemp(); p = os.path.join(d, "codex"); open(p, "w").write(stub); os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"])
r = subprocess.run([sys.executable, os.path.join(ROOT, "codex_usage.py")], env=env, capture_output=True, text=True, timeout=60)
out = json.loads(r.stdout)
lab = {w["label"]: w for w in out["windows"]}
check("usage exits 0", r.returncode == 0)
check("weekly labelled by duration, remaining 60", lab.get("weekly", {}).get("remaining_percent") == 60)
check("5h labelled by duration, remaining 10", lab.get("5h", {}).get("remaining_percent") == 10)
check("5h at 10% routes to codex, floor 11 routes to claude", cr.decide(out, 2, 1)["route"] == "codex" and cr.decide(out, 11, 1)["route"] == "claude")

# 3. codex_run.py end to end against a stub `codex` (exec records its argv, emits a turn.completed event)
stub2 = """#!/usr/bin/env python3
import sys, json
a = sys.argv[1:]
if a[:1] == ["--version"]: print("codex-cli stub"); sys.exit(0)
if a[:1] == ["app-server"]:
    for line in sys.stdin:
        m = json.loads(line)
        if m.get("method") == "initialize": print(json.dumps({"id": m["id"], "result": {}}), flush=True)
        elif m.get("method") == "account/rateLimits/read":
            snap = {"primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 4102444800}, "secondary": {"usedPercent": 20, "windowDurationMins": 10080, "resetsAt": 4102444800}}
            print(json.dumps({"id": m["id"], "result": {"ordinaryUsageAllowed": True, "rateLimits": snap}}), flush=True)
    sys.exit(0)
if a[:1] == ["exec"]:
    open(a[a.index("-C") + 1] + "/argv.json", "w").write(json.dumps(a))
    sys.stdin.read()
    open(a[a.index("-o") + 1], "w").write("done")
    for u in ({"input_tokens": 100, "output_tokens": 5, "cached_input_tokens": 0, "reasoning_output_tokens": 1}, {"input_tokens": 50, "output_tokens": 7}):
        print(json.dumps({"type": "turn.completed", "usage": u}))
"""
p2 = os.path.join(d, "codex"); open(p2, "w").write(stub2); os.chmod(p2, os.stat(p2).st_mode | stat.S_IEXEC)
wd = tempfile.mkdtemp(); task = os.path.join(wd, "task.md"); open(task, "w").write("do it")
wk = os.path.join(wd, "work")
r = subprocess.run([sys.executable, os.path.join(ROOT, "codex_run.py"), task, wk, "--model", "gpt-x", "--effort", "high"], env=env, capture_output=True, text=True, timeout=120)
check("runner exits 0 with headroom", r.returncode == 0)
argv = json.load(open(os.path.join(wk, "argv.json")))
check("runner passes -m model", argv[argv.index("-m") + 1] == "gpt-x")
check("runner passes effort override", 'model_reasoning_effort="high"' in argv)
rdir = os.path.join(wk, ".codex-offload", os.listdir(os.path.join(wk, ".codex-offload"))[0])
rj = json.load(open(os.path.join(rdir, "run.json")))
check("run.json sums tokens over turns", rj["tokens"]["input_tokens"] == 150 and rj["tokens"]["output_tokens"] == 12)
check("run.json records model and effort", rj["model"] == "gpt-x" and rj["effort"] == "high")
wk2 = os.path.join(wd, "work2")
r = subprocess.run([sys.executable, os.path.join(ROOT, "codex_run.py"), task, wk2, "--min-5h", "95"], env=env, capture_output=True, text=True, timeout=120)
check("runner gates (exit 10) when 5h remaining 90% < floor 95%", r.returncode == 10 and not os.path.exists(os.path.join(wk2, "argv.json")))
print(f"{len(fails)} failed" if fails else "all codex-offload tests passed")
sys.exit(1 if fails else 0)
