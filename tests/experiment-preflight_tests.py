"""Tests for experiment-preflight kernel.py against synthetic fixtures that reproduce known failure patterns.

Run: python experiment-preflight_tests.py [path/to/kernel.py]   (default: ./kernel.py or pfwork/kernel.py)
Each test reproduces a known failure pattern. A known-bad case must raise
the specific exception; the matching known-good case must pass.
"""
import json
import os
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd

KPATH = sys.argv[1] if len(sys.argv) > 1 else next(p for p in ("kernel.py", "pfwork/kernel.py") if os.path.exists(p))
exec(open(KPATH).read(), globals())

TESTS = []


def test(incidents):
    def deco(fn):
        TESTS.append((fn.__name__, incidents, fn))
        return fn
    return deco


def raises(name, fn, *a, **k):
    try:
        fn(*a, **k)
    except pf_exc(name) as e:
        return str(e)
    raise AssertionError("expected %s, nothing raised" % name)


rng = np.random.default_rng(0)


@test([])
def t_grouped_split_donor_leak():
    donors = np.repeat([f"D{i}" for i in range(14)], 50)           # 14 donors x 50 cells
    ids = np.arange(len(donors))
    groups = pd.Series(donors, index=ids)
    perm = rng.permutation(ids)
    tr, te = perm[:560], perm[560:]                                 # random 80/20 cell-level split
    msg = raises("SplitLeakageError", pf_check_grouped_split, tr, te, groups)
    assert "test units also occur in train" in msg
    res = pf_check_grouped_split(tr, te, groups, strict=False)
    assert res["ok"] is False and res["n_leaky_test_samples"] > 0
    held = [i for i in ids if donors[i] in ("D0", "D1", "D2")]
    rest = [i for i in ids if donors[i] not in ("D0", "D1", "D2")]
    assert pf_check_grouped_split(rest, held, groups)["ok"]
    raises("MissingGroupError", pf_check_grouped_split, [0, 1], [999999], groups)


@test([])
def t_id_collision_across_sources():
    df = pd.DataFrame({"patient": ["1", "2", "3", "1", "2", "4"], "source": ["A", "A", "A", "B", "B", "B"]})
    raises("IdCollisionError", pf_check_id_collisions, df, "patient", "source")
    df["patient"] = df["source"] + ":" + df["patient"]
    assert pf_check_id_collisions(df, "patient", "source")["ok"]


@test([])
def t_nesting_batch_sets_labels():
    df = pd.DataFrame({"Batch": list("AABBCCDD"), "Response": ["r", "r", "n", "n", "r", "r", "n", "n"],
                       "CellType": ["t", "b", "t", "b", "t", "b", "t", "b"]})
    msg = raises("NestedFactorError", pf_check_nesting, df, ["Batch", "Response", "CellType"])
    assert "Batch>Response" in msg and "CellType" not in msg.split("acknowledge")[0]
    assert pf_check_nesting(df, ["Batch", "Response", "CellType"], allow=[("Batch", "Response")])["ok"]
    assert pf_check_nesting(df, ["Batch", "CellType"])["ok"]


@test([])
def t_selection_winners_curse():
    raises("SelectionBiasError", pf_check_selection, [0, 1, 2, 3, 4], [0, 1, 2, 3, 4])
    assert pf_check_selection([0, 1, 2], [10, 11, 12, 13, 14])["ok"]
    raises("InputError", pf_check_selection, [], [1])


@test([])
def t_matched_arms_fixed_vs_rowmax():
    arms = {"critic": {"lambda_policy": "fixed lambda=20", "tuning_budget": 8, "latent_dim": 20},
            "discriminator": {"lambda_policy": "row-max over lambda grid", "tuning_budget": 8, "latent_dim": 20}}
    msg = raises("UnmatchedArmsError", pf_check_matched_arms, arms)
    assert "lambda_policy" in msg
    arms["discriminator"]["lambda_policy"] = "fixed lambda=20"
    assert pf_check_matched_arms(arms)["ok"]
    arms["discriminator"]["latent_dim"] = 10
    raises("UnmatchedArmsError", pf_check_matched_arms, arms)
    assert pf_check_matched_arms(arms, varied=["latent_dim"])["ok"]


