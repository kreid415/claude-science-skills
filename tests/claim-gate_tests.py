"""Tests for the claim-gate sidecar. Run: python claim-gate_tests.py [path/to/kernel.py]
Each case reproduces a known failure pattern as a synthetic fixture:
known-bad must raise the specific CG*Error, known-good must pass. Writes claim-gate_test_results.json."""
import json, os, subprocess, sys, tempfile, time
import numpy as np
import pandas as pd

KERNEL = sys.argv[1] if len(sys.argv) > 1 else "claim-gate_kernel.py"
ns = {}
exec(compile(open(KERNEL).read(), KERNEL, "exec"), ns)
G = ns
Base = G["cg_error"]("base")
RES = []
tmp = tempfile.mkdtemp()
P = lambda n: os.path.join(tmp, n)


def case(name, incident, kind, fn, extra=None, require_ok=True):
    """kind=None: must pass. kind='x': must raise cg_error('x') (and only that)."""
    try:
        out = fn()
        got = "pass"
        ok = kind is None and (not require_ok or out is None or out is True or (isinstance(out, dict) and out.get("ok", True)))
        if extra and ok:
            ok = bool(extra(out))
    except Base as e:
        got = "raise:" + type(e).__name__
        ok = kind is not None and isinstance(e, G["cg_error"](kind))
    except Exception as e:  # any other exception is a helper bug
        got = "BUG:" + type(e).__name__ + ":" + str(e)[:120]
        ok = False
    RES.append({"name": name, "incident": incident, "expected": "pass" if kind is None else "raise:" + kind, "got": got, "ok": bool(ok)})


# ---- cg_changed  (saved v5 byte-identical to pre-edit v4)
open(P("fig_v4.png"), "wb").write(b"\x89PNG-original")
before = G["cg_sha256"](P("fig_v4.png"))
open(P("fig_v5.png"), "wb").write(b"\x89PNG-original")           # copy silently failed to persist the edit
case("changed: identical bytes after 'edit' fails", None, "changed", lambda: G["cg_changed"](P("fig_v5.png"), before))
open(P("fig_v5.png"), "wb").write(b"\x89PNG-edited-8-annotations")
case("changed: edited file passes", None, None, lambda: G["cg_changed"](P("fig_v5.png"), before))
case("changed: bad hash string rejected", None, "changed", lambda: G["cg_changed"](P("fig_v5.png"), "abc"))
open(P("empty.png"), "wb").write(b"")
case("changed: empty output fails", None, "changed", lambda: G["cg_changed"](P("empty.png"), before))
case("changed: strict=False returns ok False", None, None,
     lambda: G["cg_changed"](P("fig_v4.png"), before, strict=False), lambda o: o["ok"] is False, require_ok=False)

# ---- cg_readback  (hardcoded 0.55 vs computed 0.513; non-computable reported as 1.0; transcription)
rows = []
for seed, v in zip([0, 1, 2], [0.50, 0.52, 0.519]):
    rows.append(dict(method="BDS", matched="n_eff", seed=seed, acc=v))
rows += [dict(method="BDS", matched="raw", seed=s, acc=0.0) for s in range(3)]
rows += [dict(method="kBET", matched="n_eff", seed=0, acc=float("nan"))]
df = pd.DataFrame(rows)
df.to_csv(P("class.csv"), index=False)
r = G["cg_readback"](P("class.csv"), {"column": "acc", "where": {"method": "BDS", "matched": "n_eff"}})
case("readback: list of matched values returned with sha", None, None, lambda: r,
     lambda o: len(o["value"]) == 3 and len(o["sha256"]) == 64 and abs(np.mean(o["value"]) - 0.513) < 1e-3)
case("readback: hardcoded 0.55 != readback mean 0.513 (caller compare)", None, None, lambda: True,
     lambda o: abs(np.mean(r["value"]) - 0.55) > 0.03)
case("readback: NaN metric refuses to return a number", None, "readback",
     lambda: G["cg_readback"](P("class.csv"), {"column": "acc", "where": {"method": "kBET"}}))
case("readback: NaN allowed when explicit", None, None,
     lambda: G["cg_readback"](P("class.csv"), {"column": "acc", "where": {"method": "kBET"}}, allow_nan=True))
case("readback: where matches no rows fails", None, "readback",
     lambda: G["cg_readback"](P("class.csv"), {"column": "acc", "where": {"method": "PCA"}}))
case("readback: unknown column fails", None, "readback",
     lambda: G["cg_readback"](P("class.csv"), {"column": "accuracy", "where": {"method": "BDS"}}))
