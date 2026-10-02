"""Tests for the run-status-board sidecar (kernel.py). Synthetic fixtures reproduce known failure patterns.
Run:  python run-status-board_tests.py [path/to/kernel.py]   (default: rsb_kernel.py in cwd)"""
import sys, os, tempfile, json, traceback

KERNEL = sys.argv[1] if len(sys.argv) > 1 else "rsb_kernel.py"
exec(open(KERNEL).read(), globals())

RESULTS = []


def t(name, incidents=""):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, incidents, True, ""))
        except Exception:
            RESULTS.append((name, incidents, False, traceback.format_exc(limit=3)))
        return fn
    return deco


def raises(exc, fn, frag=""):
    try:
        fn()
    except exc as e:
        assert frag in str(e), "wrong message: %s" % e
        return
    raise AssertionError("expected %s containing %r, nothing raised" % (exc.__name__, frag))


HDR = "JobID|JobName|State|ExitCode|Elapsed|Start|End|NodeList|ReqMem|MaxRSS|Timelimit|AllocCPUS"
CAP = "#CAPTURED 2026-10-02T12:00:00-0400"
S = "2026-10-02T08:00:00|2026-10-02T09:10:00"


def line(jid, name, state, exit_, el, start, end, node, req, rss, lim, cpu="8"):
    return "|".join([jid, name, state, exit_, el, start, end, node, req, rss, lim, cpu])


SACCT = "\n".join([CAP, HDR,
    line("5001_0", "sweep", "COMPLETED", "0:0", "01:10:00", "2026-10-02T08:00:00", "2026-10-02T09:10:00", "n1", "64Gn", "", "12:00:00"),
    line("5001_0.batch", "batch", "COMPLETED", "0:0", "01:10:00", "2026-10-02T08:00:00", "2026-10-02T09:10:00", "n1", "64Gn", "30000000K", ""),
    line("5001_0.extern", "extern", "COMPLETED", "0:0", "01:10:00", "2026-10-02T08:00:00", "2026-10-02T09:10:00", "n1", "64Gn", "0", ""),
    line("5001_1", "sweep", "COMPLETED", "0:0", "00:50:00", "2026-10-02T08:00:00", "2026-10-02T08:50:00", "n2", "64Gn", "", "12:00:00"),
    line("5001_1.batch", "batch", "OUT_OF_MEMORY", "0:125", "00:50:00", "2026-10-02T08:00:00", "2026-10-02T08:50:00", "n2", "64Gn", "65000000K", ""),
    line("5001_2", "sweep", "TIMEOUT", "0:0", "00:20:00", "2026-10-02T08:00:00", "2026-10-02T08:20:00", "n3", "64Gn", "", "12:00:00"),
    line("5001_3", "sweep", "RUNNING", "0:0", "00:30:00", "2026-10-02T11:30:00", "Unknown", "n4", "64Gn", "", "12:00:00"),
    line("5001_[4-6%2]", "sweep", "PENDING", "0:0", "00:00:00", "Unknown", "Unknown", "None assigned", "64Gn", "", "12:00:00"),
    line("5001_7", "sweep", "CANCELLED by 1234", "0:0", "00:05:00", "2026-10-02T08:00:00", "2026-10-02T08:05:00", "n5", "64Gn", "", "12:00:00"),
    line("5001_8", "sweep", "NODE_FAIL", "0:0", "00:15:00", "2026-10-02T08:00:00", "2026-10-02T08:15:00", "n6", "64Gn", "", "12:00:00"),
    line("5001_9", "sweep", "COMPLETED", "1:0", "00:15:00", "2026-10-02T08:00:00", "2026-10-02T08:15:00", "n6", "64Gn", "", "12:00:00"),
])
UNITS = ["sh_%d" % i for i in range(10)]
MAN = [{"shard": u, "task": "5001_%d" % i} for i, u in enumerate(UNITS)]
NOW = "2026-10-02T12:05:00-04:00"


