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
if a[:2] == ["login", "status"]: print("Logged in using ChatGPT"); sys.exit(0)
if a[:1] == ["app-server"]:
    for line in sys.stdin:
        m = json.loads(line)
        if m.get("method") == "initialize": print(json.dumps({"id": m["id"], "result": {}}), flush=True)
        elif m.get("method") == "account/rateLimits/read":
            ff = os.environ.get("STUB_FAIL_FIRST")
            if ff and not os.path.exists(ff):
                open(ff, "w").write("x"); import time; time.sleep(30); sys.exit(0)
            snap = {"primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 4102444800}, "secondary": {"usedPercent": 20, "windowDurationMins": 10080, "resetsAt": 4102444800}}
            if os.environ.get("STUB_CREDITS"): snap["credits"] = {"hasCredits": True, "unlimited": False, "balance": os.environ["STUB_CREDITS"]}
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
ACC = os.path.join(d, "accept.py"); LOG = os.path.join(d, "stub.log"); LEDGER = os.path.join(d, "ledger.jsonl")
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
def loop(w, plan, *extra, acc=None):
    pf = os.path.join(d, "plan.json"); json.dump(plan, open(pf, "w"))
    env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], STUB_PLAN=pf, STUB_LOG=LOG, CODEX_LEDGER=LEDGER)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "codex_loop.py"), os.path.join(w, "task.md"), w, "--accept", f"{sys.executable} {acc or ACC}", "--accept-file", acc or ACC, *extra],
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

# 9. acceptance must reject known-bad variants
import fcntl, shutil
WEAK = os.path.join(d, "weak_accept.py")
open(WEAK, "w").write("import os,sys\nsys.path.insert(0, os.environ['ACCEPT_WORKDIR'])\nimport calc\nsys.exit(0 if calc.add(2,3)==5 else 1)\n")
BAD = os.path.join(d, "bad.patch")
open(BAD, "w").write("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return 0\n+    return 5\n")
w = project(); open(LOG, "w").close(); rc, log = loop(w, {"default": [["calc.py", GOOD]]}, "--bad-variant", BAD, acc=WEAK)
check("weak acceptance passes a known-bad variant -> exit 20, no Codex exec", rc == 20 and open(LOG).read() == "")
w = project(); rc, log = loop(w, {"default": [["calc.py", GOOD]]}, "--bad-variant", BAD)
check("strict acceptance rejects the bad variant, run proceeds", rc == 0 and log["bad_variants"][0]["accept_rc"] != 0)
# 10. only one loop at a time
lockf = open(os.path.join(ROOT, ".loop.lock"), "w"); fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
w = project(); pf = os.path.join(d, "plan.json"); json.dump({}, open(pf, "w"))
r = subprocess.run([sys.executable, os.path.join(ROOT, "codex_loop.py"), os.path.join(w, "task.md"), w, "--accept", f"{sys.executable} {ACC}"],
                   env=dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], STUB_PLAN=pf, STUB_LOG=LOG, CODEX_LEDGER=LEDGER), capture_output=True, text=True)
fcntl.flock(lockf, fcntl.LOCK_UN); lockf.close()
check("second concurrent loop refused with exit 40", r.returncode == 40)
# 11. ledger records each loop and tracks escaped defects
env_l = dict(os.environ, CODEX_LEDGER=LEDGER)
rows = [json.loads(x) for x in open(LEDGER) if x.strip()]
check("every loop run with a log is in the ledger", len(rows) >= 8 and all("status" in r and "attempts" in r for r in rows))
acc_row = next(r for r in rows if r["status"] == "accepted")
mk = subprocess.run([sys.executable, os.path.join(ROOT, "codex_ledger.py"), "mark", acc_row["id"], "--defect", "--note", "found later"], env=env_l, capture_output=True, text=True)
sm = json.loads(subprocess.run([sys.executable, os.path.join(ROOT, "codex_ledger.py"), "summary"], env=env_l, capture_output=True, text=True).stdout)
check("ledger mark and summary report the escaped defect", json.loads(mk.stdout)["marked"] and sm["escaped_defects"] == 1 and sm["escaped_defect_rate"] is not None)
# 12. canary: quick checks, manifest drift, failing usage read, full known-answer task
cd = tempfile.mkdtemp()
for n in ["codex_usage.py", "codex_run.py", "codex_loop.py", "codex_ledger.py", "codex_canary.py"]: shutil.copy(os.path.join(ROOT, n), os.path.join(cd, n))
def write_manifest():
    open(os.path.join(cd, "MANIFEST.sha256"), "w").write("".join(f"{hashlib.sha256(open(os.path.join(cd, n), 'rb').read()).hexdigest()}  {n}\n" for n in ["codex_usage.py", "codex_run.py", "codex_loop.py", "codex_ledger.py", "codex_canary.py"]))
