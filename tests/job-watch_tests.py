"""Tests for job-watch (jw.py). Run: python tests/job-watch_tests.py   (stdlib only; takes about a minute)
Local jobs are real processes. SLURM is simulated by fake sbatch/squeue/sacct/scancel executables driven by a per-submission plan, so every
branch (TIMEOUT, OOM, NODE_FAIL, FAILED, CANCELLED, checkpoint exit 85, USR1 margin, validation) is exercised with a known outcome."""
import json, os, shutil, signal, stat, subprocess, sys, tempfile, time
JW = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "job-watch", "scripts", "jw.py"))
fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)
T = tempfile.mkdtemp(prefix="jwt_")
def run(args, env=None, timeout=120):
    p = subprocess.run([sys.executable, JW] + args, capture_output=True, text=True, env=env or os.environ, timeout=timeout)
    try: out = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception: out = {"raw": p.stdout[-300:], "err": p.stderr[-300:]}
    return p.returncode, out
def sdir(name):
    d = os.path.join(T, name); os.makedirs(d, exist_ok=True); return os.path.join(d, "state"), d

# ------------------------------------------------------------------ local jobs (real processes)
def local(name, cmd, *extra, watch=True, interval="0.3", deadline="0.5"):
    st, wd = sdir(name)
    rc, out = run(["start", "--state", st, "--kind", "local", "--cmd", cmd, "--workdir", wd, *extra])
    assert rc == 0, out
    if not watch: return st, wd, None
    rc, out = run(["watch", "--state", st, "--interval", interval, "--deadline-min", deadline])
    return st, wd, (rc, out)
def segs(st): return json.load(open(os.path.join(st, "state.json")))["segments"]
st, wd, (rc, out) = local("l1", "echo hi > out.txt", "--expect", "out.txt")
check("local: command that succeeds is done (exit 0) and validated", rc == 0 and os.path.exists(os.path.join(wd, "out.txt")))
st, wd, (rc, out) = local("l2", '[ "$JW_ATTEMPT" -ge 2 ] && echo "$JW_RESUME" > out.txt || exit 1', "--expect", "out.txt", "--retries", "1")
check("local: a failed first attempt is retried with JW_ATTEMPT=2 and JW_RESUME=1", rc == 0 and open(os.path.join(wd, "out.txt")).read().strip() == "1" and len(segs(st)) == 2)
st, wd, (rc, out) = local("l3", "exit 3", "--retries", "1")
check("local: retries are bounded; then failed (exit 20)", rc == 20 and len(segs(st)) == 2)
st, wd, (rc, out) = local("l4", 'if [ "$JW_ATTEMPT" -ge 2 ]; then echo ok > out.txt; fi', "--expect", "out.txt", "--retries", "1")
check("local: exit 0 without the expected output is not done; retried", rc == 0 and len(segs(st)) == 2)
st, wd, (rc, out) = local("l5", "exit 0", "--validate", "false", "--retries", "0")
check("local: --validate that fails makes the job failed even with exit 0", rc == 20 and "validate" in json.dumps(out) + open(os.path.join(st, "result.json")).read())
st, wd, (rc, out) = local("l6", 'if [ "$JW_ATTEMPT" -ge 2 ]; then echo done > out.txt; else exit 85; fi', "--expect", "out.txt", "--retries", "0")
check("local: exit 85 means checkpointed; continues without using a retry", rc == 0 and len(segs(st)) == 2)
st, wd, (rc, out) = local("l7", 'if [ "$JW_ATTEMPT" -ge 2 ]; then echo done > out.txt; else echo start; sleep 600; fi', "--expect", "out.txt", "--stall-min", "0.04", "--retries", "0", interval="0.5", deadline="1")
check("local: a stalled job (no output growth) is killed and retried", rc == 0 and len(segs(st)) == 2 and segs(st)[0]["exit"] == "stalled")
st, wd, _ = local("l8", "sleep 600", "--retries", "0", watch=False)
pid = segs(st)[0]["id"]; os.killpg(pid, signal.SIGKILL); time.sleep(0.5)
rc, out = run(["tick", "--state", st])
check("local: a job killed from outside (SIGKILL) is detected and retried", rc == 10 and len(segs(st)) == 2)
run(["stop", "--state", st])
st1, wd1, _ = local("l9a", "sleep 600", watch=False); st2, wd2, _ = local("l9b", "sleep 600", watch=False)
p1, p2 = segs(st1)[0]["id"], segs(st2)[0]["id"]
rc, out = run(["stop", "--state", st1]); time.sleep(0.5)
alive = lambda p: os.path.exists(f"/proc/{p}") and open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[0] != "Z"
check("local: stop ends only the recorded job (exit 40); a sibling job keeps running", rc == 40 and not alive(p1) and alive(p2))
run(["stop", "--state", st2])
st, wd, (rc, out) = local("l10", "sleep 4; echo x > out.txt", "--expect", "out.txt", interval="0.3", deadline="0.02")
check("watch: deadline returns exit 10 while the job is still running", rc == 10)
rc, out = run(["watch", "--state", st, "--interval", "0.5", "--deadline-min", "0.5"])
check("watch: running it again resumes the same job and finishes (exit 0)", rc == 0 and len(segs(st)) == 1)
st, wd, _ = local("l11", "sleep 3", watch=False)
import fcntl
lk = open(os.path.join(st, "jw.lock"), "w"); fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
rc, out = run(["tick", "--state", st]); fcntl.flock(lk, fcntl.LOCK_UN); lk.close()
check("lock: a second watcher on the same state is refused (exit 41)", rc == 41)
run(["stop", "--state", st])
ext = subprocess.Popen(["bash", "-c", "sleep 1.5; echo made > %s" % os.path.join(T, "attached_out.txt")], start_new_session=True)
st, wd = sdir("l12")
run(["start", "--state", st, "--kind", "local", "--attach-pid", str(ext.pid), "--workdir", wd, "--expect", os.path.join(T, "attached_out.txt")])
rc, out = run(["watch", "--state", st, "--interval", "0.4", "--deadline-min", "0.5"]); ext.wait()
check("local: an already-running process can be attached and validated when it ends", rc == 0)

