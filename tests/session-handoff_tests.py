"""Tests for session-handoff: size trigger, wave boundary, run-state table, and that the copies
inlined in SKILL.md's per-turn repl check are identical to kernel.py. Stdlib only.
Run:  python tests/session-handoff_tests.py   (from the repo root)"""
import os, re, json, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KSRC = open(os.path.join(ROOT, "session-handoff", "kernel.py")).read()
SKILL = open(os.path.join(ROOT, "session-handoff", "SKILL.md")).read()
K = {}
exec(KSRC, K)
OK = []


def t(name):
    def deco(fn):
        try:
            fn(); OK.append(True); print("PASS", name)
        except Exception:
            OK.append(False); print("FAIL", name); traceback.print_exc(limit=3)
    return deco


def fsrc(text, name):
    m = re.search(r"^def %s\(.*?(?=^\S|\Z)" % name, text, re.S | re.M)
    assert m, "no def %s" % name
    return m.group(0).rstrip()


# the real ledger handle formats seen on 2026-10-08 (JHPCE SLURM job, cs-local nohup job)
SLURM = {"job_id": "00fb039b", "provider": "ssh:jhpce", "state": "running", "remote_workdir": "/users/u/jobs/00fb039b",
         "remote_handle": '{"kind":"slurm","jobId":"36241098","workdir":"/users/u/jobs/00fb039b"}', "ended_at": None}
LOCAL = {"job_id": "c5c04372", "provider": "ssh:cs-local", "state": "queued", "remote_workdir": "/home/u/jobs/c5c04372",
         "remote_handle": '{"kind":"nohup","pgid":5351,"startTime":1,"workdir":"/home/u/jobs/c5c04372"}', "ended_at": None}
DONE = dict(SLURM, job_id="d1", state="done", ended_at=1791486803085)


@t("SKILL.md repl check inlines handoff_trigger, handoff_quiet, handoff_wave_offer identical to kernel.py")
def _():
    block = SKILL.split("## The size check", 1)[1].split("```python", 1)[1].split("```", 1)[0]
    for n in ("handoff_trigger", "handoff_quiet", "handoff_wave_offer"):
        assert fsrc(block, n) == fsrc(KSRC, n), n
    m = re.search(r"^HANDOFF_JOB_TERMINAL = (\(.*?\))$", block, re.M)
    assert m and eval(m.group(1)) == K["HANDOFF_JOB_TERMINAL"]


@t("the per-turn repl check runs end to end against a fake host")
def _():
    block = SKILL.split("## The size check", 1)[1].split("```python", 1)[1].split("```", 1)[0]

    class H:
        def query(self, sql, params=None):
            if "FROM frames f" in sql: return {"rows": [[160, 90000, 0, 12, 3]]}
            assert "compute_usage" in sql and params == ["F1"]
            return {"rows": [[DONE[c] for c in ("job_id", "provider", "state", "remote_workdir", "remote_handle", "ended_at")]]}
        def children(self): return {"running_children": [], "count": 0}
    out = []
    g = {"host": H(), "print": lambda *a: out.append(a)}
    exec(block.replace('"<this chat\'s frame id>"', '"F1"'), g)
    assert g["qt"]["quiet"] is True and out[-1][-1] is True, out  # quiet, a job ended, 160 msgs -> offer


@t("size trigger: 300 msgs / 280k / 1 fold; re-arm after 150 msgs or a new fold; urgent at 2000")
def _():
    f = K["handoff_trigger"]
    assert not f(299, 279999, 0)["fire"] and f(300, 0, 0)["fire"] and f(10, 280000, 0)["fire"] and f(10, 0, 1)["fire"]
    assert not f(400, 0, 1, 300, 1)["fire"] and f(450, 0, 1, 300, 1)["fire"] and f(301, 0, 2, 300, 1)["fire"]
    assert f(2000, 0, 0)["urgent"] and not f(1999, 0, 0)["urgent"]


@t("wave boundary: any live job, sub-agent, board unit or unconfirmed watcher blocks quiet")
def _():
    q = K["handoff_quiet"]
    assert q([DONE])["quiet"] and q([])["quiet"]
    assert q([DONE, SLURM])["live_jobs"] == ["00fb039b"] and not q([LOCAL])["quiet"]
    assert not q([DONE], children=1)["quiet"]
    assert not q([], boards=[{"board": {"totals": {"pending": 2}}}])["quiet"]
    assert not q([], boards=[{"totals": {"running": 1}}])["quiet"]
    assert q([], boards=[{"board": {"totals": {"done_valid": 9, "failed": 1, "missing": 3}}}])["quiet"]
    assert not q([], watchers=[{"id": "A"}])["quiet"] and not q([], watchers=[{"id": "A", "status": "running"}])["quiet"]
    assert q([], watchers=[{"id": "A", "status": "done"}, {"id": "B", "status": "needs_human"}])["quiet"]
    assert q([dict(DONE, state="SUCCEEDED")])["quiet"]


@t("wave offer: quiet, a job ended after the last offer, >= 150 msgs; once per wave")
def _():
    o = K["handoff_wave_offer"]
    assert o(True, 150, 2000, 1000) and not o(True, 149, 2000, 1000) and not o(False, 500, 2000, 1000)
    assert not o(True, 500, 1000, 1000) and not o(True, 500, None, 0) and o(True, 500, 1, 0)


@t("run-state rows: sacct -j for SLURM, ps -g for local, jw.py status for watchers; terminal items omitted")
def _():
    r = K["handoff_run_state_rows"]
    assert r([DONE], watchers=[{"id": "A", "host": "h", "state_dir": "/s", "status": "done"}]) == ""
    txt = r([SLURM, LOCAL, DONE], watchers=[{"id": "A", "host": "ssh:jhpce", "state_dir": "/scratch/A/state"}],
            boards=[("b/status_board.json", {"board": {"n_units": 10, "totals": {"running": 2, "done_valid": 8}, "generated_at": "T"}})],
            children=[{"frame_id": "fr1", "name": "x"}])
    assert "sacct -X -j 36241098" in txt and "ps -o pid,stat,etime,cmd -g 5351" in txt and "d1" not in txt
    assert "jw.py status --state /scratch/A/state" in txt and "2 of 10 running/pending" in txt and "`fr1`" in txt
    assert len([l for l in txt.splitlines() if l.startswith("| ")]) == 6  # header + 5 rows
    assert "ls -lt /w | head" in r([{"job_id": "x", "state": "running", "remote_workdir": "/w", "remote_handle": "not json"}])


print("all session-handoff tests passed" if all(OK) else "SOME session-handoff TESTS FAILED")
raise SystemExit(0 if all(OK) else 1)