write_manifest()
cenv = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], STUB_PLAN=os.path.join(d, "plan.json"), STUB_LOG=LOG, CODEX_LEDGER=LEDGER)
def canary(*args, env=None):
    r = subprocess.run([sys.executable, os.path.join(cd, "codex_canary.py"), *args], env=env or cenv, capture_output=True, text=True, timeout=300)
    return r.returncode, json.loads(r.stdout)
rc, out = canary()
check("canary quick checks pass with intact scripts", rc == 0 and all(c["ok"] for c in out["checks"].values()))
open(os.path.join(cd, "codex_run.py"), "a").write("\n# drift\n")
rc, out = canary()
check("canary detects a script that differs from the manifest", rc == 1 and not out["checks"]["scripts_match_manifest"]["ok"])
write_manifest()
broken = os.path.join(tempfile.mkdtemp(), "codex"); open(broken, "w").write("#!/usr/bin/env python3\nimport sys\nif sys.argv[1:2] == ['--version']: print('v'); sys.exit(0)\nif sys.argv[1:3] == ['login', 'status']: print('Logged in using ChatGPT'); sys.exit(0)\nsys.exit(1)\n"); os.chmod(broken, 0o755)
rc, out = canary(env=dict(cenv, PATH=os.path.dirname(broken) + os.pathsep + os.environ["PATH"]))
check("canary fails loudly when the usage read breaks", rc == 1 and not out["checks"]["usage_read"]["ok"])
json.dump({"default": [["slug.py", "import unicodedata, re\ndef slugify(s):\n    n = unicodedata.normalize('NFKD', s)\n    n = ''.join(c for c in n if not unicodedata.combining(c))\n    return re.sub(r'[^a-z0-9]+', '-', n.lower()).strip('-')\n"]]}, open(os.path.join(d, "plan.json"), "w"))
rc, out = canary("--full")
check("canary full known-answer task passes through the loop", rc == 0 and out["checks"]["full_known_answer_task"]["ok"])
st = json.load(open(os.path.join(cd, "canary_state.json")))
rc, out = canary("--auto")
check("auto skips the full task when it passed recently and the version is unchanged", rc == 0 and out["full_due"] is False and "full_ran" not in out)
st["codex_version"] = "codex-cli older"; json.dump(st, open(os.path.join(cd, "canary_state.json"), "w"))
rc, out = canary("--auto")
check("auto runs the full task after a Codex version change", rc == 0 and out["version_changed"] and out.get("full_ran"))
check("canary runs are kept out of the task ledger", not any(json.loads(l).get("workdir", "").find("canary_") >= 0 for l in open(LEDGER) if l.strip()) and os.path.exists(os.path.join(cd, "canary_ledger.jsonl")))
# 13. after Codex use, the remaining 5h, weekly and credit amounts are reported (also when gated or failed)
os.environ["STUB_CREDITS"] = "123.5"
w = project(); rc, log = loop(w, {"default": [["calc.py", GOOD]]})
line = log["final"]["usage_report"]["report_line"]
check("report line has 5h, weekly and credits after an accepted run", rc == 0 and "5h 90%" in line and "weekly 80%" in line and "credits 123.5" in line)
w = project(); rc, log = loop(w, {"default": [["calc.py", GOOD]]}, "--min-5h", "95")
check("window below floor but credits available -> runs on credits (no credit gate)", rc == 0 and log["attempts"][0]["run_rc"] == 0)
del os.environ["STUB_CREDITS"]
w = project(); rc, log = loop(w, {}, "--min-5h", "95")
check("report line is also produced when the run is gated", rc == 10 and "weekly 80%" in log["final"]["usage_report"]["report_line"] and "credits none" in log["final"]["usage_report"]["report_line"])
os.environ["STUB_CREDITS"] = "123.5"
w = project(); rc, log = loop(w, {})
check("report line is produced when every attempt fails", rc == 30 and "credits 123.5" in log["final"]["usage_report"]["report_line"])
ul = subprocess.run([sys.executable, os.path.join(ROOT, "codex_usage.py"), "--line"], env=dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"]), capture_output=True, text=True).stdout.strip()
check("codex_usage.py --line prints the one-line report", ul.startswith("Codex remaining: 5h 90%") and "credits 123.5" in ul)
del os.environ["STUB_CREDITS"]
# 14. a transient timeout of the usage request is retried
ff = os.path.join(d, "failfirst.flag")
if os.path.exists(ff): os.remove(ff)
r = subprocess.run([sys.executable, os.path.join(ROOT, "codex_usage.py"), "--line"], env=dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], STUB_FAIL_FIRST=ff, CODEX_USAGE_TIMEOUT="2"), capture_output=True, text=True, timeout=120)
check("usage read recovers from one timed-out request by retrying", r.stdout.startswith("Codex remaining: 5h 90%") and os.path.exists(ff))
try: os.remove(os.path.join(ROOT, ".loop.lock"))
except OSError: pass
print(f"{len(fails)} failed" if fails else "all codex-offload loop tests passed")
sys.exit(1 if fails else 0)