# ------------------------------------------------------------------ fake SLURM
FB = os.path.join(T, "fakebin"); os.makedirs(FB)
FAKE = r'''#!/usr/bin/env python3
import json, os, re, sys
D = os.environ["FAKE_DIR"]; name = os.path.basename(sys.argv[0]); a = sys.argv[1:]
def jl(): return os.path.join(D, "calls.log")
open(jl(), "a").write(name + " " + " ".join(a) + "\n")
if name == "sinfo": print("3-00:00:00"); sys.exit(0)
plan = json.load(open(os.path.join(D, "plan.json")))
def jobfile(j): return os.path.join(D, "job_%s.json" % j)
if name == "sbatch":
    idx = len([f for f in os.listdir(D) if f.startswith("job_")])
    ncall = len([l for l in open(jl()) if l.startswith("sbatch")]) - 1
    if ncall in plan.get("sbatch_fail", []):
        sys.stderr.write("sbatch: error: simulated failure\n"); sys.exit(1)
    jid = 1001 + idx; script = a[-1]; text = open(script).read()
    args = {"argv": a, "script_text": text}
    for src in (text.splitlines() + [" ".join(a)]):
        m = re.search(r"--time[= ](\S+)", src)
        if m: args["time"] = m.group(1)
        m = re.search(r"--mem[= ](\S+)", src)
        if m: args["mem"] = m.group(1)
        m = re.search(r"--signal=(\S+)", src)
        if m: args["signal"] = m.group(1)
    p = plan["jobs"][min(idx, len(plan["jobs"]) - 1)]
    json.dump({"id": jid, "plan": p, "args": args, "ticks": 0}, open(jobfile(jid), "w"))
    print(jid); sys.exit(0)
if name == "scancel": sys.exit(0)
jid = None
for i, x in enumerate(a):
    if x == "-j": jid = a[i + 1]
j = json.load(open(jobfile(jid))) if os.path.exists(jobfile(jid)) else None
if name == "squeue":
    if not j: sys.exit(0)
    p = j["plan"]; t = j["ticks"]; j["ticks"] += 1; json.dump(j, open(jobfile(jid), "w"))
    if t < p.get("running_ticks", 1):
        left = (p.get("left_seq") or ["00:30:00"])[min(t, len(p.get("left_seq") or [0]) - 1)]
        print("RUNNING|00:05:00|%s|%s" % (j["args"].get("time", "01:00:00"), left))
    sys.exit(0)
if name == "sacct":
    if not j: sys.exit(0)
    p = j["plan"]; el = p.get("elapsed", "00:20:00")
    if p.get("touch"): open(p["touch"], "w").write(str(jid))
    if p.get("write"): open(p["write"]["path"], "w").write(p["write"]["text"])
    print("%s|%s|%s|%s|" % (jid, p["final"], p.get("exit", "0:0"), el))
    print("%s.batch|%s|%s|%s|%s" % (jid, p["final"], p.get("exit", "0:0"), el, p.get("maxrss", "")))
    sys.exit(0)
'''
for n in ("sbatch", "squeue", "sacct", "scancel", "sinfo"):
    p = os.path.join(FB, n); open(p, "w").write(FAKE); os.chmod(p, 0o755)
