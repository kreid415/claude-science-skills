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
Exit: 0 accepted | 10 gated before/between attempts | 12 usage limit hit | 20 acceptance not red on baseline |
      2 bad input | 30 all attempts failed (Claude takes over)."""
import argparse, fnmatch, hashlib, json, os, shutil, subprocess, sys, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task"); ap.add_argument("workdir")
    ap.add_argument("--accept", required=True)
    ap.add_argument("--accept-before", default="fail", choices=["fail", "pass", "skip"])
    ap.add_argument("--accept-file", action="append", default=[])
    ap.add_argument("--protect", action="append", default=[])
    ap.add_argument("--new-tests-cmd", default=None)
    ap.add_argument("--ladder", default="default,gpt-6-astra:xhigh,gpt-6-astra:max")
    ap.add_argument("--timeout", type=int, default=3000)
    ap.add_argument("--max-mb", type=float, default=500)
    ap.add_argument("--sandbox", default="workspace-write")
    ap.add_argument("--min-5h", type=float, default=2); ap.add_argument("--min-week", type=float, default=1)
    a = ap.parse_args()
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
    log = {"root": root, "accept": a.accept, "accept_before_mode": a.accept_before, "ladder": a.ladder, "attempts": [], "final": None}
    def save(): open(os.path.join(root, "loop.json"), "w").write(json.dumps(log, indent=1))
    # guard 1: acceptance must be red on the pristine baseline
    if a.accept_before != "skip":
        probe = os.path.join(root, "probe"); copy_tree(base, probe)
        rc, out = run_cmd(a.accept, probe)
        log["baseline_accept"] = {"rc": rc, "tail": out[-800:]}
        shutil.rmtree(probe, ignore_errors=True)
        if (a.accept_before == "fail" and rc == 0) or (a.accept_before == "pass" and rc != 0):
            log["final"] = {"status": "acceptance_invalid", "detail": f"accept rc={rc} on baseline, expected {a.accept_before}"}
            save(); print(json.dumps(log["final"])); return 20
    prev = ""
    for k, rung in enumerate([r for r in a.ladder.split(",") if r.strip()], 1):
        model, effort = parse_rung(rung)
        att = os.path.join(root, f"b{k}"); copy_tree(base, att)
        tf = os.path.join(root, f"task_{k}.md")
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
        viol = sorted(r for r in base_h if match(r, protect) and att_h.get(r) != base_h[r])
        viol += [f for f, h in acc_files.items() if not os.path.exists(f) or sha(f) != h]
        rec["protect_violations"] = viol
        if viol: rec["reasons"].append("protected files changed: " + ", ".join(viol[:10]))
        new_tests = sorted(r for r in att_h if r not in base_h and match(r, TEST_PATTERNS))
        rec["new_tests"] = new_tests
        if new_tests:
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
            if rec.get("new_tests_check", {}).get("suspicious_red"): review.append("baseline red may be an import/collection error; read new_tests_check.baseline_tail")
            log["final"] = {"status": "accepted", "attempt": k, "rung": rung, "patch": os.path.join(root, "final.patch"), "needs_review": review}
            save(); print(json.dumps(log["final"])); return 0
        prev = ("\n\n## Previous attempt " + str(k) + " failed an independent check\n" + "\n".join(rec["reasons"])[:3500] +
                "\nFix the root cause; do not edit tests or checks.\n")
    log["final"] = {"status": "all_attempts_failed", "attempts": len(log["attempts"])}
    save(); print(json.dumps(log["final"])); return 30

if __name__ == "__main__":
    sys.exit(main())
