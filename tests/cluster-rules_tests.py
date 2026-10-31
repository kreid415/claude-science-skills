"""Tests for cluster-rules/scripts/budget_check.py. Run: python -m pytest -q tests/cluster-rules_tests.py (stdlib + pytest)."""
import json, os, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cluster-rules", "scripts"))
import budget_check as bc

LIM = dict(max_cpus_held=50, max_gpus_held=0, cpu_hours=1000, gpu_hours=0, max_job_mem_gb=16, max_job_time_h=12, max_jobs=500)


def setup(tmp_path, ledger=(), sacct="", lim=None, **extra):
    b = tmp_path / "B.json"
    b.write_text(json.dumps({"plan": "test-plan", "approved": "2026-10-31 user: agree", "limits": dict(LIM, **(lim or {})), **extra}))
    (tmp_path / "B.jobs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ledger))
    s = tmp_path / "s.txt"; s.write_text(sacct)
    return str(b), str(s)


def row(jid, state="COMPLETED", ex="0:0", cpus=2, reqmem="8G", rss="", el=3600, tcpu="02:00:00", tres=""):
    return "|".join([jid, state, ex, str(cpus), "0" if state == "PENDING" else str(cpus), reqmem, rss, str(el), tcpu, tres, tres]) + "\n"


def good(jid):  # completed task, 1 h on 2 CPUs fully busy, 7 of 8 GB used
    return row(jid) + row(jid + ".batch", rss="7G")


def run(capsys, b, s, **kw):
    req = {"cpus": "2", "mem-gb": "8", "time-h": "4"}; req.update(kw)
    args = [x for k, v in req.items() for x in ("--" + k, str(v))]
    rc = bc.main(["check", "--budget", b, "--sacct", s, *args])
    return rc, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_allow_with_usage(tmp_path, capsys):
    led = [{"job": "100", "batch": "b1"}, {"job": "400", "batch": "b2"}]  # b2 is still running: no stop checks on it
    b, s = setup(tmp_path, led, good("100_1") + "\n" + row("100_[2-9%3]", "PENDING", el=0) + row("400", "RUNNING", el=1800))
    rc, out = run(capsys, b, s)
    assert rc == 0 and out["decision"] == "allow"
    assert out["usage"] == {"cpu_hours": 3.0, "gpu_hours": 0.0, "cpus_held": 8, "gpus_held": 0, "jobs": 10}


def test_warn_at_80_percent(tmp_path, capsys):
    b, s = setup(tmp_path, lim={"cpu_hours": 10})
    rc, out = run(capsys, b, s, tasks=2)  # 2 CPUs x 2 tasks x 2 h estimate = 8 of 10
    assert rc == 0 and out["decision"] == "warn" and "80%" in out["reasons"][0]


@pytest.mark.parametrize("extra,kw,needle", [
    ({"expires": "2026-10-01"}, {"now": "2026-10-31"}, "expired"),
    ({}, {"gpus": 1}, "GPU work is not in this budget"),
    ({}, {"mem-gb": 20}, "per-job limit 16"),
    ({}, {"time-h": 13}, "per-job limit 12"),
    ({}, {"time-h": 10, "measured-h": 3}, "2 x measured"),
    ({}, {"tasks": 100, "concurrent": 30}, "CPUs held would be 60"),
    ({"lim": {"max_jobs": 5}}, {"tasks": 6, "concurrent": 1}, "jobs would be 6"),
    ({"lim": {"cpu_hours": 5}}, {"tasks": 2}, "CPU-hours"),
    ({"lim": {"max_gpus_held": 1, "gpu_hours": 100}}, {"gpus": 2}, "GPUs held would be 2"),
])
def test_refuse_limits(tmp_path, capsys, extra, kw, needle):
    b, s = setup(tmp_path, **extra)
    rc, out = run(capsys, b, s, **kw)
    assert rc == 1 and out["decision"] == "refuse" and any(needle in r for r in out["reasons"]), out


def test_fail_fraction_and_same_failure_and_ack(tmp_path, capsys):
    led = [{"job": "100", "batch": "b1"}, {"job": "200", "batch": "b2"}]
    oom = lambda j: row(j, "OUT_OF_MEMORY", "0:125")
    sacct = "".join(good(f"100_{i}") for i in range(4)) + oom("100_9") + "".join(good(f"200_{i}") for i in range(3)) + oom("200_7") + oom("200_8")
    b, s = setup(tmp_path, led, sacct)
    rc, out = run(capsys, b, s)
    assert rc == 1 and any("2/5 finished tasks failed" in r for r in out["reasons"]) and any("same failure" in r for r in out["reasons"])
    bc.main(["record", "--budget", b, "--ack", "user: continue after memory fix"]); capsys.readouterr()
    assert run(capsys, b, s)[0] == 0  # an acknowledgement clears stop conditions from earlier batches


def test_timeout_expected_and_cancelled_ignored(tmp_path, capsys):
    led = [{"job": "100", "batch": "b1"}, {"job": "200", "batch": "b2"}]
    sacct = "".join(row(f"{j}_{i}", "TIMEOUT") for j in (100, 200) for i in range(5)) + row("200_9", "CANCELLED by 1")
    b, s = setup(tmp_path, led, sacct, timeout_expected=True)
    assert run(capsys, b, s)[0] == 0


def test_efficiency_miss_twice_but_not_helper(tmp_path, capsys):
    lazy = lambda j: row(j, tcpu="00:30:00") + row(j + ".batch", rss="2G")  # 25% CPU, 25% memory
    led = [{"job": "100", "batch": "b1"}, {"job": "300", "batch": "helper-watch"}, {"job": "200", "batch": "b2"}]
    b, s = setup(tmp_path, led, lazy("100_1") + lazy("300") + lazy("200_1"))
    rc, out = run(capsys, b, s)
    assert rc == 1 and any("efficiency miss" in r and "CPU" in r and "memory" in r for r in out["reasons"])
    b, s = setup(tmp_path, led[:2], lazy("100_1") + lazy("300"))  # one plan batch + a helper: no stop
    assert run(capsys, b, s)[0] == 0


@pytest.mark.parametrize("sacct,needle", [
    ("", "sacct output is empty"),
    ("100|COMPLETED\n", "expected 11"),
    (row("999"), "not in this plan's ledger"),
])
def test_bad_sacct_fails_loud(tmp_path, capsys, sacct, needle):
    b, s = setup(tmp_path, [{"job": "100", "batch": "b1"}], sacct)
    with pytest.raises(SystemExit) as e: run(capsys, b, s)
    assert e.value.code == 2 and needle in capsys.readouterr().out


def test_bad_budget_and_ledger(tmp_path, capsys):
    b = tmp_path / "X.json"; b.write_text(json.dumps({"limits": LIM}))
    with pytest.raises(SystemExit): bc.main(["sacct-cmd", "--budget", str(b)])
    assert "approved" in capsys.readouterr().out
    with pytest.raises(SystemExit): bc.main(["sacct-cmd", "--budget", str(tmp_path / "missing.json")])
    b2, _ = setup(tmp_path); (tmp_path / "B.jobs.jsonl").write_text("{not json\n")
    with pytest.raises(SystemExit): bc.main(["sacct-cmd", "--budget", b2])
    assert "corrupt ledger" in capsys.readouterr().out.splitlines()[-1]
    b3, _ = setup(tmp_path)
    with pytest.raises(SystemExit): run(capsys, b3, str(tmp_path / "nope"))


def test_record_and_sacct_cmd(tmp_path, capsys):
    b, _ = setup(tmp_path); os.remove(tmp_path / "B.jobs.jsonl")  # no ledger file until the first record
    assert bc.main(["sacct-cmd", "--budget", b]) == 0 and "no jobs" in capsys.readouterr().out
    for bad in (["--job", "100"], ["--job", "abc", "--batch", "b1"]):
        with pytest.raises(SystemExit): bc.main(["record", "--budget", b, *bad])
    bc.main(["record", "--budget", b, "--job", "200", "--batch", "b1"]); bc.main(["record", "--budget", b, "--job", "100", "--batch", "b1"])
    capsys.readouterr(); bc.main(["sacct-cmd", "--budget", b])
    assert capsys.readouterr().out.startswith("sacct -n -P -j 100,200 --format=JobID,State")


@pytest.mark.parametrize("s,cpus,mb", [("4G", 1, 4096), ("4000M", 1, 4000), ("2Gc", 4, 8192), ("4Gn", 2, 4096),
                                       ("1048576K", 1, 1024), ("1073741824", 1, 1024), ("", 1, None)])
def test_mem_mb(s, cpus, mb):
    assert bc.mem_mb(s, cpus) == mb


@pytest.mark.parametrize("s,sec", [("1-02:00:00", 93600), ("05:30.500", 330.5), ("01:00:00", 3600)])
def test_secs(s, sec):
    assert bc.secs(s) == sec


def test_pending_array_counts_and_gpu_tres():
    assert bc.n_pending("5_[1-10,20,30-31]") == (13, 13) and bc.n_pending("5_[1-100%4]") == (100, 4) and bc.n_pending("5") == (1, 1)
    assert bc.gpus("cpu=4,gres/gpu:a100=2,mem=8G") == 2 and bc.gpus("cpu=4") == 0