def slurm(name, plan, *extra, ticks=12, pre=None):
    st, wd = sdir(name); fd = os.path.join(T, name + "_fake"); os.makedirs(fd, exist_ok=True)
    json.dump(plan, open(os.path.join(fd, "plan.json"), "w"))
    env = dict(os.environ, PATH=FB + os.pathsep + os.environ["PATH"], FAKE_DIR=fd)
    rc, out = run(["start", "--state", st, "--kind", "slurm", "--cmd", "python3 run.py", "--workdir", wd, "--time", "01:00:00", "--sbatch-args", "--partition=cpu --account=x", *extra], env=env)
    assert rc == 0, out
    rc = 10
    for _ in range(ticks):
        rc, out = run(["tick", "--state", st], env=env)
        if rc != 10: break
    jobs = {}
    for f in sorted(os.listdir(fd)):
        if f.startswith("job_"): j = json.load(open(os.path.join(fd, f))); jobs[j["id"]] = j
    calls = open(os.path.join(fd, "calls.log")).read().splitlines()
    return rc, out, jobs, calls, st, env, fd
rc, out, jobs, calls, st, env, fd = slurm("s1", {"jobs": [{"final": "COMPLETED", "running_ticks": 2}]})
check("slurm: a job that completes is done (exit 0); one sbatch call", rc == 0 and sum(c.startswith("sbatch") for c in calls) == 1)
rc, out, jobs, calls, st, env, fd = slurm("s2", {"jobs": [{"final": "TIMEOUT", "running_ticks": 1}, {"final": "COMPLETED", "running_ticks": 1}]})
j1, j2 = jobs[1001], jobs[1002]
check("slurm: TIMEOUT resubmits with a longer --time (01:00 -> 01:30) and JW_RESUME=1", rc == 0 and j1["args"]["time"] == "01:00:00" and j2["args"]["time"] == "01:30:00" and "JW_RESUME=1" in j2["args"]["script_text"] and "JW_RESUME=0" in j1["args"]["script_text"])
rc, out, jobs, calls, st, env, fd = slurm("s3", {"jobs": [{"final": "TIMEOUT", "running_ticks": 1}] * 8}, "--max-time", "01:30:00", "--max-attempts", "6")
check("slurm: repeated TIMEOUT at --max-time without --restartable ends needs_human (exit 30)", rc == 30 and len(jobs) == 2)
rc, out, jobs, calls, st, env, fd = slurm("s3a", {"jobs": [{"final": "TIMEOUT", "running_ticks": 1}] * 12}, "--max-attempts", "10")
tl = [jobs[k]["args"]["time"] for k in sorted(jobs)]
check("slurm: without --max-time the default cap is 4x the first --time (01:00 -> 01:30 -> 02:15 -> 03:23 -> 04:00), then needs_human", rc == 30 and tl == ["01:00:00", "01:30:00", "02:15:00", "03:23:00", "04:00:00"])
rc, out, jobs, calls, st, env, fd = slurm("s3b", {"jobs": [{"final": "TIMEOUT", "running_ticks": 1}, {"final": "TIMEOUT", "running_ticks": 1}, {"final": "COMPLETED", "running_ticks": 1}]}, "--max-time", "01:30:00", "--restartable")
check("slurm: with --restartable it keeps continuing at the maximum time until done", rc == 0 and len(jobs) == 3 and jobs[1003]["args"]["time"] == "01:30:00")
rc, out, jobs, calls, st, env, fd = slurm("s4", {"jobs": [{"final": "OUT_OF_MEMORY", "exit": "0:125", "running_ticks": 1}, {"final": "COMPLETED", "running_ticks": 1}]}, "--mem", "8G")
check("slurm: OUT_OF_MEMORY resubmits with 1.5x memory (8G -> 12288M)", rc == 0 and jobs[1001]["args"]["mem"] == "8192M" and jobs[1002]["args"]["mem"] == "12288M")
rc, out, jobs, calls, st, env, fd = slurm("s4b", {"jobs": [{"final": "OUT_OF_MEMORY", "running_ticks": 1}] * 6}, "--mem", "8G", "--max-mem", "10G")
check("slurm: memory growth beyond --max-mem stops for a human (exit 30)", rc == 30 and len(jobs) == 1)
rc, out, jobs, calls, st, env, fd = slurm("s5", {"jobs": [{"final": "NODE_FAIL", "exit": "0:1", "running_ticks": 1}, {"final": "PREEMPTED", "running_ticks": 1}, {"final": "COMPLETED", "running_ticks": 1}]})
check("slurm: NODE_FAIL and PREEMPTED resubmit unchanged", rc == 0 and len(jobs) == 3 and all(j["args"]["time"] == "01:00:00" for j in jobs.values()))
rc, out, jobs, calls, st, env, fd = slurm("s6", {"jobs": [{"final": "FAILED", "exit": "1:0", "running_ticks": 1}] * 5}, "--retries", "1")
check("slurm: a plain failure is retried --retries times, then failed (exit 20)", rc == 20 and len(jobs) == 2)
rc, out, jobs, calls, st, env, fd = slurm("s6b", {"jobs": [{"final": "FAILED", "exit": "85:0", "running_ticks": 1}, {"final": "COMPLETED", "running_ticks": 1}]}, "--retries", "0")
check("slurm: exit 85 (checkpointed) continues without using a retry", rc == 0 and len(jobs) == 2)
rc, out, jobs, calls, st, env, fd = slurm("s7", {"jobs": [{"final": "CANCELLED", "running_ticks": 1}, {"final": "COMPLETED"}]})
check("slurm: CANCELLED by someone else stops for a human; no resubmission", rc == 30 and len(jobs) == 1 and not any(c.startswith("scancel") for c in calls))
rc, out, jobs, calls, st, env, fd = slurm("s8", {"jobs": [{"final": "TIMEOUT", "running_ticks": 4, "left_seq": ["00:30:00", "00:09:00", "00:05:00", "00:02:00"]}, {"final": "COMPLETED", "running_ticks": 1}]}, "--signal-mode", "scancel", "--signal-margin", "600")
sc = [c for c in calls if c.startswith("scancel")]
check("slurm: --signal-mode scancel sends USR1 once, to the recorded job only, when the remaining time falls under the margin", len(sc) == 1 and "--signal=USR1" in sc[0] and sc[0].endswith("1001"))
# ---- jobs designed to run to the wall and continue (cycle mode), self-resubmitting jobs, --time max
rc, out, jobs, calls, st, env, fd = slurm("c1", {"jobs": [{"final": "TIMEOUT", "elapsed": "01:00:00", "running_ticks": 1}] * 8 + [{"final": "COMPLETED", "running_ticks": 1}]}, "--cycle", ticks=40)
check("cycle: TIMEOUT at the wall is by design; 8 cycles continue at the SAME --time (no growth), beyond the 6-segment default, then done", rc == 0 and len(jobs) == 9 and {j["args"]["time"] for j in jobs.values()} == {"01:00:00"})
rc, out, jobs, calls, st, env, fd = slurm("c2", {"jobs": [{"final": "FAILED", "exit": "85:0", "elapsed": "00:01:00", "running_ticks": 1}] * 9}, "--cycle", ticks=30)
check("cycle: segments that keep ending early (<25% of the limit) without being asked are not cycling: needs_human after 3", rc == 30 and len(jobs) == 3)
os.makedirs(os.path.join(T, "c3", "w"), exist_ok=True); pf = os.path.join(T, "c3", "prog.txt"); open(pf, "w").write("0")
rc, out, jobs, calls, st, env, fd = slurm("c3", {"jobs": [{"final": "TIMEOUT", "elapsed": "01:00:00", "running_ticks": 1}] * 9}, "--cycle", "--progress-file", pf, ticks=30)
check("cycle: cycles with an unchanged --progress-file stop for a human after 2 repeats (3 jobs)", rc == 30 and len(jobs) == 3)
pf4 = os.path.join(T, "c4_prog.txt")
rc, out, jobs, calls, st, env, fd = slurm("c4", {"jobs": [{"final": "TIMEOUT", "elapsed": "01:00:00", "running_ticks": 1, "touch": pf4}] * 5 + [{"final": "COMPLETED", "running_ticks": 1}]}, "--cycle", "--progress-file", pf4, ticks=40)
check("cycle: cycles that keep changing the --progress-file continue until done", rc == 0 and len(jobs) == 6)
ff = os.path.join(T, "c5_next.txt")
st, wd = sdir("c5"); fd = os.path.join(T, "c5_fake"); os.makedirs(fd, exist_ok=True)
json.dump({"jobs": [{"final": "TIMEOUT", "running_ticks": 1, "write": {"path": ff, "text": "2001\n"}}]}, open(os.path.join(fd, "plan.json"), "w"))
json.dump({"id": 2001, "plan": {"final": "COMPLETED", "running_ticks": 1}, "args": {"time": "01:00:00"}, "ticks": 0}, open(os.path.join(fd, "job_2001.json"), "w"))
env = dict(os.environ, PATH=FB + os.pathsep + os.environ["PATH"], FAKE_DIR=fd)
run(["start", "--state", st, "--kind", "slurm", "--cmd", "x", "--workdir", wd, "--time", "01:00:00", "--follow-file", ff], env=env)
for _ in range(10):
    rc, out = run(["tick", "--state", st], env=env)
    if rc != 10: break