def check_factory(outputs):
    """outputs: unit -> (found_seeds, expected_seeds, failed_rows) or None (nothing written)."""
    def chk(row):
        o = outputs.get(row["shard"])
        if o is None:
            return None
        found, exp, failed = o
        ok = found == exp and failed == 0
        reason = "%d/%d seeds, %d failed rows" % (found, exp, failed)
        return (ok, reason, {"found": found, "expected": exp, "finished_at": "2026-10-02T09:10:00-04:00"} if ok else {"found": found, "expected": exp})
    return chk


@t("parse_sacct: steps fold, array range expands, states", "")
def _():
    p = rs_parse_sacct(SACCT)
    by = {r["job_id"]: r for r in p["rows"]}
    assert len(p["rows"]) == 10, len(p["rows"])
    assert by["5001_0"]["max_rss_mb"] > 28000 and by["5001_0"]["state"] == "COMPLETED"
    assert by["5001_1"]["state"] == "OUT_OF_MEMORY" and any(n.startswith("oom_in_step") for n in by["5001_1"]["notes"])
    assert any(n.startswith("timeout_suspect") for n in by["5001_2"]["notes"])
    assert [by["5001_%d" % i]["state"] for i in (4, 5, 6)] == ["PENDING"] * 3 and by["5001_5"]["from_range"]
    assert by["5001_7"]["state"] == "CANCELLED" and by["5001_7"]["cancelled_by"] == "1234"
    assert by["5001_8"]["category"] == "failed" and by["5001_9"]["state"] == "FAILED"
    assert p["captured_at"] == "2026-10-02T12:00:00-04:00" and by["5001_0"]["start"].endswith("-04:00")


@t("board: pending array tasks are pending, not 'walled'/failed", "")
def _():
    b = rs_board(MAN, "shard", check_factory({"sh_0": (5, 5, 0)}), rs_parse_sacct(SACCT), sched_key="task", now=NOW)
    st = {u["unit"]: u["state"] for u in b["units"]}
    assert st["sh_4"] == st["sh_5"] == st["sh_6"] == "pending", st
    assert st["sh_3"] == "running" and st["sh_0"] == "done_valid"


@t("board: OOM'd seed with .done marker is not done (marker-only check gets a loud conflict)", "")
def _():
    outs = {"sh_0": (5, 5, 0), "sh_1": (4, 5, 0)}
    b = rs_board(MAN, "shard", check_factory(outs), rs_parse_sacct(SACCT), sched_key="task", now=NOW)
    u = {x["unit"]: x for x in b["units"]}
    assert u["sh_1"]["state"] == "failed" and "OUT_OF_MEMORY" in u["sh_1"]["reason"], u["sh_1"]
    assert "sh_1" in b["needs_action"]
    # known-bad checker: trusts the marker file (always True) -> board still surfaces the scheduler conflict
    b2 = rs_board(MAN, "shard", lambda r: (True, ".done marker") if r["shard"] == "sh_1" else None, rs_parse_sacct(SACCT), sched_key="task", now=NOW)
    u2 = {x["unit"]: x for x in b2["units"]}["sh_1"]
    assert u2["state"] == "done_valid" and any(n.startswith("conflict") for n in u2["notes"])
    assert any("sh_1" in a for a in b2["anomalies"])


@t("board: shard exit 0 with failed rows -> done_invalid, anomaly", "")
def _():
    outs = {"sh_0": (5, 5, 0), "sh_9": (5, 5, 9)}
    sc = SACCT.replace("5001_9|sweep|COMPLETED|1:0", "5001_9|sweep|COMPLETED|0:0")
    b = rs_board(MAN, "shard", check_factory(outs), rs_parse_sacct(sc), sched_key="task", now=NOW)
    u = {x["unit"]: x for x in b["units"]}["sh_9"]
    assert u["state"] == "done_invalid" and "9 failed rows" in u["reason"] and any(n.startswith("anomaly") for n in u["notes"]), u


