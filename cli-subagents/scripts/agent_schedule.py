#!/usr/bin/env python3
"""agent_schedule.py add NAME --cron "M H D Mo W" --spec FILE | list | remove NAME | run NAME --spec FILE   scheduled headless agent_task.py jobs (cron).
`add` writes ONE crontab line tagged `# cli-subagents:NAME` (replacing an earlier line with the same tag); `remove`/`list` touch only tagged lines. cron's own entries are never rewritten.
`run` is what cron calls: per-job lock (a run still going => this one is skipped and logged as SKIPPED_LOCKED), PATH/HOME set for cron, then agent_task.py.
Outputs: $CLI_SUBAGENTS_INBOX/NAME/<UTC ts>/ (default ~/agent-inbox; agent_task's result.json etc., task.md, summary.md) and one line in index.jsonl (see agent_inbox.py).
Spec (JSON): engine, task (file), workdir  (required); agent, access (default read-only), allowed_tools, permission_mode, add_dirs [], model, effort, timeout, schema, in_place.
 Task file tokens: {since} (UTC time of the last DECLARED/NO_CHANGES run of this job, or "never"), {now}, {file:PATH} (contents of PATH), {changed_files_path} and {changed_count}.
 watch_dirs [DIR...] (optional): `run` lists files under them modified since the last such run (all files on the first run) into <run dir>/changed_files.txt, makes the run dir readable
 for the agent, and records NO_CHANGES without calling the agent when the list is empty. Hidden folders are skipped.
Set CLI_SUBAGENTS_CRONTAB=FILE to use a file instead of the real crontab (tests). cron must be running; the cron daemon uses /bin/sh, so `%` is avoided in the line."""
import argparse, datetime, fcntl, json, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import agent_inbox

TAG = "# cli-subagents:"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
KEYS = {"engine", "task", "workdir", "agent", "access", "allowed_tools", "permission_mode", "add_dirs", "model", "effort", "timeout", "schema", "in_place", "watch_dirs"}
P = lambda p: os.path.abspath(os.path.expanduser(p))

def read_cron():
    f = os.environ.get("CLI_SUBAGENTS_CRONTAB")
    if f: return open(f).read() if os.path.exists(f) else ""
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""  # "no crontab for user" = empty

def write_cron(text):
    f = os.environ.get("CLI_SUBAGENTS_CRONTAB")
    if f: open(f, "w").write(text)
    else: subprocess.run(["crontab", "-"], input=text, text=True, check=True)

def tagged(line):
    m = re.search(re.escape(TAG) + r"([A-Za-z0-9_-]+)$", line.rstrip()); return m and m.group(1)

def check_spec(spec):
    bad = set(spec) - KEYS; miss = {"engine", "task", "workdir"} - set(spec)
    if bad or miss or spec["engine"] not in ("codex", "claude"): raise ValueError(f"spec: unknown keys {sorted(bad)}, missing {sorted(miss)}, engine must be codex|claude")
    if not os.path.isfile(P(spec["task"])): raise ValueError("spec: task file missing: " + spec["task"])

def cmd_add(a):
    if not NAME_RE.match(a.name): raise ValueError("NAME must match [A-Za-z0-9_-]+")
    if len(a.cron.split()) != 5 or "%" in a.cron: raise ValueError("--cron needs exactly 5 fields and no %")
    check_spec(json.load(open(a.spec)))
    line = f"{a.cron} {sys.executable} {os.path.join(HERE, 'agent_schedule.py')} run {a.name} --spec {P(a.spec)} >> {os.path.join(agent_inbox.inbox(), a.name + '.cron.log')} 2>&1 {TAG}{a.name}"
    os.makedirs(agent_inbox.inbox(), exist_ok=True)
    keep = [l for l in read_cron().splitlines() if tagged(l) != a.name]
    write_cron("\n".join(keep + [line]) + "\n"); print(line)

def cmd_list(a):
    for l in read_cron().splitlines():
        if tagged(l): print(json.dumps({"name": tagged(l), "line": l}))

def cmd_remove(a):
    lines = read_cron().splitlines(); keep = [l for l in lines if tagged(l) != a.name]
    if len(keep) == len(lines): print(json.dumps({"error": "no job " + a.name})); return 1
    write_cron("\n".join(keep) + "\n" if keep else ""); print(json.dumps({"removed": a.name})); return 0

def changed_files(dirs, since_ts):
    out = []
    for d in dirs:
        for root, subs, files in os.walk(P(d)):
            subs[:] = [s for s in subs if not s.startswith(".")]
            out += [p for p in (os.path.join(root, f) for f in files if not f.startswith(".")) if os.path.getmtime(p) > since_ts]
    return sorted(out)