ids = [x["id"] for x in json.load(open(os.path.join(st, "state.json")))["segments"]]
nsb = sum(1 for l in open(os.path.join(fd, "calls.log")) if l.startswith("sbatch"))
check("follow: a job that resubmits itself and writes the successor id is followed (no extra sbatch); the chain ends done", rc == 0 and ids[-1] == 2001 and len(ids) == 2 and nsb == 1)
rc, out, jobs, calls, st, env, fd = slurm("c6", {"jobs": [{"final": "COMPLETED", "running_ticks": 1}]}, "--time", "max", ticks=3)
check("--time max resolves the partition limit from sinfo (3 days) for the first submission", rc == 0 and jobs[1001]["args"]["time"].startswith("3-00:00") and any(c.startswith("sinfo") and "-p cpu" in c for c in calls))
rc, out, jobs, calls, st, env, fd = slurm("s9", {"jobs": [{"final": "COMPLETED", "running_ticks": 1}]})
sb = jobs[1001]["args"]["script_text"]
check("slurm: default signal mode puts --signal=B:USR1@600 in the sbatch script", jobs[1001]["args"].get("signal") == "B:USR1@600" and "trap" in sb)
# the generated wrapper itself: forward USR1 to the app, exit 85 after a checkpoint
import re
wd = os.path.join(T, "s9", "w"); os.makedirs(wd, exist_ok=True)
app = os.path.join(wd, "app.sh"); open(app, "w").write("#!/bin/bash\ntrap 'echo ckpt > ckpt.txt; exit 3' USR1\nfor i in $(seq 1 100); do sleep 0.1; done\nexit 0\n"); os.chmod(app, 0o755)
def wrapper(cmd):
    st_ = os.path.join(T, "wrapper_state"); shutil.rmtree(st_, ignore_errors=True); os.makedirs(st_)
    sys.path.insert(0, os.path.dirname(JW)); import importlib; jw = importlib.import_module("jw")
    state = {"tag": "t", "segments": [], "config": {"workdir": wd, "cmd": cmd, "resume_cmd": None, "signal_mode": "sbatch", "signal_margin": 600}}
    return jw.sbatch_script(st_, state, 1, False, 3600, None)