@t("board: 1-of-3 seeds output is done_invalid; sub-unit tally; headline not 'complete'", "")
def _():
    man = [{"unit": "u%d" % i} for i in range(30)]
    outs = {"u%d" % i: (3, 3, 0) for i in range(22)}
    outs.update({"u22": (1, 3, 0), "u23": (2, 3, 0)})
    chk = lambda r: (lambda o: None if o is None else (o[0] == o[1], "%d/%d seeds" % (o[0], o[1]), {"found": o[0], "expected": o[1]}))(outs.get(r["unit"]))
    b = rs_board(man, "unit", chk, now=NOW)
    assert b["totals"]["done_valid"] == 22 and b["totals"]["done_invalid"] == 2 and b["totals"]["missing"] == 6
    assert b["headline"].startswith("22/30 units done_valid") and "complete" not in b["headline"].lower()
    assert b["subunits"] == {"found": 22 * 3 + 3, "expected": 24 * 3}


@t("tz: naive cluster timestamps need explicit tz; rs_since reproduces 34 min not 3 h", "")
def _():
    r = rs_since("2026-10-02T08:52:00", "2026-10-02T09:26:00-04:00", tz="-04:00")
    assert abs(r["age_s"] - 34 * 60) < 1
    wrong = rs_since("2026-10-02T08:52:00", "2026-10-02T09:26:00-04:00", tz="UTC")  # the original mistake
    assert wrong["age_s"] > 4 * 3600
    raises(ValueError, lambda: rs_since("2026-10-02T08:52:00", "2026-10-02T09:26:00-04:00"), "RS_TZ_MISSING")
    raises(ValueError, lambda: rs_since("2026-10-02T13:00:00", "2026-10-02T09:26:00-04:00", tz="-04:00"), "RS_SINCE_FUTURE")


@t("clock: sacct without #CAPTURED stamp raises; tz conflict raises; non-strict + tz ok", "")
def _():
    nocap = SACCT.replace(CAP + "\n", "")
    raises(ValueError, lambda: rs_parse_sacct(nocap), "RS_NO_CAPTURE_STAMP")
    raises(ValueError, lambda: rs_parse_sacct(SACCT, tz="UTC"), "RS_TZ_CONFLICT")
    p = rs_parse_sacct(nocap, tz="-04:00", strict=False)
    assert p["captured_at"] is None and p["rows"][0]["start"].endswith("-04:00")
    raises(ValueError, lambda: rs_parse_sacct(nocap, strict=False), "RS_TZ_MISSING")


@t("running job is 'running', never complete (stale 'just finished' claim)", "")
def _():
    b = rs_board(MAN, "shard", lambda r: None, rs_parse_sacct(SACCT), sched_key="task", now=NOW)
    u = {x["unit"]: x for x in b["units"]}["sh_3"]
    assert u["state"] == "running" and b["totals"]["done_valid"] == 0
    md = rs_render(b)
    assert "generated_at: 2026-10-02T12:05:00-04:00" in md and "## running (1)" in md


@t("TIMEOUT with Elapsed << Timelimit is flagged as non-walltime (cap kill)", "")
def _():
    b = rs_board(MAN, "shard", lambda r: None, rs_parse_sacct(SACCT), sched_key="task", now=NOW)
    u = {x["unit"]: x for x in b["units"]}["sh_2"]
    assert u["state"] == "failed" and any("timeout_suspect" in a for a in b["anomalies"])
    ok = SACCT.replace("5001_2|sweep|TIMEOUT|0:0|00:20:00", "5001_2|sweep|TIMEOUT|0:0|12:00:00")
    p = rs_parse_sacct(ok)
    assert not [n for r in p["rows"] if r["job_id"] == "5001_2" for n in r["notes"] if n.startswith("timeout_suspect")]