def cmd_run(a):
    spec = json.load(open(a.spec)); check_spec(spec)
    home = os.path.expanduser("~"); os.environ["HOME"] = home
    os.environ["PATH"] = os.pathsep.join([os.path.join(home, ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin", os.environ.get("PATH", "")])
    now = datetime.datetime.now(datetime.timezone.utc); ts = now.strftime("%Y%m%dT%H%M%S%fZ")  # microseconds keep ids unique
    base = os.path.join(agent_inbox.inbox(), a.name); out = os.path.join(base, ts); os.makedirs(out, exist_ok=True)
    entry = {"id": f"{a.name}/{ts}", "name": a.name, "time": now.isoformat(timespec="seconds"), "status": None, "exit_code": None, "result": None, "summary": None, "usage": None, "read": False}
    lk = open(os.path.join(base, ".lock"), "w")
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        entry.update(status="SKIPPED_LOCKED", exit_code=0); agent_inbox.append(entry); print(json.dumps(entry)); return 0
    prev = [r for r in agent_inbox.read() if r["name"] == a.name and r["status"] in ("DECLARED", "NO_CHANGES")]
    since = prev[-1]["time"] if prev else "never"
    since_ts = datetime.datetime.fromisoformat(since).timestamp() if prev else 0
    add_dirs = list(spec.get("add_dirs", []))
    tok = {"since": since, "now": now.isoformat(timespec="seconds"), "changed_count": "", "changed_files_path": ""}
    if "watch_dirs" in spec:
        files = changed_files(spec["watch_dirs"], since_ts); cf = os.path.join(out, "changed_files.txt")
        open(cf, "w").write("".join(f + "\n" for f in files)); tok.update(changed_count=str(len(files)), changed_files_path=cf); add_dirs += [out] + list(spec["watch_dirs"])
        if not files:
            entry.update(status="NO_CHANGES", exit_code=0, read=True); agent_inbox.append(entry); print(json.dumps(entry)); return 0
    text = open(P(spec["task"])).read()
    text = re.sub(r"\{file:([^}]+)\}", lambda m: open(P(m.group(1))).read(), text)
    text = re.sub(r"\{(since|now|changed_count|changed_files_path)\}", lambda m: tok[m.group(1)], text)
    task = os.path.join(out, "task.md"); open(task, "w").write(text)
    wd = P(spec["workdir"]); in_place = spec.get("in_place", False)
    if in_place: os.makedirs(wd, exist_ok=True)
    cmd = [sys.executable, os.path.join(HERE, "agent_task.py"), task, "--engine", spec["engine"], "--workdir", wd, "--out", out, "--access", spec.get("access", "read-only")]
    for flag, key in (("--agent", "agent"), ("--allowed-tools", "allowed_tools"), ("--permission-mode", "permission_mode"), ("--model", "model"), ("--effort", "effort"), ("--timeout", "timeout"), ("--schema", "schema")):
        if spec.get(key) is not None: cmd += [flag, str(spec[key])]
    for d in add_dirs: cmd += ["--add-dir", d]
    if in_place: cmd.append("--in-place")
    p = subprocess.run(cmd, capture_output=True, text=True)
    rj = os.path.join(out, "result.json"); res = json.load(open(rj)) if os.path.exists(rj) else {}
    entry.update(status=res.get("status") or "FAILED", exit_code=p.returncode, result=rj if res else None, usage=res.get("usage_report"))
    if not res: open(os.path.join(out, "error.txt"), "w").write((p.stdout + p.stderr)[-2000:])
    dec = res.get("declaration")
    if dec:
        r = dec["result"]; sm = os.path.join(out, "summary.md")
        open(sm, "w").write((r if isinstance(r, str) else json.dumps(r, indent=1)) + "\n\nDeviations: " + "; ".join(dec["deviations"]) + "\n"); entry["summary"] = sm
    agent_inbox.append(entry); print(json.dumps(entry)); return p.returncode

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("add"); s.add_argument("name"); s.add_argument("--cron", required=True); s.add_argument("--spec", required=True)
    sp.add_parser("list"); sp.add_parser("remove").add_argument("name")
    s = sp.add_parser("run"); s.add_argument("name"); s.add_argument("--spec", required=True)
    a = ap.parse_args()
    try: return {"add": cmd_add, "list": cmd_list, "remove": cmd_remove, "run": cmd_run}[a.cmd](a) or 0
    except (ValueError, OSError, json.JSONDecodeError) as e: print(json.dumps({"error": str(e)})); return 2

if __name__ == "__main__":
    sys.exit(main())