script = wrapper("bash " + app)
body = [l for l in open(script).read().splitlines() if not l.startswith("#SBATCH")]
open(script + ".run", "w").write("\n".join(body) + "\n")
p = subprocess.Popen(["bash", script + ".run"], start_new_session=True); time.sleep(1.0); p.send_signal(signal.SIGUSR1); rcw = p.wait(timeout=20)
check("wrapper: USR1 reaches the app, the app checkpoints and exits nonzero -> wrapper exits 85", rcw == 85 and os.path.exists(os.path.join(wd, "ckpt.txt")))
script2 = wrapper("exit 7"); body = [l for l in open(script2).read().splitlines() if not l.startswith("#SBATCH")]; open(script2 + ".run", "w").write("\n".join(body) + "\n")
check("wrapper: without a signal the app's own exit status is preserved", subprocess.run(["bash", script2 + ".run"]).returncode == 7)
rc, out, jobs, calls, st, env, fd = slurm("s10", {"jobs": [{"final": "COMPLETED", "running_ticks": 1}, {"final": "COMPLETED", "running_ticks": 1}]}, "--expect", "never_there.txt", "--retries", "1")
check("slurm: COMPLETED without the expected output is not done; retried then failed", rc == 20 and len(jobs) == 2)
rc, out, jobs, calls, st, env, fd = slurm("s11", {"jobs": [{"final": "NODE_FAIL", "running_ticks": 1}] * 10}, "--max-attempts", "3")
check("slurm: --max-attempts bounds the segments (3), then needs a human", rc == 30 and len(jobs) == 3)
subm = {str(j) for j in jobs}
ids_used = {c.split("-j ")[1].split()[0] for c in calls if "-j " in c}
check("slurm: every squeue/sacct query names a job id this state submitted (never account-wide)", ids_used <= subm and not any(" -u " in c for c in calls))
rc, out, jobs, calls, st, env, fd = slurm("s12", {"jobs": [{"final": "TIMEOUT", "running_ticks": 1}, {"final": "COMPLETED", "running_ticks": 1}], "sbatch_fail": [1]})
check("slurm: a failed resubmission is retried on the next tick and then succeeds", rc == 0 and len(jobs) == 2)
# attach to a job started elsewhere
st, wd = sdir("s13"); fd = os.path.join(T, "s13_fake"); os.makedirs(fd, exist_ok=True)
json.dump({"preexisting": [777], "jobs": [{"final": "COMPLETED", "running_ticks": 1}]}, open(os.path.join(fd, "plan.json"), "w"))
json.dump({"id": 777, "plan": {"final": "TIMEOUT", "running_ticks": 1}, "args": {"time": "02:00:00"}, "ticks": 0}, open(os.path.join(fd, "job_777.json"), "w"))
env = dict(os.environ, PATH=FB + os.pathsep + os.environ["PATH"], FAKE_DIR=fd)
script = os.path.join(wd, "my.sbatch"); open(script, "w").write("#!/bin/bash\necho hi\n")
run(["start", "--state", st, "--kind", "slurm", "--attach-job", "777", "--script", script, "--workdir", wd], env=env)
for _ in range(8):
    rc, out = run(["tick", "--state", st], env=env)
    if rc != 10: break
newj = [f for f in os.listdir(fd) if f.startswith("job_") and f != "job_777.json"]
j2 = json.load(open(os.path.join(fd, newj[0]))) if newj else {}
check("slurm: attaching to an existing job; its TIMEOUT resubmits the user's script with --time 03:00:00 and JW_RESUME=1", rc == 0 and j2.get("args", {}).get("time") == "03:00:00" and "JW_RESUME=1" in " ".join(j2.get("args", {}).get("argv", [])))
shutil.rmtree(T, ignore_errors=True)
print(f"{len(fails)} failed" if fails else "all job-watch tests passed")
sys.exit(1 if fails else 0)
