#!/usr/bin/env python3
"""agent_inbox.py list [--unread] | mark-read [ID ... | --all]   results of scheduled jobs (agent_schedule.py run).
Index: $CLI_SUBAGENTS_INBOX/index.jsonl (default ~/agent-inbox): one line per run {id, name, time, status, exit_code, result, summary, usage, read}. ID = NAME/<UTC timestamp>.
`list` prints one JSON line per entry; read the `summary` file (or `result`) of each unread entry, then `mark-read`."""
import argparse, fcntl, json, os, sys

def inbox(): return os.environ.get("CLI_SUBAGENTS_INBOX") or os.path.expanduser("~/agent-inbox")
def index_path(): return os.path.join(inbox(), "index.jsonl")

def locked(fn):
    os.makedirs(inbox(), exist_ok=True)
    with open(index_path() + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX); return fn()

def read():
    p = index_path()
    return [json.loads(l) for l in open(p) if l.strip()] if os.path.exists(p) else []

def append(entry):
    def w():
        with open(index_path(), "a") as f: f.write(json.dumps(entry) + "\n")
    locked(w)

def mark_read(ids=None):
    """ids None = all. Returns the number of entries changed."""
    def w():
        rows = read(); n = 0
        for r in rows:
            if not r["read"] and (ids is None or r["id"] in ids): r["read"] = True; n += 1
        with open(index_path() + ".tmp", "w") as f: f.writelines(json.dumps(r) + "\n" for r in rows)
        os.replace(index_path() + ".tmp", index_path()); return n
    return locked(w)

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list").add_argument("--unread", action="store_true")
    m = sp.add_parser("mark-read"); m.add_argument("ids", nargs="*"); m.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if a.cmd == "list":
        for r in read():
            if not (a.unread and r["read"]): print(json.dumps(r))
        return 0
    if not (a.ids or a.all): print(json.dumps({"error": "give IDs or --all"})); return 2
    print(json.dumps({"marked": mark_read(None if a.all else set(a.ids))})); return 0

if __name__ == "__main__":
    sys.exit(main())