case("readback: expect_rows mismatch fails", None, "readback",
     lambda: G["cg_readback"](P("class.csv"), {"column": "acc", "where": {"method": "BDS"}, "expect_rows": 3}))
case("readback: single row gives scalar", None, None,
     lambda: G["cg_readback"](P("class.csv"), {"column": "acc", "where": {"method": "BDS", "matched": "n_eff", "seed": 1}}),
     lambda o: o["value"] == 0.52)
case("readback: missing file fails", None, "readback", lambda: G["cg_readback"](P("nope.csv"), {"column": "x"}))
case("readback: empty selector fails", None, "readback", lambda: G["cg_readback"](P("class.csv"), {}))
json.dump({"a": {"b": [1, 2, {"c": 0.44}]}, "n": 10}, open(P("r.json"), "w"))
case("readback: json dotted key", None, None, lambda: G["cg_readback"](P("r.json"), {"key": "a.b.2.c"}), lambda o: o["value"] == 0.44)
case("readback: json missing key fails", None, "readback", lambda: G["cg_readback"](P("r.json"), {"key": "a.z"}))
np.savez(P("m.npz"), W=np.arange(6).reshape(2, 3))
case("readback: npz array + index", None, None, lambda: G["cg_readback"](P("m.npz"), {"key": "W", "index": [1, 2]}), lambda o: o["value"] == 5)
df.to_parquet(P("class.parquet"))
case("readback: parquet", None, None, lambda: G["cg_readback"](P("class.parquet"), {"column": "seed", "where": {"method": "kBET"}}), lambda o: o["value"] == 0)

# ---- cg_reconcile  (partition sums to 131 not 125; 43 vs 44; categories != total)
tr = {"a": range(0, 13), "b": range(13, 17), "c": range(17, 23), "d": range(23, 28), "e": range(28, 31),
      "f": range(31, 34), "g": range(34, 73), "h": range(70, 128)}      # g/h overlap on 70,71,72
case("reconcile: overlapping tranches vs stated 125", None, "reconcile", lambda: G["cg_reconcile"](total=125, partitions=tr))
good = {"a": range(0, 60), "b": range(60, 125)}
case("reconcile: disjoint tranches sum to 125", None, None, lambda: G["cg_reconcile"](total=125, partitions=good))
case("reconcile: summary says 43, files listed 44", None, "reconcile",
     lambda: G["cg_reconcile"](summary=43, files=len([f"f{i}.png" for i in range(44)])))
case("reconcile: parts do not sum to stated total", None, "reconcile", lambda: G["cg_reconcile"](total=21, parts={"x": 9, "y": 7, "z": 3}))
case("reconcile: parts sum correctly", None, None, lambda: G["cg_reconcile"](total=19, parts={"x": 9, "y": 7, "z": 3}))
case("reconcile: duplicate items flagged", None, "reconcile", lambda: G["cg_reconcile"](total=3, items=["a", "b", "b"]))
case("reconcile: single quantity is an error", None, "reconcile", lambda: G["cg_reconcile"](total=3))
case("reconcile: non-integer count rejected", None, "reconcile", lambda: G["cg_reconcile"](total=3.5, other=3))

# ---- cg_reconcile_table  (prose total != manifest; tasks missing; NaN)
import itertools
ds, ms = ["lung", "kang", "norman"], ["pca", "nmf", "vae"]
full = pd.DataFrame(list(itertools.product(ds, ms)), columns=["dataset", "model"]); full["ari"] = 0.5
exp = list(itertools.product(ds, ms))
case("table: 9 of 9 configs present", None, None, lambda: G["cg_reconcile_table"](full, expected_n=9, by=["dataset", "model"], expected_keys=exp))
short = full.drop(index=[4, 7]).reset_index(drop=True)
case("table: 2 configs missing", None, "reconcile", lambda: G["cg_reconcile_table"](short, expected_n=9, by=["dataset", "model"], expected_keys=exp))
dup = pd.concat([full, full.iloc[[0]]], ignore_index=True)
case("table: duplicated config", None, "reconcile", lambda: G["cg_reconcile_table"](dup, by=["dataset", "model"], expected_keys=exp))
nan = full.copy(); nan.loc[3, "ari"] = np.nan
case("table: NaN metric rows flagged", None, "reconcile", lambda: G["cg_reconcile_table"](nan, value_col="ari", by=["dataset", "model"]))
case("table: unbalanced groups", None, "reconcile", lambda: G["cg_reconcile_table"](short, by=["dataset"], balanced=True))
case("table: no check requested is an error", None, "reconcile", lambda: G["cg_reconcile_table"](full))

