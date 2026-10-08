#!/usr/bin/env python3
"""jw.py - job-watch: supervise a long job with no LLM polling it (stdlib only, Python 3.8+).
  jw.py start  --state DIR --kind local|slurm (--cmd CMD | --script FILE | --attach-pid PID | --attach-job ID) [options]
  jw.py tick   --state DIR      one idempotent supervision step
  jw.py watch  --state DIR [--interval 60] [--deadline-min 25]   ticks until the job is terminal or the deadline passes
  jw.py status --state DIR      one JSON object with a report_line
  jw.py stop   --state DIR      cancel ONLY the job/process this state recorded
Exit codes of tick/watch: 0 done | 10 still running (deadline reached, run watch again) | 20 failed | 30 needs a human | 40 stopped | 41 another watcher holds the lock | 2 usage/state error.

Local jobs run as a detached process group; completion is read from an exit-code file, so the watcher can die and restart. A stalled job
(no growth of its log or --progress-file for --stall-min) is killed and retried. SLURM jobs are submitted and tracked with sbatch/squeue/sacct/
scancel against the recorded job ids only, never account-wide. When a segment ends in TIMEOUT it is resubmitted with a longer --time (up to
--max-time); OUT_OF_MEMORY resubmits with more --mem (up to --max-mem); NODE_FAIL/PREEMPTED/BOOT_FAIL resubmit unchanged; a plain failure retries
--retries times; a CANCELLED job (somebody else's decision) stops for a human. Before the limit (--signal-margin seconds) the job gets USR1 so a
restartable program can checkpoint and exit; the generated wrapper then exits 85, which means "checkpointed, continue". The command sees
JW_ATTEMPT (1,2,...), JW_RESUME (0 first time, 1 after), JW_SEGMENT and JW_STATE. Completion is checked with --validate CMD and --expect FILE."""
import argparse, fcntl, json, math, os, re, shlex, signal, subprocess, sys, time