@t("ETA refuses below min_done; works with enough completions; interval ordered", "")
def _():
    def mk(n_done):
        man = [{"unit": "u%d" % i} for i in range(40)]
        fin = {"u%d" % i: "2026-10-02T%02d:%02d:00-04:00" % (8 + (i * 7) // 60, (i * 7) % 60) for i in range(n_done)}
        chk = lambda r: (True, "ok", {"finished_at": fin[r["unit"]]}) if r["unit"] in fin else None
        return rs_board(man, "unit", chk, now="2026-10-02T12:00:00-04:00")
    b3 = mk(3)
    e = rs_eta(b3, strict=False)
    assert e["refused"] and "need >= 5" in e["reason"] and e["eta_median"] is None
    raises(RuntimeError, lambda: rs_eta(b3), "RS_ETA_REFUSED")
    b12 = mk(12)
    e = rs_eta(b12)
    assert not e["refused"] and e["remaining"] == 28 and e["seconds_low"] <= e["seconds_median"] <= e["seconds_high"]
    assert abs(e["seconds_median"] - 28 * 420) < 1, e["seconds_median"]  # equal 7-min gaps -> 28 * 420 s
    md = rs_render(b12, e)
    assert "ETA (median" in md and "ETA: none" in rs_render(b3, rs_eta(b3, strict=False)).replace("**", "")


@t("glob presence-only requires explicit opt-in and is labelled", "")
def _():
    d = tempfile.mkdtemp()
    open(os.path.join(d, "a.json"), "w").write("{}")
    man = [{"s": "a"}, {"s": "b"}]
    raises(ValueError, lambda: rs_board(man, "s", d + "/{s}.json"), "RS_PRESENCE_ONLY")
    b = rs_board(man, "s", d + "/{s}.json", allow_presence_only=True, now=NOW)
    assert b["totals"]["done_valid"] == 1 and b["totals"]["missing"] == 1 and b["validity_basis"] == "presence_only"
    assert "PRESENCE ONLY" in rs_render(b)


@t("fail-loud: unknown state, malformed line, stale snapshot, unmapped sched, dup keys, empty manifest, bad checker", "")
def _():
    raises(ValueError, lambda: rs_parse_sacct(SACCT.replace("RUNNING", "FROBNICATED")), "RS_STATE_UNKNOWN")
    p = rs_parse_sacct(SACCT.replace("RUNNING", "FROBNICATED"), strict=False)
    assert any("unknown_state" in n for r in p["rows"] for n in r["notes"])
    raises(ValueError, lambda: rs_parse_sacct(SACCT + "\n5002|a|b|c"), "RS_SACCT_LINE")
    parsed = rs_parse_sacct(SACCT)
    raises(RuntimeError, lambda: rs_board(MAN, "shard", lambda r: None, parsed, sched_key="task", now="2026-10-02T13:00:00-04:00"), "RS_SCHED_STALE")
    raises(ValueError, lambda: rs_board(MAN, "shard", lambda r: None, parsed, now=NOW), "RS_SCHED_UNMAPPED")  # default matches job_name == unit
    raises(ValueError, lambda: rs_board([{"s": "a"}, {"s": "a"}], "s", lambda r: None), "RS_UNIT_DUP")
    raises(ValueError, lambda: rs_board([], "s", lambda r: None), "RS_MANIFEST_EMPTY")
    raises(TypeError, lambda: rs_board([{"s": "a"}], "s", lambda r: "yes"), "RS_CHECK_RETURN")
    def boom(r):
        raise FileNotFoundError("x")
    raises(RuntimeError, lambda: rs_board([{"s": "a"}], "s", boom), "RS_CHECK_ERROR")
    b = rs_board([{"s": "a"}], "s", boom, strict=False)
    assert b["units"][0]["state"] == "done_invalid"
    raises(ValueError, lambda: rs_parse_sacct(""), "RS_EMPTY")


@t("squeue: array range pending with limit reason is blocking, not a failure", "")
def _():
    sq = "\n".join([CAP, "JOBID|NAME|STATE|TIME|TIME_LIMIT|SUBMIT_TIME|START_TIME|NODELIST(REASON)",
                    "5001_[4-6%2]|sweep|PENDING|0:00|12:00:00|2026-10-02T07:00:00|N/A|(AssocGrpCPUMinutesLimit)",
                    "5001_3|sweep|RUNNING|30:00|12:00:00|2026-10-02T07:00:00|2026-10-02T11:30:00|n4"])
    p = rs_parse_squeue(sq)
    assert len(p["rows"]) == 4 and p["rows"][0]["reason_blocking"] and p["rows"][3]["category"] == "running"
    b = rs_board(MAN, "shard", lambda r: None, p, sched_key="task", now=NOW)
    assert b["totals"]["pending"] == 3 and "3 blocked by a limit reason" in b["headline"]
    benign = sq.replace("AssocGrpCPUMinutesLimit", "Resources")
    assert not rs_parse_squeue(benign)["rows"][0]["reason_blocking"]


@t("save/history round-trip and sacct+squeue merge precedence (live row wins)", "")
def _():
    sq = "\n".join([CAP, "JOBID|NAME|STATE|TIME|TIME_LIMIT|SUBMIT_TIME|START_TIME|NODELIST(REASON)",
                    "5001_3|sweep|RUNNING|30:00|12:00:00|2026-10-02T07:00:00|2026-10-02T11:30:00|n4"])
    b = rs_board(MAN, "shard", check_factory({"sh_0": (5, 5, 0)}), [rs_parse_sacct(SACCT), rs_parse_squeue(sq)], sched_key="task", now=NOW)
    assert b["sched"]["n_rows"] == 11 and b["totals"]["running"] == 1
    d = tempfile.mkdtemp()
    e = rs_eta(b, strict=False)
    paths = rs_save(b, d, e)
    rs_save(b, d, e)
    assert json.load(open(paths["json"]))["board"]["headline"] == b["headline"]
    assert len(rs_history(d)) == 2 and "sh_0" in rs_history(d)[0]["finished"]
    assert rs_history(tempfile.mkdtemp()) == []
    open(paths["history"], "a").write("{bad\n")
    raises(ValueError, lambda: rs_history(d), "RS_HISTORY_CORRUPT")


@t("live scheduler row outranks an older terminal row for the same unit (requeue/retry)", "")
def _():
    sq = "\n".join([CAP, "JOBID|NAME|STATE|TIME|TIME_LIMIT|SUBMIT_TIME|START_TIME|NODELIST(REASON)",
                    "5001_1|sweep|RUNNING|05:00|12:00:00|2026-10-02T11:50:00|2026-10-02T11:55:00|n9"])
    b = rs_board(MAN, "shard", lambda r: None, [rs_parse_sacct(SACCT), rs_parse_squeue(sq)], sched_key="task", now=NOW)
    u = {x["unit"]: x for x in b["units"]}["sh_1"]
    assert u["state"] == "running" and u["attempts_seen"] == 2, u


@t("sidecar names: every public helper is rs_/RS_ prefixed, no underscore-leading names", "")
def _():
    import ast
    tree = ast.parse(open(KERNEL).read())
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            assert n.name.startswith("rs_") and not n.name.startswith("_"), n.name


if __name__ == "__main__":
    npass = sum(1 for r in RESULTS if r[2])
    for name, inc, ok, tb in RESULTS:
        print(("PASS" if ok else "FAIL"), "|", name, "| incidents:", inc or "-")
        if not ok:
            print(tb)
    print("TOTAL pass=%d fail=%d" % (npass, len(RESULTS) - npass))
    sys.exit(0 if npass == len(RESULTS) else 1)
