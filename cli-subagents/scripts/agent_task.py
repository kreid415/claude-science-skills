#!/usr/bin/env python3
"""agent_task.py TASK.md --engine {codex,claude} --workdir DIR [options]   general task mode: one CLI agent as a stand-in sub-agent.
  --access read-only|workspace-write (default workspace-write)   codex: -s sandbox; claude: tool allow-list (see agent_run.CLAUDE_TOOLS)
  --model M --effort E   codex: -m / -c model_reasoning_effort; claude: --model / --effort (low|medium|high|xhigh|max)
  --allowed-tools CSV --permission-mode dontAsk|acceptEdits|plan   claude only; overrides the default allow-list
  --schema FILE   JSON schema for the declaration's `result`; sent via the CLI's native option (codex --output-schema, claude --json-schema)
  --resume SESSION_ID   follow-up message in an earlier session (same workdir; implies --in-place)
  --out DIR (default ~/cli-subagents/runs/<ts>_<engine>_<pid>)  --timeout S (3600)  --in-place  --max-mb 500
Workdir: a clean git repo is used in place; anything else (plain directory, dirty repo) is first copied to <out>/work, baselined with a git
commit, so the real tree is never edited and the diff is recorded. Pass --in-place to skip the copy.
Every task gets a required closing declaration appended; it is parsed and validated, else status UNDECLARED.
Exit codes: 0 declared | 10 gated (codex usage) | 11 exec failed | 12 usage limit hit mid-run | 13 UNDECLARED | 2 bad input.
Writes <out>/result.json, events.jsonl, last_message.md, task_sent.md, diff.patch, run.json and one ledger entry (mode=task)."""
import argparse, datetime, hashlib, json, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import agent_run, agent_ledger
from agent_loop import copy_tree, size_mb

DECL = """

## Required closing declaration
{where}a JSON object with exactly these keys:
- "result": {what}
- "deviations": a list of strings, each naming something you did less than or other than the task asked; use ["none"] only if you did everything asked, in full.
- "files_changed": a list of file paths (relative to the working directory) you created, modified or deleted; [] if none.
Do not write anything after it."""

def declaration_text(schema):
    if schema: return DECL.format(where="Your final message must be ", what="your answer, matching the schema you were given.")
    return DECL.format(where="End your final message with exactly one fenced ```json block containing ", what="your answer or the outcome of the task (any JSON value).")

def wrapper_schema(schema):
    return {"type": "object", "properties": {"result": schema, "deviations": {"type": "array", "items": {"type": "string"}},
            "files_changed": {"type": "array", "items": {"type": "string"}}}, "required": ["result", "deviations", "files_changed"], "additionalProperties": False}

def parse_declaration(text, structured=None, schema=None):
    """-> (declaration dict | None, problem str | None). structured: claude's structured_output when --json-schema was used."""
    cand = structured
    if cand is None:
        blocks = re.findall(r"```json\s*(.*?)```", text or "", re.S)
        try: cand = json.loads(blocks[-1] if blocks else (text or ""))
        except ValueError: return None, "no parseable JSON declaration in the final message"
    if not isinstance(cand, dict) or set(cand) != {"result", "deviations", "files_changed"}: return None, "declaration must be an object with exactly result, deviations, files_changed"
    dv, fc = cand["deviations"], cand["files_changed"]
    if not (isinstance(dv, list) and dv and all(isinstance(x, str) for x in dv)): return None, "deviations must be a non-empty list of strings (['none'] for full coverage)"
    if not (isinstance(fc, list) and all(isinstance(x, str) for x in fc)): return None, "files_changed must be a list of strings"
    if schema:
        try:
            import jsonschema; jsonschema.validate(cand["result"], schema)
        except ImportError: pass
        except Exception as e: return None, "result violates the schema: " + str(e)[:300]
    return cand, None