# ---- cg_scope  (verified on 1 of 3 models; 'every dataset'; partial audit as comprehensive)
case("scope: 'all models' claimed, 1 verified", None, "scope", lambda: G["cg_scope"]("all", ["scVI"], universe=["scVI", "NMF", "PCA"], noun="models"))
r2 = G["cg_scope"]("all", ["scVI"], universe=["scVI", "NMF", "PCA"], noun="models", strict=False)
case("scope: strict=False returns scoped phrase", None, None, lambda: r2, lambda o: o["ok"] is False and "1 of 3" in o["scoped_phrase"] and "NMF" in o["scoped_phrase"], require_ok=False)
case("scope: claim on dataset_A+dataset_B, verified dataset_A only", None, "scope", lambda: G["cg_scope"](["dataset_A", "dataset_B"], ["dataset_A"]))
case("scope: 87 findings claimed audited, 20 checked", None, "scope", lambda: G["cg_scope"](list(range(87)), list(range(20)), noun="findings"))
case("scope: fully verified passes", None, None, lambda: G["cg_scope"](["dataset_A"], ["dataset_A", "dataset_B"]))
case("scope: all verified on whole universe", None, None, lambda: G["cg_scope"]("all", ["a", "b"], universe=["a", "b"]),
     lambda o: "all 2" in o["scoped_phrase"])
case("scope: 'all' with no universe is an error", None, "scope", lambda: G["cg_scope"]("all", ["a"]))
case("scope: claimed item not in universe (typo)", None, "scope", lambda: G["cg_scope"](["Kamg"], ["dataset_A"], universe=["dataset_A", "dataset_B"]))

# ---- cg_render  (hardcoded values; headline not recomputed; double rounding)
tpl = "BDS accuracy at matched n_eff is {{bds.acc:.2f}} (n={{bds.n}} seeds)."
case("render: fills from dict", None, None, lambda: G["cg_render"](tpl, {"bds": {"acc": 0.51333, "n": 3}}),
     lambda o: o["text"] == "BDS accuracy at matched n_eff is 0.51 (n=3 seeds)." and o["provenance"]["bds.acc"]["value"] == 0.51333)
case("render: unresolved placeholder fails", None, "render", lambda: G["cg_render"](tpl, {"bds": {"acc": 0.5}}))
case("render: unused dict value fails (template out of sync)", None, "render", lambda: G["cg_render"](tpl, {"bds": {"acc": 0.5, "n": 3}, "extra": 9}))
case("render: unused allowed when asked", None, None, lambda: G["cg_render"](tpl, {"bds": {"acc": 0.5, "n": 3}, "extra": 9}, allow_unused=True))
case("render: float without spec fails (no silent 17 digits)", None, "render", lambda: G["cg_render"]("x={{v}}", {"v": 0.123456789}))
case("render: NaN value refuses to render", None, "render", lambda: G["cg_render"]("k={{v:.2f}}", {"v": float("nan")}))
case("render: None value refuses to render", None, "render", lambda: G["cg_render"]("k={{v}}", {"v": None}))
case("render: non-scalar leaf fails", None, "render", lambda: G["cg_render"]("k={{v}}", {"v": [1, 2]}))
case("render: bad format spec fails", None, "render", lambda: G["cg_render"]("k={{v:.2q}}", {"v": 0.5}))
case("render: typed literal 0.55 flagged under strict_literals", None, "render",
     lambda: G["cg_render"]("BDS is 0.55 at {{k}} seeds.", {"k": 3}, strict_literals=True))
case("render: literals reported but not fatal by default", None, None,
     lambda: G["cg_render"]("BDS is 0.55 at {{k}} seeds, Table 2, Fig. 3, 2026.", {"k": 3}),
     lambda o: [l["token"] for l in o["literal_numbers"]] == ["0.55"])
rb = G["cg_readback"](P("class.csv"), {"column": "acc", "where": {"method": "BDS", "matched": "n_eff", "seed": 1}})
case("render: readback leaf carries source sha into provenance", None, None, lambda: G["cg_render"]("acc={{a:.2f}}", {"a": rb}),
     lambda o: o["provenance"]["a"]["source"]["sha256"] == rb["sha256"] and o["text"] == "acc=0.52")
