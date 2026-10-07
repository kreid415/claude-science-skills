#!/usr/bin/env python3
"""codex_loop.py TASK.md WORKDIR --accept CMD [options]
Guarded, escalating Codex run. Never edits WORKDIR: every attempt runs in a fresh copy of a pristine baseline and the
accepted result is written as final.patch for review (apply with `patch -p1` from the repo root).

Guards (each is checked by code, not by the model):
  1. Acceptance is supplied by the caller (--accept CMD, run with ACCEPT_WORKDIR=<tree>); keep its files outside WORKDIR.
     It must FAIL on the pristine baseline (--accept-before fail, default); a check that already passes cannot show success.
  2. Pre-existing test/config files (defaults below + --protect GLOB) must be byte-identical after the attempt.
     Hashes of --accept-file paths are checked before and after.
  3. Tests the model wrote (new files matching test patterns) need --new-tests-cmd: they must FAIL on the baseline with
     those files added and PASS on the attempt tree. Without the command the attempt cannot be accepted.
Escalation ladder (--ladder, default 'default,gpt-6-astra:xhigh,gpt-6-astra:max'): rung = 'default' | MODEL | MODEL:EFFORT.
A failed attempt feeds its reasons and acceptance output into the next attempt (fresh baseline copy each time).
  4. --bad-variant PATCH (repeatable, `patch -p1` format): known-wrong implementations the acceptance must REJECT.
  5. --tests-only (tests for EXISTING code): no acceptance command. Needs --tests-cmd, --mutation-target FILE (repeatable) and optionally
     --bug-patch PATCH (repeatable, `patch -p1`, known real bugs). Guards: no pre-existing file may change; at least one new test file;
     the tests pass on the current code; every bug patch makes them FAIL; mutation testing of the targets (codex_mutate.py) must kill at
     least --mutation-min of the mutants on two different random samples (survivors are fed to the next rung, but the confirmation sample
     is fresh so the model cannot tune to the listed ones). Survivors may be equivalent mutants: read them (needs_review).
One loop at a time (lock file next to this script). Each loop appends an entry to the ledger (codex_ledger.py) and ends by reading the remaining 5h, weekly and credit amounts
(final.usage_report.report_line in loop.json, also printed) so they can be reported to the user.
Exit: 0 accepted | 10 gated before/between attempts | 12 usage limit hit | 20 acceptance invalid (passes baseline or a bad variant) |
      2 bad input | 30 all attempts failed (Claude takes over) | 40 another loop is running."""
import argparse, ast, fcntl, fnmatch, hashlib, json, os, shutil, subprocess, sys, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SKIP_DIRS = {".git", ".codex-offload", "__pycache__", ".pytest_cache"}
TEST_PATTERNS = ["tests/*", "test/*", "test_*.py", "*_test.py", "conftest.py", "*.test.*", "*.spec.*"]
DEFAULT_PROTECT = TEST_PATTERNS + ["pytest.ini", "tox.ini", ".github/*"]
SUSPICIOUS_RED = ("ImportError", "ModuleNotFoundError", "SyntaxError", "no tests ran", "collected 0 items")

def files(root):
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            p = os.path.join(dp, f)
            if os.path.islink(p): continue
            out.append(os.path.relpath(p, root))
    return sorted(out)

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()

def hashes(root): return {r: sha(os.path.join(root, r)) for r in files(root)}

def match(rel, pats): return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p) for p in pats)

def size_mb(root): return sum(os.path.getsize(os.path.join(root, r)) for r in files(root)) / 1e6

def copy_tree(src, dst):
    shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns(".codex-offload", "__pycache__", ".pytest_cache"))

def run_cmd(cmd, cwd, timeout=1800):
    env = dict(os.environ, ACCEPT_WORKDIR=cwd)
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        return 124, "timeout"

def parse_rung(r):
    r = r.strip()
    if r == "default": return None, None
    m, _, e = r.partition(":")
    return m or None, e or None