@test([])
def t_coverage_unequal_datasets():
    rows = [(m, d, 0.5) for m in ["scVI", "PCA"] for d in "ABCDE"] + [("scGPT", d, 0.6) for d in "ABC"]
    df = pd.DataFrame(rows, columns=["model", "dataset", "score"])
    msg = raises("CoverageImbalanceError", pf_check_coverage, df, "dataset", "model")
    assert "3/5" in msg
    res = pf_check_coverage(df, "dataset", "model", strict=False)
    assert res["common_units"] == ["A", "B", "C"]
    df2 = df[df.model != "scGPT"]
    assert pf_check_coverage(df2, "dataset", "model")["ok"]
    df3 = df2.copy()
    df3.loc[df3.index[0], "score"] = np.nan                         # non-computable cell must not count as covered
    raises("CoverageImbalanceError", pf_check_coverage, df3, "dataset", "model", value_col="score")


@test([])
def t_identity_baseline_not_noop():
    x = rng.poisson(2.0, size=(1000, 300)).astype(float)

    def leaky_zero_intensity(m):                                   # 'intensity=0' still perturbs some entries
        out = m.copy()
        idx = rng.choice(m.size, 2494, replace=False)
        out.flat[idx] += 0.403
        return out
    msg = raises("NotAnIdentityError", pf_check_identity_baseline, leaky_zero_intensity, x, atol=1e-9)
    assert "2494" in msg
    assert pf_check_identity_baseline(lambda m: m.copy(), x, atol=1e-9)["ok"]
    import scipy.sparse as sp
    assert pf_check_identity_baseline(lambda m: m.copy(), sp.csr_matrix(x))["ok"]

    def mutating(m):
        m[0, 0] += 1.0
        return m
    raises("NotAnIdentityError", pf_check_identity_baseline, mutating, x.copy())
    raises("NotAnIdentityError", pf_check_identity_baseline, lambda m: m[:, :10], x)   # shape change (genes dropped)