CONTINUE_EXIT = 85
EXIT = {"done": 0, "running": 10, "failed": 20, "needs_human": 30, "stopped": 40}
INFRA = {"NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "REQUEUED", "SPECIAL_EXIT"}

def now(): return int(time.time())

def parse_time(s):
    """Slurm time to seconds: M, M:S, H:M:S, D-H, D-H:M, D-H:M:S."""
    s = str(s).strip()
    if s in ("", "UNLIMITED", "INVALID", "Partition_Limit", "NOT_SET"): return None
    d = 0
    if "-" in s: d, s = s.split("-", 1); d = int(d); parts = [int(x) for x in s.split(":")]; parts += [0] * (3 - len(parts))
    else:
        parts = [int(x) for x in s.split(":")]
        if len(parts) == 1: parts = [0, parts[0], 0]
        elif len(parts) == 2: parts = [0, parts[0], parts[1]]
    h, m, sec = parts[:3]
    return d * 86400 + h * 3600 + m * 60 + sec

def fmt_time(sec):
    sec = int(math.ceil(sec / 60.0) * 60)
    d, r = divmod(sec, 86400); h, r = divmod(r, 3600); m = r // 60
    return f"{d}-{h:02d}:{m:02d}:00" if d else f"{h:02d}:{m:02d}:00"

def parse_mem(s):
    """Memory to MB: 16G, 16000M, 16000 (MB), 1T."""
    if s is None: return None
    m = re.match(r"^\s*([\d.]+)\s*([KMGT]?)B?[nc]?\s*$", str(s), re.I)
    if not m: return None
    v = float(m.group(1)); u = m.group(2).upper() or "M"
    return int(v * {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}[u])

def sh(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout); return p.returncode, p.stdout, p.stderr
    except Exception as e: return 127, "", f"{type(e).__name__}: {e}"

# ---------------------------------------------------------------- state
def load(d):
    return json.load(open(os.path.join(d, "state.json")))

def save(d, st):
    st["updated"] = now(); tmp = os.path.join(d, "state.json.tmp")
    json.dump(st, open(tmp, "w"), indent=1); os.replace(tmp, os.path.join(d, "state.json"))

def note(st, msg): st.setdefault("notes", []).append({"ts": now(), "msg": msg}); st["notes"] = st["notes"][-40:]

def report_line(st):
    seg = st["segments"][-1] if st["segments"] else {}
    c = st["config"]
    bits = [f"job-watch {st['tag']}: {st['status']}", f"segment {len(st['segments'])}/{c['max_attempts']}"]
    if seg.get("id") is not None: bits.append(f"{c['kind']} {seg['id']} {seg.get('state', '?')}")
    if seg.get("elapsed"): bits.append(f"elapsed {seg['elapsed']}")
    if st["status"] == "running" and seg.get("time_s"): bits.append(f"limit {fmt_time(seg['time_s'])}")
    if st.get("notes"): bits.append("last: " + st["notes"][-1]["msg"])
    return "; ".join(bits)

# ---------------------------------------------------------------- local segments
def launch_local(d, st, resume):
    c = st["config"]; n = len(st["segments"]) + 1
    cmd = c["resume_cmd"] if (resume and c.get("resume_cmd")) else c["cmd"]
    logd = os.path.join(d, "log"); os.makedirs(logd, exist_ok=True)
    exitf = os.path.join(logd, f"seg_{n}.exit"); logf = os.path.join(logd, f"seg_{n}.log"); wrap = os.path.join(logd, f"seg_{n}.sh")
    open(wrap, "w").write("#!/bin/bash\ncd %s\nexport JW_ATTEMPT=%d JW_SEGMENT=%d JW_RESUME=%d JW_STATE=%s\nbash -c %s\nrc=$?\necho $rc > %s\nexit $rc\n"
                          % (shlex.quote(c["workdir"]), n, n, 1 if resume else 0, shlex.quote(d), shlex.quote(cmd), shlex.quote(exitf)))
    out = open(logf, "ab")
    p = subprocess.Popen(["bash", wrap], stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    seg = {"n": n, "id": p.pid, "state": "RUNNING", "started": now(), "log": logf, "exit_file": exitf, "resume": bool(resume)}
    st["segments"].append(seg); note(st, f"launched local segment {n} (pid {p.pid})")
    return seg

def pid_alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f: return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except Exception:
        try: os.kill(pid, 0); return True
        except OSError: return False

def kill_group(pid):
    for sig, wait in ((signal.SIGTERM, 8), (signal.SIGKILL, 2)):
        try: os.killpg(pid, sig)
        except OSError: return
        t = time.time()
        while time.time() - t < wait:
            if not pid_alive(pid): return
            time.sleep(0.3)

def newest_progress(st, seg):
    c = st["config"]; m = 0
    for f in [seg["log"]] + c.get("progress_files", []):
        try: m = max(m, os.path.getmtime(f if os.path.isabs(f) else os.path.join(c["workdir"], f)))
        except OSError: pass
    return m

def tick_local(d, st):
    seg = st["segments"][-1]; c = st["config"]
    try: os.waitpid(seg["id"], os.WNOHANG)
    except (ChildProcessError, OSError): pass
    code = None
    if os.path.exists(seg["exit_file"]):
        try: code = int(open(seg["exit_file"]).read().strip())
        except ValueError: code = None
    alive = pid_alive(seg["id"]) if code is None else False
    if code is None and alive:
        seg["elapsed"] = f"{(now() - seg['started']) // 60}m"
        stall = c.get("stall_min")
        if stall and time.time() - max(newest_progress(st, seg), seg["started"]) > stall * 60:
            kill_group(seg["id"]); seg["state"] = "STALLED"; seg["exit"] = "stalled"
            note(st, f"segment {seg['n']} stalled for {stall} min; killed"); return after_local(d, st, seg, "stalled")
        return "running"
    if code is None and seg.get("attached"): code = 0           # attached pid: exit status unknown, validation decides
    if code is None: code = 137                                # process vanished without writing an exit code
    seg["exit"] = code; seg["state"] = "COMPLETED" if code == 0 else ("KILLED" if code in (137, 143) else "FAILED")
    return after_local(d, st, seg, code)

def after_local(d, st, seg, code):
    c = st["config"]; seg["ended"] = now(); seg["elapsed"] = f"{(seg['ended'] - seg['started']) // 60}m"
    if code == 0:
        ok, why = validate(st)
        if ok: return finish(d, st, "done", f"segment {seg['n']} completed and validated")
        seg["validation"] = why
        if st["config"].get("retries", 0) > st["retries_used"] and len(st["segments"]) < c["max_attempts"]:
            st["retries_used"] += 1; note(st, f"validation failed ({why}); retrying"); launch_local(d, st, True); return "running"
        return finish(d, st, "failed", f"completed but validation failed: {why}")
    if code == CONTINUE_EXIT and len(st["segments"]) < c["max_attempts"]:
        note(st, f"segment {seg['n']} checkpointed (exit {CONTINUE_EXIT}); continuing"); launch_local(d, st, True); return "running"
    retryable = code in (137, 143, "stalled") or c.get("retries", 0) > st["retries_used"]
    if retryable and len(st["segments"]) < c["max_attempts"]:
        if code not in (137, 143, "stalled"): st["retries_used"] += 1
        note(st, f"segment {seg['n']} ended with {code}; retrying"); launch_local(d, st, True); return "running"
    return finish(d, st, "failed", f"segment {seg['n']} ended with {code}; no attempts left" if len(st["segments"]) >= c["max_attempts"] else f"segment {seg['n']} ended with {code}")

# ---------------------------------------------------------------- slurm segments
def sbatch_script(d, st, n, resume, time_s, mem_mb):
    c = st["config"]; logd = os.path.join(d, "log"); os.makedirs(logd, exist_ok=True)
    path = os.path.join(logd, f"seg_{n}.sbatch")
    lines = ["#!/bin/bash", f"#SBATCH --job-name=jw-{st['tag']}"[:60], f"#SBATCH --time={fmt_time(time_s)}"]
    if mem_mb: lines.append(f"#SBATCH --mem={mem_mb}M")
    lines += [f"#SBATCH --output={os.path.join(logd, 'seg_%d.out' % n)}", f"#SBATCH --open-mode=append"]
    if c["signal_mode"] == "sbatch" and c["signal_margin"]: lines.append(f"#SBATCH --signal=B:USR1@{int(c['signal_margin'])}")
    lines += [f"cd {shlex.quote(c['workdir'])}", f"export JW_ATTEMPT={n} JW_SEGMENT={n} JW_RESUME={1 if resume else 0} JW_STATE={shlex.quote(d)}"]
    cmd = c["resume_cmd"] if (resume and c.get("resume_cmd")) else c["cmd"]
    lines += ["bash -c %s &" % shlex.quote(cmd), "APP=$!", "CP=0", "trap 'CP=1; kill -USR1 $APP 2>/dev/null' USR1",
              "while true; do wait $APP; RC=$?; kill -0 $APP 2>/dev/null || break; done",
              f"if [ $CP -eq 1 ] && [ $RC -ne 0 ]; then exit {CONTINUE_EXIT}; fi", "exit $RC"]
    open(path, "w").write("\n".join(lines) + "\n"); return path

def submit_slurm(d, st, resume, time_s=None, mem_mb=None):
    c = st["config"]; n = len(st["segments"]) + 1
    time_s = time_s or (st["segments"][-1]["time_s"] if st["segments"] else c["time_s"]); mem_mb = mem_mb or (st["segments"][-1].get("mem_mb") if st["segments"] else c.get("mem_mb"))
    if c.get("script"):
        script = c["script"]; extra = ["--time", fmt_time(time_s), f"--job-name=jw-{st['tag']}"[:60], f"--export=ALL,JW_ATTEMPT={n},JW_SEGMENT={n},JW_RESUME={1 if resume else 0},JW_STATE={d}"]
        if mem_mb: extra += ["--mem", f"{mem_mb}M"]
        if c["signal_mode"] == "sbatch" and c["signal_margin"]: extra += [f"--signal=B:USR1@{int(c['signal_margin'])}"]
    else:
        script = sbatch_script(d, st, n, resume, time_s, mem_mb); extra = []
    cmd = ["sbatch", "--parsable"] + shlex.split(c.get("sbatch_args", "")) + extra + [script]
    rc, out, err = sh(cmd)
    m = re.match(r"\s*(\d+)", out or "")
    if rc != 0 or not m:
        note(st, f"sbatch failed: {(err or out).strip()[:200]}"); return None
    seg = {"n": n, "id": int(m.group(1)), "state": "SUBMITTED", "submitted": now(), "time_s": time_s, "mem_mb": mem_mb, "resume": bool(resume), "signalled": False, "sacct_miss": 0}
    st["segments"].append(seg); note(st, f"submitted slurm segment {n} as job {seg['id']} (--time {fmt_time(time_s)}" + (f", --mem {mem_mb}M" if mem_mb else "") + ")")
    return seg

def partition_max(sbatch_args):
    m = re.search(r"(?:--partition[= ]|-p[ =]?)(\S+)", sbatch_args or "")
    if not m: return None
    rc, out, err = sh(["sinfo", "-h", "-p", m.group(1).split(",")[0], "-o", "%l"])
    return parse_time(out.split()[0]) if rc == 0 and out.split() else None

def progress_sig(c):
    import glob
    sig = []
    for pat in c.get("progress_files") or []:
        for f in sorted(glob.glob(pat if os.path.isabs(pat) else os.path.join(c["workdir"], pat))):
            try: stt = os.stat(f); sig.append([f, stt.st_mtime_ns, stt.st_size])
            except OSError: pass
    return sig

def cycle_guard(st, seg):
    """Cycle mode: ending at the limit or after a checkpoint request is by design. Stop for a human only when cycles stop making progress."""
    c = st["config"]
    if not c.get("cycle"): return None
    tl = seg.get("time_s") or c.get("time_s") or 0; el = parse_time(seg.get("elapsed") or "") or 0
    expected = seg.get("state") == "TIMEOUT" or seg.get("signalled")
    if tl and el < 0.25 * tl and not expected: st["short_cycles"] = st.get("short_cycles", 0) + 1
    else: st["short_cycles"] = 0
    if st["short_cycles"] >= 3: return f"3 consecutive segments ended in under 25% of their time limit without being asked to stop; the program is exiting early, not cycling"
    if c.get("progress_files"):
        sig = progress_sig(c)
        if st.get("last_progress") is not None and sig == st["last_progress"]: st["noprog"] = st.get("noprog", 0) + 1
        else: st["noprog"] = 0
        st["last_progress"] = sig
        if st["noprog"] >= 2: return "2 consecutive cycles without any change to --progress-file; not resubmitting a job that makes no progress"
    return None

def follow_next(st):
    f = st["config"].get("follow_file")
    if not f: return None
    p = f if os.path.isabs(f) else os.path.join(st["config"]["workdir"], f)
    try: m = re.search(r"\d+", open(p).read())
    except OSError: return None
    if not m: return None
    jid = int(m.group(0))
    return jid if jid not in [x["id"] for x in st["segments"]] else None

def try_follow(d, st, seg, left):
    """The program resubmits itself and writes the successor's job id to --follow-file: track that job instead of submitting another."""
    nxt = follow_next(st)
    if not nxt or not left: return None
    g = cycle_guard(st, seg)
    if g: return finish(d, st, "needs_human", g)
    q = squeue_row(nxt)
    st["segments"].append({"n": len(st["segments"]) + 1, "id": nxt, "state": (q or {}).get("state", "PENDING"), "submitted": now(), "time_s": (q or {}).get("limit"), "mem_mb": seg.get("mem_mb"), "resume": True, "signalled": False, "sacct_miss": 0, "followed": True})
    note(st, f"job {seg['id']} ended {seg['state']}; following its successor job {nxt} (declared in the follow file)"); return "running"

def squeue_row(jid):
    rc, out, err = sh(["squeue", "-h", "-j", str(jid), "-o", "%T|%M|%l|%L"])
    out = out.strip()
    if rc != 0 or not out: return None
    p = out.splitlines()[0].split("|")
    return {"state": p[0], "elapsed": p[1], "limit": parse_time(p[2]), "left": parse_time(p[3]) if len(p) > 3 else None}

def sacct_row(jid):
    rc, out, err = sh(["sacct", "-n", "-P", "-j", str(jid), "--format=JobID,State,ExitCode,Elapsed,MaxRSS"])
    rows = [l.split("|") for l in out.splitlines() if l.strip()]
    if rc != 0 or not rows: return None
    main = next((r for r in rows if r[0] == str(jid)), rows[0]); rss = 0
    for r in rows:
        v = r[4] if len(r) > 4 else ""
        m = re.match(r"^([\d.]+)([KMG]?)", v or "")
        if m: rss = max(rss, float(m.group(1)) * {"": 1 / 1024, "K": 1 / 1024, "M": 1, "G": 1024}[m.group(2)])
    return {"state": main[1].split()[0].rstrip("+"), "exit": main[2], "elapsed": main[3], "maxrss_mb": int(rss)}

def tick_slurm(d, st):
    c = st["config"]; seg = st["segments"][-1]; jid = seg["id"]
    q = squeue_row(jid)
    if q:
        seg["state"] = q["state"]; seg["elapsed"] = q["elapsed"]
        if q["limit"]: seg["time_s"] = q["limit"]
        if q["state"] == "RUNNING" and c["signal_mode"] == "scancel" and c["signal_margin"] and not seg["signalled"] and q["left"] is not None and q["left"] <= c["signal_margin"]:
            rc, _, err = sh(["scancel", "--signal=USR1", "--batch", str(jid)]); seg["signalled"] = True
            note(st, f"sent USR1 to job {jid} ({q['left']}s left) so it can checkpoint")
        if q["state"] == "PENDING" and c.get("max_pending_h") and now() - seg.get("submitted", now()) > c["max_pending_h"] * 3600 and not seg.get("pending_noted"):
            seg["pending_noted"] = True; note(st, f"job {jid} pending for more than {c['max_pending_h']} h")
        return "running"
    a = sacct_row(jid)
    if not a:
        seg["sacct_miss"] += 1
        if seg["sacct_miss"] >= 6: return finish(d, st, "needs_human", f"job {jid} is in neither squeue nor sacct")
        return "running"
    seg.update({"state": a["state"], "exit": a["exit"], "elapsed": a["elapsed"], "maxrss_mb": a["maxrss_mb"], "ended": now()})
    code = a["exit"].split(":")[0]; s = a["state"]
    if s in ("PENDING", "RUNNING", "COMPLETING", "SUSPENDED"): return "running"
    left = len(st["segments"]) < c["max_attempts"]
    if s == "COMPLETED":
        ok, why = validate(st)
        if ok: return finish(d, st, "done", f"job {jid} completed and validated")
        seg["validation"] = why
        r = try_follow(d, st, seg, left)
        if r: return r
        if seg.get("signalled") and left:
            g = cycle_guard(st, seg)
            if g: return finish(d, st, "needs_human", g)
            note(st, "completed after a checkpoint signal but not complete; continuing"); return resubmit(d, st, True)
        if left and c.get("retries", 0) > st["retries_used"]:
            st["retries_used"] += 1; note(st, f"job {jid} completed but validation failed ({why}); retry {st['retries_used']}/{c['retries']}"); return resubmit(d, st, True)
        return finish(d, st, "failed", f"job {jid} completed but validation failed: {why}")
    if s == "TIMEOUT" or (s == "FAILED" and code == str(CONTINUE_EXIT)):
        r = try_follow(d, st, seg, left)
        if r: return r
    if s == "CANCELLED": return finish(d, st, "needs_human", f"job {jid} was cancelled by someone else; not resubmitting")
    if not left: return finish(d, st, "needs_human", f"job {jid} ended {s}; {c['max_attempts']} segments used")
    if s == "TIMEOUT" and c.get("cycle"):
        g = cycle_guard(st, seg)
        if g: return finish(d, st, "needs_human", g)
        note(st, f"job {jid} reached its time limit as designed (cycle {len(st['segments'])}); continuing from checkpoint at the same --time"); return resubmit(d, st, True)
    if s == "TIMEOUT":
        cur = seg["time_s"] or c["time_s"]
        if not c.get("max_time_s"): c["max_time_s"] = (c.get("time_s") or cur) * 4      # default cap: 4x the first --time
        mx = c["max_time_s"]
        if cur < mx:
            new = min(mx, cur * c["time_factor"]); note(st, f"job {jid} hit its time limit; resubmitting with --time {fmt_time(new)}"); return resubmit(d, st, True, time_s=new)
        if c.get("restartable"): note(st, f"job {jid} hit its time limit at the maximum; continuing from checkpoint"); return resubmit(d, st, True)
        return finish(d, st, "needs_human", f"job {jid} hit its time limit at --max-time {fmt_time(mx)} and the command is not marked --restartable")
    if s == "OUT_OF_MEMORY":
        cur = seg.get("mem_mb") or (a["maxrss_mb"] or 0)
        if not cur: return finish(d, st, "needs_human", f"job {jid} ran out of memory and no --mem is known; set --mem")
        new = int(cur * c["mem_factor"]); 
        if not c.get("max_mem_mb"): c["max_mem_mb"] = (c.get("mem_mb") or cur) * 4         # default cap: 4x the first --mem
        mxm = c["max_mem_mb"]
        if new > mxm: return finish(d, st, "needs_human", f"job {jid} ran out of memory at {cur}M; next step {new}M exceeds --max-mem {mxm}M")
        note(st, f"job {jid} ran out of memory ({cur}M); resubmitting with --mem {new}M"); return resubmit(d, st, True, mem_mb=new)
    if s in INFRA: note(st, f"job {jid} ended {s}; resubmitting unchanged"); return resubmit(d, st, True)
    if s == "FAILED" and code == str(CONTINUE_EXIT):
        g = cycle_guard(st, seg)
        if g: return finish(d, st, "needs_human", g)
        note(st, f"job {jid} checkpointed (exit {CONTINUE_EXIT}); continuing"); return resubmit(d, st, True)
    if s == "FAILED" and c.get("retries", 0) > st["retries_used"]:
        st["retries_used"] += 1; note(st, f"job {jid} failed (exit {a['exit']}); retry {st['retries_used']}/{c['retries']}"); return resubmit(d, st, True)
    return finish(d, st, "failed", f"job {jid} ended {s} (exit {a['exit']})")

def resubmit(d, st, resume, time_s=None, mem_mb=None):
    seg = submit_slurm(d, st, resume, time_s, mem_mb)
    if not seg:
        st["submit_failures"] = st.get("submit_failures", 0) + 1
        if st["submit_failures"] >= 5: return finish(d, st, "needs_human", "sbatch kept failing: " + st["notes"][-1]["msg"])
        st["resubmit_pending"] = {"resume": resume, "time_s": time_s, "mem_mb": mem_mb}; return "running"
    st["submit_failures"] = 0; st.pop("resubmit_pending", None); return "running"

# ---------------------------------------------------------------- shared
def validate(st):
    c = st["config"]; wd = c["workdir"]
    for f in c.get("expect", []):
        p = f if os.path.isabs(f) else os.path.join(wd, f)
        if not os.path.exists(p) or (os.path.isfile(p) and os.path.getsize(p) == 0): return False, f"expected output missing or empty: {f}"
    if c.get("validate"):
        rc, out, err = sh(["bash", "-c", f"cd {shlex.quote(wd)} && {c['validate']}"], timeout=600)
        if rc != 0: return False, f"validate command exited {rc}: {(out + err).strip()[-200:]}"
    return True, "ok"

def finish(d, st, status, msg):
    st["status"] = status; note(st, msg); st["finished"] = now(); st["report_line"] = report_line(st)
    json.dump({"status": status, "message": msg, "segments": st["segments"], "tag": st["tag"], "report_line": st["report_line"], "finished": st["finished"]}, open(os.path.join(d, "result.json"), "w"), indent=1)
    return status

def tick(d):
    st = load(d)
    if st["status"] != "running": return st["status"]
    if st.get("resubmit_pending"):
        r = st["resubmit_pending"]; res = resubmit(d, st, r["resume"], r["time_s"], r["mem_mb"])
    elif st["config"]["kind"] == "local": res = tick_local(d, st)
    else: res = tick_slurm(d, st)
    st["last_tick"] = now(); st["report_line"] = report_line(st); save(d, st); return st["status"]

def lock(d):
    f = open(os.path.join(d, "jw.lock"), "w")
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); return f
    except OSError: return None

# ---------------------------------------------------------------- cli
def cmd_start(a):
    d = os.path.abspath(a.state); os.makedirs(d, exist_ok=True)
    if os.path.exists(os.path.join(d, "state.json")): print(json.dumps({"error": "state exists; use a new --state"})); return 2
    if a.kind == "slurm" and not (a.cmd or a.script or a.attach_job): print(json.dumps({"error": "slurm needs --cmd, --script or --attach-job"})); return 2
    if a.kind == "local" and not (a.cmd or a.attach_pid): print(json.dumps({"error": "local needs --cmd or --attach-pid"})); return 2
    if a.time == "max":
        t = partition_max(a.sbatch_args)
        if not t: print(json.dumps({"error": "--time max needs --sbatch-args with --partition and a finite partition limit from sinfo"})); return 2
    else: t = parse_time(a.time) if a.time else None
    if a.cycle and a.kind != "slurm": print(json.dumps({"error": "--cycle is for slurm jobs"})); return 2
    cfg = {"kind": a.kind, "cmd": a.cmd, "script": os.path.abspath(a.script) if a.script else None, "resume_cmd": a.resume_cmd, "workdir": os.path.abspath(a.workdir),
           "validate": a.validate, "expect": a.expect, "max_attempts": a.max_attempts or (a.max_cycles if a.cycle else 6), "cycle": a.cycle, "follow_file": a.follow_file, "retries": a.retries, "restartable": a.restartable,
           "stall_min": a.stall_min, "progress_files": a.progress_file, "sbatch_args": a.sbatch_args, "time_s": t, "time_factor": a.time_factor,
           "max_time_s": parse_time(a.max_time) if a.max_time else ((t if a.cycle else t * 4) if t else None), "mem_mb": parse_mem(a.mem), "mem_factor": a.mem_factor,
           "max_mem_mb": parse_mem(a.max_mem) if a.max_mem else (parse_mem(a.mem) * 4 if a.mem else None), "signal_margin": a.signal_margin, "signal_mode": a.signal_mode, "max_pending_h": a.max_pending_hours}
    st = {"tag": a.tag or os.path.basename(d.rstrip("/")), "status": "running", "created": now(), "config": cfg, "segments": [], "retries_used": 0, "notes": []}
    os.makedirs(cfg["workdir"], exist_ok=True)
    if a.kind == "local":
        if a.attach_pid:
            st["segments"].append({"n": 1, "id": a.attach_pid, "state": "RUNNING", "started": now(), "log": a.log or os.devnull, "exit_file": a.exit_file or os.path.join(d, "attached.exit"), "resume": False, "attached": True})
            note(st, f"attached to pid {a.attach_pid}")
        else: launch_local(d, st, False)
    else:
        if a.attach_job:
            q = squeue_row(a.attach_job)
            st["segments"].append({"n": 1, "id": int(a.attach_job), "state": (q or {}).get("state", "RUNNING"), "submitted": now(), "time_s": (q or {}).get("limit") or t, "mem_mb": parse_mem(a.mem), "signalled": False, "sacct_miss": 0, "resume": False})
            cfg["time_s"] = cfg["time_s"] or st["segments"][0]["time_s"]
            if cfg["time_s"] and not a.max_time: cfg["max_time_s"] = cfg["time_s"] * 4
            note(st, f"attached to slurm job {a.attach_job}")
        else:
            if not t: print(json.dumps({"error": "--time is required for slurm"})); return 2
            if not submit_slurm(d, st, False): print(json.dumps({"error": "sbatch failed", "detail": st["notes"][-1]["msg"]})); return 2
    st["report_line"] = report_line(st); save(d, st); print(json.dumps({"state": d, "report_line": st["report_line"]})); return 0

def cmd_tick(a):
    d = os.path.abspath(a.state)
    if not os.path.exists(os.path.join(d, "state.json")): print(json.dumps({"error": "no state"})); return 2
    lk = lock(d)
    if not lk: print(json.dumps({"status": "busy"})); return 41
    s = tick(d); print(json.dumps({"status": s, "report_line": load(d)["report_line"]})); return EXIT[s]

def cmd_watch(a):
    d = os.path.abspath(a.state)
    if not os.path.exists(os.path.join(d, "state.json")): print(json.dumps({"error": "no state"})); return 2
    lk = lock(d)
    if not lk: print(json.dumps({"status": "busy"})); return 41
    end = time.time() + a.deadline_min * 60
    while True:
        s = tick(d)
        if s != "running": print(json.dumps({"status": s, "report_line": load(d)["report_line"]})); return EXIT[s]
        if time.time() + a.interval > end: break
        time.sleep(a.interval)
    print(json.dumps({"status": "running", "report_line": load(d)["report_line"], "note": "deadline reached; run watch again"})); return EXIT["running"]

def cmd_status(a):
    d = os.path.abspath(a.state)
    if not os.path.exists(os.path.join(d, "state.json")): print(json.dumps({"error": "no state"})); return 2
    st = load(d); seg = st["segments"][-1] if st["segments"] else {}
    print(json.dumps({"status": st["status"], "report_line": report_line(st), "segments": st["segments"], "retries_used": st["retries_used"], "notes": st.get("notes", [])[-8:]})); return EXIT.get(st["status"], 0)

def cmd_stop(a):
    d = os.path.abspath(a.state); st = load(d)
    if st["status"] != "running": print(json.dumps({"status": st["status"]})); return EXIT[st["status"]]
    seg = st["segments"][-1]
    if st["config"]["kind"] == "slurm": sh(["scancel", str(seg["id"])])
    else: kill_group(seg["id"])
    finish(d, st, "stopped", f"stopped on request; only {st['config']['kind']} id {seg['id']} was touched"); save(d, st); print(json.dumps({"status": "stopped"})); return 40

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="sub", required=True)
    s = sp.add_parser("start"); s.add_argument("--state", required=True); s.add_argument("--kind", required=True, choices=["local", "slurm"]); s.add_argument("--tag")
    s.add_argument("--cmd"); s.add_argument("--resume-cmd"); s.add_argument("--script"); s.add_argument("--attach-pid", type=int); s.add_argument("--attach-job"); s.add_argument("--log"); s.add_argument("--exit-file")
    s.add_argument("--workdir", default="."); s.add_argument("--validate"); s.add_argument("--expect", action="append", default=[])
    s.add_argument("--max-attempts", type=int, default=None); s.add_argument("--cycle", action="store_true"); s.add_argument("--max-cycles", type=int, default=500); s.add_argument("--follow-file"); s.add_argument("--retries", type=int, default=1); s.add_argument("--restartable", action="store_true")
    s.add_argument("--stall-min", type=float); s.add_argument("--progress-file", action="append", default=[])
    s.add_argument("--sbatch-args", default=""); s.add_argument("--time"); s.add_argument("--time-factor", type=float, default=1.5); s.add_argument("--max-time")
    s.add_argument("--mem"); s.add_argument("--mem-factor", type=float, default=1.5); s.add_argument("--max-mem")
    s.add_argument("--signal-margin", type=int, default=600); s.add_argument("--signal-mode", default="sbatch", choices=["sbatch", "scancel", "none"]); s.add_argument("--max-pending-hours", type=float, default=24)
    for name in ("tick", "status", "stop"): p = sp.add_parser(name); p.add_argument("--state", required=True)
    w = sp.add_parser("watch"); w.add_argument("--state", required=True); w.add_argument("--interval", type=float, default=60); w.add_argument("--deadline-min", type=float, default=25)
    a = ap.parse_args()
    return {"start": cmd_start, "tick": cmd_tick, "watch": cmd_watch, "status": cmd_status, "stop": cmd_stop}[a.sub](a)

if __name__ == "__main__":
    sys.exit(main())