def _main(holder):
    ap = argparse.ArgumentParser()
    ap.add_argument("task"); ap.add_argument("workdir")
    ap.add_argument("--accept", default=None)
    ap.add_argument("--tests-only", action="store_true"); ap.add_argument("--tests-cmd", default=None)
    ap.add_argument("--mutation-target", action="append", default=[]); ap.add_argument("--bug-patch", action="append", default=[])
    ap.add_argument("--mutation-min", type=float, default=0.75); ap.add_argument("--mutation-max", type=int, default=60); ap.add_argument("--mutation-timeout", type=int, default=120)
    ap.add_argument("--accept-before", default="fail", choices=["fail", "pass", "skip"])
    ap.add_argument("--accept-file", action="append", default=[])
    ap.add_argument("--bad-variant", action="append", default=[])
    ap.add_argument("--protect", action="append", default=[])
    ap.add_argument("--new-tests-cmd", default=None)
    ap.add_argument("--ladder", default="default,gpt-6-astra:xhigh,gpt-6-astra:max")
    ap.add_argument("--timeout", type=int, default=3000)
    ap.add_argument("--max-mb", type=float, default=500)
    ap.add_argument("--sandbox", default="workspace-write")
    ap.add_argument("--min-5h", type=float, default=2); ap.add_argument("--min-week", type=float, default=1)
    a = ap.parse_args()
    if a.tests_only:
        if not (a.tests_cmd and a.mutation_target): print(json.dumps({"error": "--tests-only needs --tests-cmd and at least one --mutation-target"})); return 2
    elif not a.accept: print(json.dumps({"error": "--accept is required (or use --tests-only)"})); return 2
    wd = os.path.abspath(a.workdir)
    if not os.path.isdir(wd): print(json.dumps({"error": "workdir missing"})); return 2
    if size_mb(wd) > a.max_mb: print(json.dumps({"error": f"workdir > {a.max_mb} MB; copy too large"})); return 2
    task0 = open(a.task).read()
    root = os.path.join(wd, ".codex-offload", "loop_" + datetime.datetime.now().strftime("%Y%m%dT%H%M%S"))
    os.makedirs(root)
    base = os.path.join(root, "a"); copy_tree(wd, base)
    base_h = hashes(base)
    protect = DEFAULT_PROTECT + a.protect
    acc_files = {f: sha(f) for f in a.accept_file if os.path.exists(f)}
    log = {"mode": "tests_only" if a.tests_only else "implement", "root": root, "accept": a.accept, "accept_before_mode": a.accept_before, "ladder": a.ladder, "attempts": [], "final": None}
    log["started"] = int(time.time()); log["task_sha256"] = hashlib.sha256(task0.encode()).hexdigest(); log["workdir"] = wd; holder["log"] = log
    def save(): open(os.path.join(root, "loop.json"), "w").write(json.dumps(log, indent=1))
    # guard 1: acceptance must be red on the pristine baseline
    if not a.tests_only and a.accept_before != "skip":
        probe = os.path.join(root, "probe"); copy_tree(base, probe)
        rc, out = run_cmd(a.accept, probe)
        log["baseline_accept"] = {"rc": rc, "tail": out[-800:]}
        shutil.rmtree(probe, ignore_errors=True)
        if (a.accept_before == "fail" and rc == 0) or (a.accept_before == "pass" and rc != 0):
            log["final"] = {"status": "acceptance_invalid", "detail": f"accept rc={rc} on baseline, expected {a.accept_before}"}
            save(); print(json.dumps(log["final"])); return 20
    # guard 1b: acceptance must reject every known-bad variant
    for bp in ([] if a.tests_only else a.bad_variant):
        probe = os.path.join(root, "probe"); copy_tree(base, probe)
        pr = subprocess.run(["patch", "-p1", "-s", "-i", os.path.abspath(bp)], cwd=probe, capture_output=True, text=True)
        if pr.returncode != 0:
            log["final"] = {"status": "bad_variant_patch_failed", "detail": bp + ": " + (pr.stdout + pr.stderr)[-300:]}; save(); print(json.dumps(log["final"])); return 2
        rc, out = run_cmd(a.accept, probe); shutil.rmtree(probe, ignore_errors=True)
        log.setdefault("bad_variants", []).append({"patch": bp, "accept_rc": rc})
        if rc == 0:
            log["final"] = {"status": "acceptance_invalid", "detail": f"acceptance PASSES known-bad variant {bp}"}; save(); print(json.dumps(log["final"])); return 20
    if a.tests_only:
        import codex_mutate
        for tg in a.mutation_target:
            tp = os.path.join(base, tg)
            try: ast.parse(open(tp).read())
            except Exception as e:
                log["final"] = {"status": "bad_target", "detail": f"{tg}: {type(e).__name__}: {e}"}; save(); print(json.dumps(log["final"])); return 2
        for bp in a.bug_patch:
            probe = os.path.join(root, "probe"); copy_tree(base, probe)
            pr = subprocess.run(["patch", "-p1", "-s", "-i", os.path.abspath(bp)], cwd=probe, capture_output=True, text=True); shutil.rmtree(probe, ignore_errors=True)
            if pr.returncode != 0:
                log["final"] = {"status": "bug_patch_failed", "detail": bp + ": " + (pr.stdout + pr.stderr)[-300:]}; save(); print(json.dumps(log["final"])); return 2
    prev = ""
    for k, rung in enumerate([r for r in a.ladder.split(",") if r.strip()], 1):
        model, effort = parse_rung(rung)
        att = os.path.join(root, f"b{k}"); copy_tree(base, att)
        tf = os.path.join(root, f"task_{k}.md")
        if a.tests_only:
            guard = ("\n\n## Rules for this task\nOnly ADD new test files; do not modify or delete any existing file. Your tests must pass on the current code and must "
                     "detect incorrect behavior: they are judged by mutation testing and by known bugs you cannot see. Small changes to the code under test (flipped "
                     "comparisons, changed operators or constants, altered return values, negated conditions) must make your tests fail, so assert exact expected values and "
                     "cover every branch, boundary value and error path. Do not weaken, skip or special-case any check.\n")
        else:
          guard = ("\n\n## Rules for this task\nDo not modify or delete existing test files or test configuration. Do not weaken, skip or special-case "
                 "any check. Implement the required behavior in the code. Your result is judged by an independent check you cannot see, "
                 "run on a pristine copy of the original tree, so make the implementation correct in general, not only for the examples.\n")
        open(tf, "w").write(task0 + guard + prev)
        cmd = [sys.executable, os.path.join(HERE, "codex_run.py"), tf, att, "--timeout", str(a.timeout), "--sandbox", a.sandbox,
               "--min-5h", str(a.min_5h), "--min-week", str(a.min_week)]
        if model: cmd += ["--model", model]
        if effort: cmd += ["--effort", effort]
        p = subprocess.run(cmd, capture_output=True, text=True)
        rec = {"k": k, "rung": rung, "run_rc": p.returncode, "reasons": []}
        runs = sorted(d for d in os.listdir(os.path.join(att, ".codex-offload"))) if os.path.isdir(os.path.join(att, ".codex-offload")) else []
        if runs:
            rj = os.path.join(att, ".codex-offload", runs[-1], "run.json")
            if os.path.exists(rj): rec["tokens"] = json.load(open(rj)).get("tokens"); rec["run_dir"] = os.path.dirname(rj)
        log["attempts"].append(rec)
        if p.returncode == 10: log["final"] = {"status": "gated", "after_attempt": k - 1}; save(); print(json.dumps(log["final"])); return 10
        if p.returncode == 12: log["final"] = {"status": "usage_limit", "attempt": k}; save(); print(json.dumps(log["final"])); return 12
        if p.returncode != 0:
            rec["reasons"].append(f"exec_failed rc={p.returncode}")
        att_h = hashes(att)
        viol = sorted(r for r in base_h if (True if a.tests_only else match(r, protect)) and att_h.get(r) != base_h[r])
        viol += [f for f, h in acc_files.items() if not os.path.exists(f) or sha(f) != h]
        rec["protect_violations"] = viol
        if viol: rec["reasons"].append("protected files changed: " + ", ".join(viol[:10]))
        new_tests = sorted(r for r in att_h if r not in base_h and match(r, TEST_PATTERNS))
        rec["new_tests"] = new_tests
        if new_tests and not a.tests_only:
            if not a.new_tests_cmd: rec["reasons"].append("model-written tests present but --new-tests-cmd not given (unverified)")
            else:
                tb = os.path.join(root, f"tests_base_{k}"); copy_tree(base, tb)
                for r in new_tests:
                    os.makedirs(os.path.dirname(os.path.join(tb, r)) or tb, exist_ok=True); shutil.copy(os.path.join(att, r), os.path.join(tb, r))
                rcb, outb = run_cmd(a.new_tests_cmd, tb); rca, outa = run_cmd(a.new_tests_cmd, att)
                shutil.rmtree(tb, ignore_errors=True)
                susp = rcb != 0 and any(s in outb for s in SUSPICIOUS_RED)
                rec["new_tests_check"] = {"baseline_rc": rcb, "attempt_rc": rca, "suspicious_red": susp, "baseline_tail": outb[-600:]}
                if rcb == 0: rec["reasons"].append("model-written tests PASS on the baseline (vacuous)")
                if rca != 0: rec["reasons"].append("model-written tests fail on the attempt tree: " + outa[-600:])
        if a.tests_only:
            if not new_tests: rec["reasons"].append("no new test files were added")
            rct, outt = run_cmd(a.tests_cmd, att); rec["tests_rc"] = rct
            if rct != 0: rec["reasons"].append("tests fail on the current code: " + outt[-800:])
            elif not viol:
                for bp in a.bug_patch:
                    pb = os.path.join(root, f"bug_{k}"); copy_tree(att, pb)
                    subprocess.run(["patch", "-p1", "-s", "-i", os.path.abspath(bp)], cwd=pb, capture_output=True, text=True)
                    rcb2, outb2 = run_cmd(a.tests_cmd, pb); shutil.rmtree(pb, ignore_errors=True)
                    rec.setdefault("bug_checks", []).append({"patch": bp, "tests_rc": rcb2, "suspicious": rcb2 != 0 and any(x in outb2 for x in SUSPICIOUS_RED)})
                    if rcb2 == 0: rec["reasons"].append(f"tests do NOT catch known bug {bp}")
                if not rec["reasons"]:
                    for seed in (k, 1000 + k):
                        mr = codex_mutate.mutate(att, a.mutation_target, a.tests_cmd, a.mutation_max, a.mutation_timeout, seed)
                        rec.setdefault("mutation", []).append({"seed": seed, **{x: mr.get(x) for x in ("status", "candidates", "run", "killed", "timeouts", "score", "detail")}, "survivors": mr.get("survivors", [])[:25]})
                        if mr["status"] != "ok":
                            rec["reasons"].append(f"mutation testing could not run: {mr['status']} {mr.get('detail', '')}"); break
                        if mr["score"] < a.mutation_min:
                            lst = "; ".join(f"{x['file']}:{x['line']} {x['change']}" for x in mr["survivors"][:15])
                            rec["reasons"].append(f"mutation score {mr['score']} < {a.mutation_min} (sample seed {seed}); changes to the code the tests did not detect: {lst}"); break
        else:
            rcx, outx = run_cmd(a.accept, att)
            rec["accept_rc"] = rcx; rec["accept_tail"] = outx[-1500:]
            if rcx != 0: rec["reasons"].append("acceptance failed: " + outx[-1500:])
        rec["verdict"] = "accepted" if not rec["reasons"] else "failed"
        save()
        if rec["verdict"] == "accepted":
            patch = subprocess.run(["diff", "-ruN", "-x", ".git", "-x", ".codex-offload", "-x", "__pycache__", "-x", ".pytest_cache", "a", f"b{k}"],
                                   cwd=root, capture_output=True, text=True).stdout
            open(os.path.join(root, "final.patch"), "w").write(patch)
            review = []
            if new_tests: review.append("read model-written tests: " + ", ".join(new_tests))
            if rec.get("mutation"):
                lm = rec["mutation"][-1]; review.append(f"mutation score {lm['score']} ({lm['killed']}/{lm['run']} killed, seed {lm['seed']}); read the survivors for equivalent mutants: " + "; ".join(f"{x['file']}:{x['line']} {x['change']}" for x in lm["survivors"][:10]))
            if any(b.get("suspicious") for b in rec.get("bug_checks", [])): review.append("a bug patch made the tests fail with an import/syntax error; check bug_checks")
            if rec.get("new_tests_check", {}).get("suspicious_red"): review.append("baseline red may be an import/collection error; read new_tests_check.baseline_tail")
            log["final"] = {"status": "accepted", "attempt": k, "rung": rung, "patch": os.path.join(root, "final.patch"), "needs_review": review}
            save(); print(json.dumps(log["final"])); return 0
        prev = ("\n\n## Previous attempt " + str(k) + " failed an independent check\n" + "\n".join(rec["reasons"])[:3500] +
                "\nFix the root cause; do not edit tests or checks.\n")
    log["final"] = {"status": "all_attempts_failed", "attempts": len(log["attempts"])}
    save(); print(json.dumps(log["final"])); return 30