@test([])
def t_n_collapse_along_noise_axis():
    n = {0.0: 52055, 0.5: 20000, 1.0: 4000, 2.0: 375}
    df = pd.DataFrame([(ds, lvl) for ds in ("lung", "immune") for lvl, k in n.items() for _ in range(k // 50)],
                      columns=["dataset", "noise"])               # counts scaled /50 to keep the fixture small
    msg = raises("SampleSizeError", pf_check_n_per_condition, df, ["dataset", "noise"], min_n=5, axis_col="noise", max_ratio=10)
    assert "collapses" in msg and "148.7x" in msg    # 1041 / 7 cells after /50 scaling
    assert pf_check_n_per_condition(df, ["dataset", "noise"], min_n=5, axis_col="noise", max_ratio=200)["ok"]
    raises("SampleSizeError", pf_check_n_per_condition, df, ["dataset", "noise"], min_n=100)
    df_missing = df[~((df.dataset == "lung") & (df.noise == 2.0))]
    raises("SampleSizeError", pf_check_n_per_condition, df_missing, ["dataset", "noise"], min_n=5)


@test([])
def t_cost_pilot_vs_production():
    pilot = {"epochs": 30, "batch": 256, "model": "vae"}
    prod = {"epochs": 500, "batch": 256, "model": "vae"}
    msg = raises("CostEstimateError", pf_estimate_cost, 600, 1, 1000, 2.0, pilot, prod)
    assert "epochs" in msg
    raises("CostEstimateError", pf_estimate_cost, 600, 1, 1000)           # no settings supplied
    r = pf_estimate_cost(600, 1, 1000, 2.0, prod, dict(prod), cores=4)
    assert abs(r["estimate_s"] - 600000) < 1e-6 and abs(r["cpu_hours"] - 600000 * 4 / 3600) < 1e-6
    assert r["recommended_walltime_s"] % 60 == 0 and r["budget_s"] == 1200000
    # 4 h wall vs 5.5 h/task measured need
    msg = raises("CostEstimateError", pf_estimate_cost, 19800, 1, 40, 1.0, prod, dict(prod), 4 * 3600, 1)
    assert "walltime" in msg
    assert pf_estimate_cost(19800, 1, 40, 1.25, prod, dict(prod), 12 * 3600, 1)["ok"]
    raises("InputError", pf_estimate_cost, 0, 1, 10)


@test([])
def t_manifest_dropped_flag():
    intended = {"epochs": 500, "early_stopping": True, "seed": 1}
    good = [f"python run.py --dataset d{i} --epochs 500 --early-stopping --seed 1" for i in range(90)]
    assert pf_check_manifest(good, intended, expected_rows=90)["ok"]
    bad = [g.replace("--epochs 500 ", "") for g in good]                  # rewrite dropped the flag -> default 150 epochs
    msg = raises("ManifestMismatchError", pf_check_manifest, bad, intended)
    assert "epochs" in msg and "90" in msg
    wrong = [g.replace("--epochs 500", "--epochs 150") for g in good]
    raises("ManifestMismatchError", pf_check_manifest, wrong, intended)
    raises("ManifestMismatchError", pf_check_manifest, good[:80], intended, expected_rows=90)    # count reconciliation (924 vs 822 style)
    rows = [{"epochs": 500, "early_stopping": "false", "seed": 1}]
    res = pf_check_manifest(rows, {"epochs": 500, "early_stopping": False, "seed": 1}, strict=False)
    assert res["ok"] and any("string" in w for w in res["warnings"])      # 'false' string passes the diff but warns
    raises("ManifestMismatchError", pf_check_manifest, [{"epochs": 500, "seed": 1}], intended)  # seed/flag absent


@test([])
def t_timing_contention_and_warmup():
    raises("TimingConfoundError", pf_check_timing_repeats, [210, 89, 90, 88])      # contaminated first run
    raises("TimingConfoundError", pf_check_timing_repeats, [35.5, 0.9, 0.8, 0.9])  # JIT in first call
    raises("TimingConfoundError", pf_check_timing_repeats, [88, 150, 90, 210])     # contention spread
    r = pf_check_timing_repeats([89, 90, 88, 91])
    assert r["ok"] and not r["warmup_suspected"]
    raises("InputError", pf_check_timing_repeats, [1.0, 1.1])


@test([])
def t_metric_known_answer():
    def corr_good(y, p):
        y, p = np.asarray(y, float), np.asarray(p, float)
        return float(np.corrcoef(y, p)[0, 1])

    def corr_nan_small_n(y, p, k=3):                              # silently NaN below a hidden size bound
        return float("nan") if len(y) < 2 * k else corr_good(y, p)

    def corr_flipped(y, p):                                       # orientation flipped (lower-is-better)
        return 1.0 - corr_good(y, p)

    y = [1, 2, 3, 4]
    cases = [{"name": "perfect", "args": (y, y), "expected": 1.0},
             {"name": "reversed", "args": (y, y[::-1]), "expected": -1.0},
             {"name": "small_n", "args": ([1, 2, 3], [1, 2, 3]), "expected": 1.0}]
    assert pf_check_metric_known_answer(corr_good, cases)["ok"]
    msg = raises("MetricMismatchError", pf_check_metric_known_answer, corr_nan_small_n, cases)
    assert "NaN" in msg and "small_n" in msg
    msg = raises("MetricMismatchError", pf_check_metric_known_answer, corr_flipped, cases)
    assert "perfect" in msg
    raises("MetricMismatchError", pf_check_metric_known_answer, lambda *a: (_ for _ in ()).throw(ValueError("k>=n/2")), cases)
    raises("InputError", pf_check_metric_known_answer, corr_good, cases[:1])
    raises("InputError", pf_check_metric_known_answer, corr_good, [cases[0], dict(cases[0], name="dup")])  # constant answers


@test([])
def t_inclusion_species_filter():
    genes = ["ENSG%05d" % i for i in range(710)] + ["ENSMUSG%05d" % i for i in range(290)]   # 29% mouse
    df = pd.DataFrame({"gene": genes})
    df["species"] = np.where(df.gene.str.startswith("ENSMUSG"), "mouse", "human")
    msg = raises("InclusionFilterError", pf_check_inclusion, df, "species", ["human"])
    assert "29.0%" in msg
    assert pf_check_inclusion(df[df.species == "human"], "species", ["human"])["ok"]
    assert pf_check_inclusion(df, "species", ["human"], max_frac_outside=0.30)["ok"]


@test([])
def t_replicates_identical():
    same = [np.arange(5.0)] * 5                                     # seed never reaches the RNG
    raises("ReplicatesIdenticalError", pf_check_replicates_differ, same)
    assert pf_check_replicates_differ([np.arange(5.0), np.arange(5.0) + 0.1, np.arange(5.0) + 0.2])["ok"]
    r = pf_check_replicates_differ([np.zeros(3), np.zeros(3), np.ones(3)])
    assert r["ok"] and r["warnings"]


@test([])
def t_plan_predeclaration():
    good = {"question": "Does method A beat B?", "primary_outcome": "mean ARI on held-out donors", "unit_of_analysis": "donor",
            "split_unit": "donor", "selection_rule": "lambda chosen on seeds 0-4; reported on seeds 5-14",
            "n_comparisons": 3, "multiple_comparison_correction": "Holm", "data_filters": "human genes only"}
    assert pf_check_plan(good)["ok"]
    raises("PlanIncompleteError", pf_check_plan, {k: v for k, v in good.items() if k != "primary_outcome"})
    raises("PlanIncompleteError", pf_check_plan, dict(good, primary_outcome=["ARI", "NMI", "ASW"]))
    raises("PlanIncompleteError", pf_check_plan, {k: v for k, v in good.items() if k != "multiple_comparison_correction"})
    raises("PlanIncompleteError", pf_check_plan, dict(good, selection_rule="TBD"))
    assert pf_check_plan(dict(good, n_comparisons=1, multiple_comparison_correction=None))["ok"]


@test([])
def t_lifecycle_go_nogo():
    plan = {"question": "q?", "primary_outcome": "ARI on held-out donors", "unit_of_analysis": "donor", "split_unit": "donor",
            "selection_rule": "select on seeds 0-4, report 5-14", "n_comparisons": 1, "data_filters": "human only"}
    with tempfile.TemporaryDirectory() as root:
        raises("PlanIncompleteError", pf_new, "e01", {"question": "x"}, root)
        rec = pf_new("e01_demo", plan, root)
        assert os.path.exists(os.path.join(root, "experiments", "e01_demo", "PREFLIGHT.md"))
        v = pf_verdict(rec)
        assert v["go"] is False and "PF-03" in v["blocking"] and "PF-01" not in v["blocking"]
        raises("EvidenceRequiredError", pf_answer, rec, "PF-03", "pass", "ok")
        raises("EvidenceRequiredError", pf_answer, rec, "PF-04", "n/a", "n/a")
        raises("InputError", pf_answer, rec, "PF-99", "pass", "a long enough evidence string here")
        raises("InputError", pf_answer, rec, "PF-03", "maybe", "a long enough evidence string here")
        for it in pf_items():
            if it["severity"] == "blocking" and it["id"] not in rec["answers"]:
                pf_answer(rec, it["id"], "pass", "checked in notebook run 12: command + output reviewed (%s)" % it["id"])
        assert pf_verdict(rec)["go"] is True
        pf_answer(rec, "PF-19", "fail", "manifest diff shows --epochs absent in 90 of 90 rows")
        v = pf_verdict(rec)
        assert v["go"] is False and v["blocking"] == ["PF-19"]
        pf_answer(rec, "PF-19", "pass", "re-diffed after regenerating the manifest: 90/90 rows carry --epochs 500")
        assert pf_verdict(rec)["go"] is True and len(rec["answers"]["PF-19"]["history"]) == 2   # pass, fail, pass
        pf_answer(rec, "PF-04", "n/a", "single-source dataset; no merge of sources occurs")
        assert pf_verdict(rec)["n_na"] >= 1
        # auto-check recorded as answer
        res = pf_check_selection([0, 1], [0, 1], strict=False)
        pf_record_check(rec, "PF-06", res)
        assert rec["answers"]["PF-06"]["status"] == "fail" and pf_verdict(rec)["go"] is False
        pf_record_check(rec, "PF-06", pf_check_selection([0, 1], [2, 3]))
        assert pf_verdict(rec)["go"] is True
        # plan edited after declaration -> NO-GO
        rec["plan"]["primary_outcome"] = "NMI (changed after seeing results)"
        v = pf_verdict(rec)
        assert v["go"] is False and v["plan_changed"] is True
        rec["plan"]["primary_outcome"] = plan["primary_outcome"]
        assert pf_verdict(rec)["go"] is True
        paths = pf_save(rec)
        again = pf_load("e01_demo", root)
        assert again["answers"]["PF-19"]["status"] == "pass" and again["verdict"]["go"] is True
        assert "**Verdict: GO**" in open(paths["md"]).read()
        raises("RecordExistsError", pf_new, "e01_demo", plan, root)
        rec2 = pf_new("e01_demo", plan, root, overwrite=True)
        archived = [f for f in os.listdir(os.path.join(root, "experiments", "e01_demo")) if f.startswith("preflight.2")]
        assert len(archived) == 1 and rec2["answers"].keys() == {"PF-01", "PF-02"}
        raises("RecordNotFoundError", pf_load, "nope", root)
        raises("InputError", pf_new, "../escape", plan, root)


@test([])
def t_domain_pack_plugs_in():
    sc = [{"id": "SC-01", "area": "species", "question": "q", "severity": "blocking", "applies_if": "always",
           "how_to_check": "h", "auto": None},
          {"id": "SC-02", "area": "x", "question": "q", "severity": "minor", "applies_if": "a", "how_to_check": "h", "auto": None}]
    plan = {"question": "q?", "primary_outcome": "ARI", "unit_of_analysis": "donor", "split_unit": "donor",
            "selection_rule": "seeds", "n_comparisons": 1, "data_filters": "human"}
    with tempfile.TemporaryDirectory() as root:
        rec = pf_new("e02", plan, root, extra_items=sc)
        for it in pf_items():
            if it["severity"] == "blocking" and it["id"] not in rec["answers"]:
                pf_answer(rec, it["id"], "pass", "verified with the helper and logged evidence for %s" % it["id"])
        assert pf_verdict(rec)["go"] is False                               # generic passes but SC-01 unanswered
        assert pf_verdict(rec, extra_items=sc)["blocking"] == ["SC-01"]
        pf_answer(rec, "SC-01", "pass", "all kept genes are human (checked symbol prefixes)", extra_items=sc)
        assert pf_verdict(rec, extra_items=sc)["go"] is True
        rec_b = pf_new("e03", plan, root)
        raises("InputError", pf_answer, rec_b, "SC-01", "pass", "evidence that is long enough here")   # not registered
        raises("ItemSchemaError", pf_verdict, rec_b, [dict(sc[0], severity="huge")])
        raises("ItemSchemaError", pf_verdict, rec_b, [dict(sc[0], id="PF-01")])
        assert [o["id"] for o in pf_open(rec, sc)][:1] != []                  # open items listed


@test([])
def t_items_schema_and_auto_helpers_exist():
    items = pf_items()
    assert len(items) == len({i["id"] for i in items}) >= 20
    for i in items:
        assert set(i) == set(PF_ITEM_KEYS), i["id"]
        assert i["id"].startswith("PF-") and i["severity"] in PF_SEVERITIES
        assert i["auto"] is None or callable(globals().get(i["auto"])), i["id"]
    assert pf_validate_items(items)


@test([])
def t_strict_false_returns_result():
    res = pf_check_selection([1], [1], strict=False)
    assert res["ok"] is False and res["item"] == "PF-06" and "summary" in res
    assert issubclass(pf_exc("SelectionBiasError"), pf_exc("PreflightError"))


def main():
    passed = failed = 0
    log = []
    for name, inc, fn in TESTS:
        try:
            fn()
            passed += 1
            log.append({"test": name, "incidents": inc, "status": "PASS"})
            print("PASS", name, inc)
        except Exception:
            failed += 1
            log.append({"test": name, "incidents": inc, "status": "FAIL", "trace": traceback.format_exc()})
            print("FAIL", name, inc)
            traceback.print_exc()
    covered = sorted({i for _, inc, _ in TESTS for i in inc})
    print(json.dumps({"passed": passed, "failed": failed, "incidents_reproduced": covered}))
    with open("experiment-preflight_test_results.json", "w") as f:
        json.dump({"passed": passed, "failed": failed, "incidents_reproduced": covered, "tests": log}, f, indent=2)
    return failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