raw = {"mig": 0.2549, "sap": 0.1281}
case("render: ratio computed from raw not rounded (double-rounding guard)", None, None,
     lambda: G["cg_render"]("ratio={{r:.2f}}", {"r": raw["mig"] / raw["sap"]}), lambda o: o["text"] == "ratio=1.99")

# ---- cg_stale  (stale pre-remediation outputs; outline stale artifact; stale memory)
code, out = P("metric.py"), P("svcca.csv")
open(out, "w").write("x\n1\n"); open(code, "w").write("print(1)\n")
now = time.time()
os.utime(out, (now - 1000, now - 1000)); os.utime(code, (now - 10, now - 10))   # code fixed AFTER output was made
case("stale: output older than fixed code", None, "stale", lambda: G["cg_stale"]([out], [code]))
os.utime(out, (now, now))
case("stale: regenerated output passes", None, None, lambda: G["cg_stale"]([out], [code]))
case("stale: missing output fails", None, "stale", lambda: G["cg_stale"]([P("gone.csv")], [code]))
case("stale: no reference given fails", None, "stale", lambda: G["cg_stale"]([out]))
repo = P("repo"); os.makedirs(repo)
run = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True,
                                env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
run("init", "-q"); open(os.path.join(repo, "f.py"), "w").write("1\n"); run("add", "."); run("commit", "-qm", "remediation")
old_out = os.path.join(repo, "old.csv"); open(old_out, "w").write("1\n"); os.utime(old_out, (now - 86400, now - 86400))
case("stale: output predates remediation commit", None, "stale", lambda: G["cg_stale"]([old_out], since_commit="HEAD", repo=repo))
new_out = os.path.join(repo, "new.csv"); open(new_out, "w").write("1\n")
case("stale: output after commit passes", None, None, lambda: G["cg_stale"]([new_out], since_commit="HEAD", repo=repo))
case("stale: unknown commit fails", None, "stale", lambda: G["cg_stale"]([new_out], since_commit="deadbeef0", repo=repo))
d = P("outdir"); os.makedirs(d); open(os.path.join(d, "a"), "w").write("1"); open(os.path.join(d, "b"), "w").write("1")
os.utime(os.path.join(d, "a"), (now - 5000, now - 5000))
case("stale: directory is as old as its oldest file", None, "stale", lambda: G["cg_stale"]([d], [code]))

# ---- cg_contradictions
seeds = pd.DataFrame({"model": ["MOFA+"] * 3, "seed": ["s0", "s1", "s2"], "pearson_r2": [0.166, -0.030, -3.950]})
case("contradictions: '2 of 3 seeds positive' vs one positive -> number 2 unmatched", None, "contradictions",
     lambda: G["cg_contradictions"]("MOFA+ baseline PearsonR2 is positive for 2 of 3 seeds (0.166, -0.030, -3.950).", seeds))
case("contradictions: wrong sign/abs-only match flagged", None, "contradictions",
     lambda: G["cg_contradictions"]("MOFA+ PearsonR2 reaches 3.950 on one seed.", seeds))
case("contradictions: correct values pass", None, None,
     lambda: G["cg_contradictions"]("MOFA+ PearsonR2 is 0.166 on s0 and -0.030 on s1.", seeds, ignore_numbers=[]))
cov = pd.DataFrame({"method": ["PCA", "NMF", "MOFA+", "RcppML"], "cov_acc": [0.44, 0.50, 0.57, 0.30]})
case("contradictions: range 0.44-0.57 contradicted by 0.30 row", None, "contradictions",
     lambda: G["cg_contradictions"]("All covariates fall in 0.44-0.57 across methods.", cov))
case("contradictions: range claim consistent passes", None, None,
     lambda: G["cg_contradictions"]("PCA, NMF and MOFA+ fall in 0.44-0.57.", cov.iloc[:3].reset_index(drop=True)))
rank = pd.DataFrame({"dataset": ["dataset_A", "dataset_B", "dataset_C CC"], "method": ["PCA"] * 3, "rank": [2, 3, 9]})
case("contradictions: 'top-3 on every dataset' flagged vs rank 9/28", None, "contradictions",
     lambda: G["cg_contradictions"]("PCA is top-3 on every dataset.", rank, fail_on_quantifiers=True))
rq = G["cg_contradictions"]("PCA is top-3 on every dataset.", rank, strict=False)
case("contradictions: quantifier sentence is returned for recompute", None, None, lambda: rq,
     lambda o: len(o["quantifier_claims"]) == 1, require_ok=False)
