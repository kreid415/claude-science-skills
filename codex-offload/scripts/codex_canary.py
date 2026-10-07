#!/usr/bin/env python3
"""codex_canary.py [--full] [--auto]  health check for the Codex offload path. Run before offloading in a session.
Quick checks (no quota): codex present and version, ChatGPT login, usage read returns windows, scripts match MANIFEST.sha256.
Full check (spends a little quota): a known-answer task through codex_loop.py (independent acceptance, baseline must be red).
--auto runs the full check only when due: Codex version changed since the last full pass, or last full pass is older than 7 days.
State: <this dir>/canary_state.json. Canary loops are logged to canary_ledger.jsonl, never the task ledger. Output: one JSON object. Exit 0 = OK to offload, 1 = do NOT offload and tell the user."""
import argparse, hashlib, json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = ["codex_usage.py", "codex_run.py", "codex_loop.py", "codex_ledger.py", "codex_canary.py", "codex_mutate.py"]
TASK = ("Implement `slugify(s)` in slug.py. Rules: lowercase; remove accents (decompose with unicodedata NFKD and drop combining marks); "
        "replace every run of characters that are not ASCII letters or digits with a single hyphen; strip leading and trailing hyphens; the empty string maps to the empty string.")
ACCEPT = ("import os,sys\nsys.path.insert(0, os.environ['ACCEPT_WORKDIR'])\nfrom slug import slugify\n"
          "cases = {'Hello, World!':'hello-world','  Multiple   spaces ':'multiple-spaces','Caf\\u00e9 d\\u00e9j\\u00e0 vu':'cafe-deja-vu','--a--b--':'a-b','':'','!!!':''}\n"
          "bad = {k:(slugify(k),v) for k,v in cases.items() if slugify(k)!=v}\nsys.exit(1 if bad else 0)\n")

def sh(cmd, timeout=120, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env); return p.returncode, (p.stdout + p.stderr)
    except Exception as e: return 127, f"{type(e).__name__}: {e}"

def sha(p):
    h = hashlib.sha256(); h.update(open(p, "rb").read()); return h.hexdigest()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--full", action="store_true"); ap.add_argument("--auto", action="store_true")
    a = ap.parse_args()
    sp = os.path.join(HERE, "canary_state.json")
    state = json.load(open(sp)) if os.path.exists(sp) else {}
    res = {"checks": {}, "ok": True}
    def chk(name, ok, detail=""):
        res["checks"][name] = {"ok": bool(ok), "detail": str(detail)[-300:]}
        if not ok: res["ok"] = False
    rc, out = sh(["codex", "--version"]); ver = out.strip()
    chk("codex_present", rc == 0, ver)
    rc, out = sh(["codex", "login", "status"]); chk("chatgpt_login", rc == 0 and "logged in" in out.lower(), out.strip())
    rc, out = sh([sys.executable, os.path.join(HERE, "codex_usage.py")], 90)
    try: u = json.loads(out)
    except ValueError: u = {"error": "unparseable: " + out[-120:]}
    chk("usage_read", "error" not in u and bool(u.get("windows")), json.dumps({k: u.get(k) for k in ("windows", "credits", "error")}))
    mp = os.path.join(HERE, "MANIFEST.sha256")
    if not os.path.exists(mp): chk("scripts_match_manifest", False, "MANIFEST.sha256 missing next to the scripts")
    else:
        want = {l.split()[1]: l.split()[0] for l in open(mp) if l.strip()}
        bad = [n for n in SCRIPTS if n in want and (not os.path.exists(os.path.join(HERE, n)) or sha(os.path.join(HERE, n)) != want[n])]
        missing = [n for n in SCRIPTS if n not in want]
        chk("scripts_match_manifest", not bad and not missing, f"drifted: {bad} not in manifest: {missing}")
    changed = state.get("codex_version") not in (None, ver)
    due = changed or time.time() - state.get("last_full_ok", 0) > 7 * 86400
    res["version_changed"] = changed; res["full_due"] = due
    if a.full or (a.auto and due):
        d = tempfile.mkdtemp(prefix="canary_"); w = os.path.join(d, "proj"); os.makedirs(w)
        open(os.path.join(w, "slug.py"), "w").write("def slugify(s):\n    return s\n")
        open(os.path.join(d, "task.md"), "w").write(TASK); open(os.path.join(d, "accept.py"), "w").write(ACCEPT)
        rc, out = sh([sys.executable, os.path.join(HERE, "codex_loop.py"), os.path.join(d, "task.md"), w, "--accept", f"{sys.executable} {os.path.join(d, 'accept.py')}",
                      "--accept-file", os.path.join(d, "accept.py"), "--ladder", "default", "--timeout", "600"], 900,
                     env=dict(os.environ, CODEX_LEDGER=os.path.join(HERE, "canary_ledger.jsonl")))
        chk("full_known_answer_task", rc == 0, f"loop rc={rc} " + out[-200:]); res["full_ran"] = True
        if rc == 0: state["last_full_ok"] = int(time.time())
    if res["ok"]: state["codex_version"] = ver; state["last_quick_ok"] = int(time.time())
    json.dump(state, open(sp, "w"))
    print(json.dumps(res)); return 0 if res["ok"] else 1

if __name__ == "__main__":
    sys.exit(main())
