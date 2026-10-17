#!/usr/bin/env python3
"""Offline stand-in for `claude` (set CLAUDE_BIN to this file). Env: FAKE_MODE ok|nodecl|limit, FAKE_RESULT (JSON, default "pong"),
FAKE_LOGGED_IN (1|0), FAKE_ARGV_LOG (file that receives one argv JSON line per run)."""
import json, os, sys
a = sys.argv[1:]
if "--version" in a: print("9.9.9 (Fake Claude)"); sys.exit(0)
if a[:1] == ["auth"]: print(json.dumps({"loggedIn": os.environ.get("FAKE_LOGGED_IN", "1") == "1"})); sys.exit(0)
prompt = sys.stdin.read(); mode = os.environ.get("FAKE_MODE", "ok")
open(os.environ["FAKE_ARGV_LOG"], "a").write(json.dumps(a) + "\n")
sid = a[a.index("--resume") + 1] if "--resume" in a else "sess-1"
emit = lambda o: print(json.dumps(o), flush=True)
emit({"type": "system", "subtype": "init", "session_id": sid, "model": "fake-opus"})
import time
emit({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour", "unifiedWindows": {"five_hour": {"utilization": 0.02, "resetsAt": time.time() + 3600}, "seven_day": {"utilization": 0.05, "resetsAt": time.time() + 86400}}}})
if "slugify" in prompt:
    open("slug.py", "w").write("import re, unicodedata\ndef slugify(s):\n    s = unicodedata.normalize('NFKD', s.lower())\n    s = ''.join(c for c in s if not unicodedata.combining(c))\n    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')\n")
decl = {"result": json.loads(os.environ.get("FAKE_RESULT", '"pong"')), "deviations": ["none"], "files_changed": []}
if mode == "limit":
    emit({"type": "result", "subtype": "error", "is_error": True, "result": "Claude usage limit reached", "session_id": sid}); sys.exit(1)
res = {"type": "result", "subtype": "success", "is_error": False, "session_id": sid, "total_cost_usd": 0.01,
       "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2},
       "result": "done" if mode == "nodecl" else "done\n```json\n" + json.dumps(decl) + "\n```"}
if "--json-schema" in a: res["structured_output"] = decl
emit(res)