def prepare_workdir(wd, out, in_place, max_mb):
    """-> (workdir to run in, scratch_copy bool). Clean git repo in place; otherwise a baselined scratch copy."""
    git = lambda *a, cwd=wd: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True).stdout
    is_git = bool(git("rev-parse", "--is-inside-work-tree").strip())
    if in_place or (is_git and not git("status", "--porcelain", "--", ".", *agent_run.GIT_EXCLUDE).strip()): return wd, False
    if size_mb(wd) > max_mb: raise ValueError(f"workdir > {max_mb} MB; copy too large (use --in-place on a clean repo)")
    dst = os.path.join(out, "work"); copy_tree(wd, dst)
    if not os.path.isdir(os.path.join(dst, ".git")): git("init", "-q", cwd=dst)  # own .git only: never commit into a parent repo
    git("add", "-A", cwd=dst); git("-c", "user.name=cli-subagents", "-c", "user.email=cli-subagents@localhost", "commit", "-q", "--allow-empty", "-m", "baseline", cwd=dst)
    return dst, True

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("task"); ap.add_argument("--engine", required=True, choices=["codex", "claude"]); ap.add_argument("--workdir", required=True)
    ap.add_argument("--access", default="workspace-write", choices=["read-only", "workspace-write"]); ap.add_argument("--model"); ap.add_argument("--effort")
    ap.add_argument("--allowed-tools"); ap.add_argument("--permission-mode", default="dontAsk"); ap.add_argument("--schema"); ap.add_argument("--resume")
    ap.add_argument("--out"); ap.add_argument("--timeout", type=int, default=3600); ap.add_argument("--in-place", action="store_true"); ap.add_argument("--max-mb", type=float, default=500)
    ap.add_argument("--min-5h", type=float, default=2); ap.add_argument("--min-week", type=float, default=1)
    a = ap.parse_args()
    wd0 = os.path.abspath(a.workdir)
    if not os.path.isdir(wd0): print(json.dumps({"error": "workdir missing"})); return 2
    started = datetime.datetime.now()
    out = os.path.abspath(a.out or os.path.expanduser(f"~/cli-subagents/runs/{started:%Y%m%dT%H%M%S}_{a.engine}_{os.getpid()}")); os.makedirs(out, exist_ok=True)
    user_schema = json.load(open(a.schema)) if a.schema else None
    task = open(a.task).read(); prompt = task + declaration_text(user_schema); open(os.path.join(out, "task_sent.md"), "w").write(prompt)
    try:
        wd, scratch = prepare_workdir(wd0, out, a.in_place or bool(a.resume), a.max_mb)
        rc, rec = agent_run.run(a.engine, prompt, wd, out, a.access, a.model, a.effort, a.timeout, a.resume, wrapper_schema(user_schema) if user_schema else None,
                                a.allowed_tools, a.permission_mode, a.min_5h, a.min_week)
    except ValueError as e: print(json.dumps({"error": str(e)})); return 2
    res = {"engine": a.engine, "mode": "task", "status": "GATED", "exit_code": rc, "workdir_requested": wd0, "workdir_used": wd, "scratch_copy": scratch, "access": a.access,
           "resumed_from": a.resume, "run_dir": out, "task_sha256": hashlib.sha256(task.encode()).hexdigest(), "started": int(started.timestamp())}
    if rc == 10: res["route"] = rec
    else:
        res.update({k: rec[k] for k in ("cli_version", "model", "effort", "session_id", "tokens", "cost_usd_reported", "permission_denials", "rate_limit_events", "usage_report", "usage_before", "usage_after", "git", "final_message", "seconds")})
        res["engine_exit_code"] = rec["exit_code"]; res["status"] = {0: "DECLARED", 11: "EXEC_FAILED", 12: "USAGE_LIMIT"}[rc]
        if rc == 0:
            res["declaration"], res["declaration_problem"] = parse_declaration(rec["final_message"], rec["structured_output"], user_schema)
            if res["declaration"] is None: res["status"], res["exit_code"], rc = "UNDECLARED", 13, 13
    open(os.path.join(out, "result.json"), "w").write(json.dumps(res, indent=1))
    agent_ledger.append({"id": os.path.basename(out), "engine": a.engine, "mode": "task", "status": res["status"], "exit_code": rc, "attempts": 1, "model": res.get("model"), "effort": res.get("effort"),
                         "session_id": res.get("session_id"), "workdir": wd0, "task_sha256": res["task_sha256"], "tokens": res.get("tokens"), "started": res["started"],
                         "seconds": res.get("seconds"), "run_dir": out, "outcome_check": None})
    print(json.dumps({"status": res["status"], "result_json": os.path.join(out, "result.json"), "session_id": res.get("session_id"), "usage_report": res.get("usage_report")}))
    return rc

if __name__ == "__main__":
    sys.exit(main())
