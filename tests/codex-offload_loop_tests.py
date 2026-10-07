"""Tests for codex_loop.py (guarded escalation). Run: python tests/codex-offload_loop_tests.py   (stdlib only)
A stub `codex` applies a per-model plan of file writes, so each guard is exercised with known-bad and known-good attempts:
acceptance-not-red, protected-file edit, vacuous model-written tests, unverified tests, escalation, all-fail, gating, no edit to WORKDIR."""
import hashlib, json, os, stat, subprocess, sys, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codex-offload", "scripts"))
fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)
STUB = """#!/usr/bin/env python3
import sys, json, os
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
    wd = a[a.index("-C") + 1]; prompt = sys.stdin.read()
    model = a[a.index("-m") + 1] if "-m" in a else "default"
    eff = next((x.split('"')[1] for x in a if x.startswith("model_reasoning_effort")), None)
    plan = json.load(open(os.environ["STUB_PLAN"]))
    acts = plan.get(model + ":" + eff) if eff and (model + ":" + eff) in plan else plan.get(model, [])
    for rel, content in acts:
        p = os.path.join(wd, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(content)
    open(os.environ["STUB_LOG"], "a").write("exec\\n")
    open(a[a.index("-o") + 1], "w").write("done")
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 1}}))
"""
d = tempfile.mkdtemp(); p = os.path.join(d, "codex"); open(p, "w").write(STUB); os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
ACC = os.path.join(d, "accept.py"); LOG = os.path.join(d, "stub.log")
open(ACC, "w").write("import os,sys\nsys.path.insert(0, os.environ['ACCEPT_WORKDIR'])\nimport calc\nok = calc.add(2,3)==5 and calc.add(-1,1)==0\nsys.exit(0 if ok else 1)\n")
GOOD = "def add(a, b):\n    return a + b\n"
def project(calc="def add(a, b):\n    return 0\n"):
    w = tempfile.mkdtemp(); os.makedirs(os.path.join(w, "tests"))
    open(os.path.join(w, "calc.py"), "w").write(calc); open(os.path.join(w, "tests", "test_calc.py"), "w").write("assert True\n")
    open(os.path.join(w, "task.md"), "w").write("Fix calc.add."); return w
def tree_hash(w):
    h = hashlib.sha256()
    for dp, dn, fn in sorted(os.walk(w)):
        if ".codex-offload" in dp: continue
        for f in sorted(fn): h.update(f.encode() + open(os.path.join(dp, f), "rb").read())
    return h.hexdigest()
def loop(w, plan, *extra):
    pf = os.path.join(d, "plan.json"); json.dump(plan, open(pf, "w"))
    env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], STUB_PLAN=pf, STUB_LOG=LOG)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "codex_loop.py"), os.path.join(w, "task.md"), w, "--accept", f"{sys.executable} {ACC}", "--accept-file", ACC, *extra],
                       env=env, capture_output=True, text=True, timeout=300)
    lj = [x for x in os.listdir(os.path.join(w, ".codex-offload")) if x.startswith("loop_")]
    log = json.load(open(os.path.join(w, ".codex-offload", lj[0], "loop.json"))) if lj else {}
    return r.returncode, log

# 1. acceptance already passes on the baseline -> invalid, no Codex run
w = project(GOOD); rc, log = loop(w, {})
check("acceptance green on baseline -> exit 20, no attempts", rc == 20 and log.get("attempts") == [])
# 2. escalation + protected-file guard; WORKDIR untouched
w = project(); h0 = tree_hash(w)
plan = {"default": [["calc.py", GOOD], ["tests/test_calc.py", "assert True  # edited\n"]], "gpt-6-astra:xhigh": [["calc.py", GOOD]]}
rc, log = loop(w, plan)
a1, a2 = log["attempts"][0], log["attempts"][1]
check("attempt 1 passes acceptance but is rejected for editing a protected test", a1["accept_rc"] == 0 and a1["verdict"] == "failed" and "tests/test_calc.py" in a1["protect_violations"])
check("escalates to the astra rung and accepts", rc == 0 and a2["rung"] == "gpt-6-astra:xhigh" and log["final"]["attempt"] == 2)
check("failure reasons are fed to the next attempt", "protected files changed" in open(os.path.join(log["root"], "task_2.md")).read())
check("final.patch exists and touches calc.py only", "calc.py" in open(log["final"]["patch"]).read() and "test_calc" not in open(log["final"]["patch"]).read())
check("WORKDIR is never modified", tree_hash(w) == h0)
# 3. every rung fails -> exit 30
w = project(); rc, log = loop(w, {})
check("all rungs fail -> exit 30 with 3 attempts", rc == 30 and len(log["attempts"]) == 3)
# 4. vacuous model-written tests are rejected, a discriminating test is accepted
TNEW_BAD = "import sys,os\nsys.path.insert(0, os.getcwd())\nimport calc\nassert True\n"
TNEW_OK = "import sys,os\nsys.path.insert(0, os.getcwd())\nimport calc\nassert calc.add(2,3)==5\n"
w = project()
plan = {"default": [["calc.py", GOOD], ["tests/test_new.py", TNEW_BAD]], "gpt-6-astra:xhigh": [["calc.py", GOOD], ["tests/test_new.py", TNEW_OK]]}
rc, log = loop(w, plan, "--new-tests-cmd", f"{sys.executable} tests/test_new.py")
check("vacuous model-written test (passes on baseline) rejected", log["attempts"][0]["verdict"] == "failed" and any("PASS on the baseline" in x for x in log["attempts"][0]["reasons"]))
check("discriminating model-written test accepted and flagged for review", rc == 0 and log["attempts"][1]["new_tests_check"]["baseline_rc"] != 0 and log["final"]["needs_review"])
# 5. model-written tests without a verification command cannot be accepted
w = project(); plan = {"default": [["calc.py", GOOD], ["tests/test_new.py", TNEW_OK]], "gpt-6-astra:xhigh": [["calc.py", GOOD], ["tests/test_new.py", TNEW_OK]], "gpt-6-astra:max": [["calc.py", GOOD], ["tests/test_new.py", TNEW_OK]]}
rc, log = loop(w, plan)
check("unverified model-written tests -> never accepted", rc == 30 and all("unverified" in " ".join(x["reasons"]) for x in log["attempts"]))
# 6. gating stops before any attempt
w = project(); open(LOG, "w").close(); rc, log = loop(w, {}, "--min-5h", "95")
check("usage gate -> exit 10 without a Codex exec", rc == 10 and open(LOG).read() == "")
# 7. tampering with the acceptance file is detected
w = project(); rc, log = loop(w, {"default": [["calc.py", GOOD]]})
check("clean run accepted at the first rung, no violations (control)", rc == 0 and log["final"]["attempt"] == 1 and log["attempts"][0]["protect_violations"] == [])
# 8. an attempt that rewrites the acceptance script (outside WORKDIR) is rejected even though the rewritten check passes
orig_acc = open(ACC).read(); w = project()
rc, log = loop(w, {"default": [["calc.py", GOOD], [ACC, "import sys\nsys.exit(0)\n"]]})
open(ACC, "w").write(orig_acc)
check("acceptance-file tampering detected", log["attempts"][0]["verdict"] == "failed" and ACC in log["attempts"][0]["protect_violations"])
print(f"{len(fails)} failed" if fails else "all codex-offload loop tests passed")
sys.exit(1 if fails else 0)
