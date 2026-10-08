"""Tests for phase-delegation scripts/pd.py (stdlib). Run: python tests/phase-delegation_tests.py (repo root)."""
import os, traceback
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G = {}
exec(open(os.path.join(ROOT, "phase-delegation", "scripts", "pd.py")).read(), G)
OK = []
VID = "316cffe9-0125-47ed-8bb5-e8d7613461b0"


def t(name):
    def deco(fn):
        try:
            fn(); OK.append(True); print("PASS", name)
        except Exception:
            OK.append(False); print("FAIL", name); traceback.print_exc(limit=3)
    return deco


def raises(fn, frag):
    try:
        fn()
    except ValueError as e:
        assert frag in str(e), str(e); return
    raise AssertionError("no ValueError")


def brief(**kw):
    a = dict(phase="run", objective="run the test", inputs={"script": VID}, outputs=["log"],
             acceptance=["sacct shows COMPLETED"], host="ssh:cs-local")
    a.update(kw); return G["pd_brief"](**a)


def res(status="done", checks=True, dev=("none",), arts=(("log", VID),), jobs=(), **kw):
    so = {"status": status, "summary": "s", "jobs": [dict(ledger_id=j, host="h", state=s) for j, s in jobs],
          "artifacts": [dict(role=r, version_id=v) for r, v in arts],
          "checks": [dict(name="c1", ok=checks, evidence="seen COMPLETED in sacct")]}
    r = {"status": "completed", "structured_output": so, "deviations": list(dev)}
    r.update(kw); return r


@t("brief: markers built by concatenation, requirements/acceptance/outputs/rules present")
def _():
    b = brief(requirements=["use every shard"])
    assert ("{" + "{artifact:%s}}" % VID) in b and "COMPUTE TARGET: ssh:cs-local" in b and "- use every shard" in b
    assert "compute_done reaches only you" in b and "OUTPUTS:" in b and "deviations" in b
    assert "{{artifact:" not in open(os.path.join(ROOT, "phase-delegation", "scripts", "pd.py")).read()


@t("brief refuses: unknown phase, empty acceptance, input without version id, run without host, fallback data")
def _():
    raises(lambda: brief(phase="launch"), "phase must be")
    raises(lambda: brief(acceptance=[]), "acceptance is empty")
    raises(lambda: brief(inputs={"x": ""}), "no artifact version id")
    raises(lambda: brief(inputs={"x": "data.csv"}), "no artifact version id")
    raises(lambda: brief(host=None), "needs the compute target")
    raises(lambda: brief(notes="If access is blocked, simulate the data instead."), "pre-authorizes")
    raises(lambda: brief(requirements=["otherwise use synthetic counts"]), "pre-authorizes")
    brief(phase="analyze", host=None, objective="fit the simulation model to the observed data")  # legit word use


@t("request: default profiles, explicit override, schema attached")
def _():
    r = G["pd_request"]("Run A", "b", "run")
    assert r["profile"] == "CLUSTER_OPS" and r["output_schema"] is G["PD_SCHEMA"]
    assert "profile" not in G["pd_request"]("V", "b", "validate")
    assert G["pd_request"]("V", "b", "validate", profile="SC_ANALYST")["profile"] == "SC_ANALYST"
    assert set(G["PD_SCHEMA"]["required"]) == {"status", "summary", "jobs", "artifacts", "checks"}


@t("audit: clean result ok; limits carried; undeclared, prose-only, failed child flagged")
def _():
    A = G["pd_audit"]
    assert A(res(), ["log"], 1)["ok"]
    a = A(res(dev=("used 3 of 4 shards",)), ["log"]); assert a["ok"] and a["limits"] == ["used 3 of 4 shards"]
    assert not A(res(dev=("UNDECLARED (none sent)",)), ["log"])["ok"]
    assert not A({"status": "completed", "response": "prose", "structured_output_unsatisfied": True})["ok"]
    assert not A({"status": "failed", "error": "boom"})["ok"]


@t("audit: non-done status, failed check, missing output, too few checks, live job flagged")
def _():
    A = G["pd_audit"]
    assert "phase status needs_human" in A(res(status="needs_human"))["problems"][0]
    assert "failed checks: c1" in A(res(checks=False))["problems"]
    assert "missing outputs: board" in A(res(), ["log", "board"])["problems"]
    assert "1 of 3 acceptance checks reported" in A(res(), ["log"], 3)["problems"]
    a = A(res(jobs=(("j1", "RUNNING"), ("j2", "succeeded"))), ["log"])
    assert a["live_jobs"] == ["j1"] and not a["ok"]


print("all phase-delegation tests passed" if all(OK) else "SOME phase-delegation TESTS FAILED")
raise SystemExit(0 if all(OK) else 1)