lam = pd.DataFrame({"lambda": ["1e-3", "1e-2", "1e-1"], "paga": [0.71, 0.64, 0.58], "ari": [0.30, 0.33, 0.35]})
case("contradictions: best PAGA attributed to wrong lambda row", None, "contradictions",
     lambda: G["cg_contradictions"]("At 1e-1 the PAGA score is 0.71.", lam))
case("contradictions: value attributed to correct row passes", None, None,
     lambda: G["cg_contradictions"]("At 1e-3 the PAGA score is 0.71.", lam))
case("contradictions: count/percentage mismatch (56% vs 20%)", None, "contradictions",
     lambda: G["cg_contradictions"]("Panel C: 56% of cells are flagged.", pd.DataFrame({"flag_frac": [0.20, 0.21, 0.19]})))
case("contradictions: percentage written from fractions passes", None, None,
     lambda: G["cg_contradictions"]("Panel C: 20% of cells are flagged.", pd.DataFrame({"flag_frac": [0.20, 0.21, 0.19]})))
case("contradictions: empty summary fails", None, "contradictions", lambda: G["cg_contradictions"]("  ", seeds))


# ---- reason checks: known-bad cases must fail for the INTENDED reason, not incidentally
C = lambda txt, df_: G["cg_contradictions"](txt, df_, strict=False)
case("reason: flagged because '2' matches no value; count claim listed", None, None,
     lambda: C("MOFA+ baseline PearsonR2 is positive for 2 of 3 seeds (0.166, -0.030, -3.950).", seeds),
     lambda o: [u["number"] for u in o["unmatched"]] == ["2"] and o["count_claims"] == ["2 of 3"], require_ok=False)
case("reason: sign/abs-only match", None, None, lambda: C("MOFA+ PearsonR2 reaches 3.950 on one seed.", seeds),
     lambda o: [u["number"] for u in o["sign_mismatch"]] == ["3.950"], require_ok=False)
case("reason: range violation names the 0.30 row", None, None, lambda: C("All covariates fall in 0.44-0.57 across methods.", cov),
     lambda o: o["range_violations"] and o["range_violations"][0]["outside"][0][0] == "RcppML", require_ok=False)
case("reason: inconsistent (value exists on another row)", None, None, lambda: C("At 1e-1 the PAGA score is 0.71.", lam),
     lambda o: [u["number"] for u in o["inconsistent"]] == ["0.71"], require_ok=False)
case("reason: overlap named in problems", None, None, lambda: G["cg_reconcile"](total=125, partitions=tr, strict=False),
     lambda o: any("overlap" in p for p in o["problems"]) and o["counts"]["sum(partition sizes)"] == 131 and o["counts"]["len(union(partitions))"] == 128, require_ok=False)
case("reason: counts reported", None, None, lambda: G["cg_reconcile"](summary=43, files=44, strict=False),
     lambda o: o["counts"] == {"summary": 43, "files": 44}, require_ok=False)
case("reason: stale margin negative", None, None, lambda: (os.utime(out, (now - 1000, now - 1000)), G["cg_stale"]([out], [code], strict=False))[1],
     lambda o: o["rows"][0]["margin_vs_inputs_s"] < -900, require_ok=False)

# ---- cg_receipt
ok_r = G["cg_readback"](P("class.csv"), {"column": "acc", "where": {"method": "BDS", "matched": "n_eff", "seed": 1}})
case("receipt: assembles from passing checks", None, None, lambda: G["cg_receipt"]("BDS n_eff acc is 0.52", [ok_r], not_checked=["other seeds"]),
     lambda o: "sha256:" in o["text"] and "not checked: other seeds" in o["text"])
case("receipt: refuses empty evidence", None, "receipt", lambda: G["cg_receipt"]("done", []))
bad_ev = G["cg_scope"]("all", ["a"], universe=["a", "b"], strict=False)
case("receipt: refuses a failed check", None, "receipt", lambda: G["cg_receipt"]("all verified", [bad_ev]))
case("receipt: refuses hand-typed evidence", None, "receipt", lambda: G["cg_receipt"]("100 passed", [{"summary": "100 passed"}]))

n_ok = sum(r["ok"] for r in RES); n_bad = len(RES) - n_ok
json.dump({"passed": n_ok, "failed": n_bad, "cases": RES}, open("claim-gate_test_results.json", "w"), indent=1)
print("passed", n_ok, "failed", n_bad)
for r in RES:
    if not r["ok"]:
        print("FAIL", r)
sys.exit(1 if n_bad else 0)