def _ledger_entry(log, rc):
    atts = log.get("attempts", []); fin = log.get("final") or {}
    tok = {}
    for t in (x.get("tokens") or {} for x in atts):
        for k, v in t.items(): tok[k] = tok.get(k, 0) + (v or 0)
    cred = 0.0; usage_before = usage_after = None; on_credits = False
    for x in atts:
        rd = x.get("run_dir")
        if rd and os.path.exists(os.path.join(rd, "run.json")):
            rj = json.load(open(os.path.join(rd, "run.json")))
            cred += (rj.get("credits") or {}).get("delta") or 0; on_credits = on_credits or bool(rj.get("on_credits"))
            if usage_before is None and os.path.exists(os.path.join(rd, "usage_before.json")): usage_before = json.load(open(os.path.join(rd, "usage_before.json"))).get("windows")
            if os.path.exists(os.path.join(rd, "usage_after.json")): usage_after = json.load(open(os.path.join(rd, "usage_after.json"))).get("windows")
    return {"id": os.path.basename(log["root"]), "workdir": log.get("workdir"), "task_sha256": log.get("task_sha256"), "ladder": log.get("ladder"),
            "status": fin.get("status", "unknown"), "exit_code": rc, "attempts": len(atts), "accepted_rung": fin.get("rung"),
            "needs_review": fin.get("needs_review"), "mode": log.get("mode"), "mutation_score": next((x["mutation"][-1].get("score") for x in reversed(atts) if x.get("mutation")), None), "tokens": tok, "credit_delta": round(cred, 6), "on_credits": on_credits,
            "windows_before": usage_before, "windows_after": usage_after, "started": log.get("started"), "seconds": int(time.time()) - (log.get("started") or int(time.time())),
            "outcome_check": None}

def main():
    lock = open(os.path.join(HERE, ".loop.lock"), "w")
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(json.dumps({"status": "busy", "detail": "another codex_loop.py is running"})); return 40
    holder = {}
    rc = _main(holder)
    if "log" in holder:
        log = holder["log"]
        try:
            sys.path.insert(0, HERE); import codex_usage
            try: u = codex_usage.read()
            except Exception as e: u = {"error": f"{type(e).__name__}: {e}"}
            rep = {"report_line": codex_usage.format_line(u), "usage": u}
            log["final"] = dict(log.get("final") or {}, usage_report=rep)
            open(os.path.join(log["root"], "loop.json"), "w").write(json.dumps(log, indent=1))
            print(json.dumps({"usage_report": rep["report_line"]}))
        except Exception as e:
            print(json.dumps({"usage_report_error": f"{type(e).__name__}: {e}"}))
        try:
            sys.path.insert(0, HERE); import codex_ledger
            codex_ledger.append(_ledger_entry(holder["log"], rc))
        except Exception as e:
            print(json.dumps({"ledger_error": f"{type(e).__name__}: {e}"}))
    return rc

if __name__ == "__main__":
    sys.exit(main())
