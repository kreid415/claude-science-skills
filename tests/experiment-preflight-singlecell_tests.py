"""Tests for experiment-preflight-singlecell sidecar (scpf_). Each test: known-bad must raise, known-good must pass.
Fixtures reproduce known failure patterns."""
import sys, traceback
import numpy as np, pandas as pd, scipy.sparse as sp

exec(open(sys.argv[1] if len(sys.argv) > 1 else "scpf_kernel.py").read())

RESULTS = []

def case(name, incident, fn, expect_fail):
    """expect_fail=True: fn must raise ValueError/KeyError; False: must return ok=True."""
    try:
        r = fn()
        passed = (not expect_fail) and r["ok"]
        msg = "ok" if passed else "expected failure but passed"
    except (ValueError, KeyError) as e:
        passed = expect_fail
        msg = ("raised: " + str(e)[:110]) if passed else "unexpected raise: " + str(e)[:160]
    except Exception:
        passed = False
        msg = "CRASH " + traceback.format_exc()[-200:]
    RESULTS.append((passed, name, incident, "bad" if expect_fail else "good", msg))

rng = np.random.default_rng(0)

# ---------- donor-level labels / collisions / leakage [238, 239, 306, 266]
def make_obs():
    rows = []
    # cohort A: patients P1..P3; cohort B: P1..P3 reused ids with different response
    for coh, resp in (("A", {"P1": "R", "P2": "NR", "P3": "R"}), ("B", {"P1": "NR", "P2": "R", "P3": "NR"})):
        for p, r in resp.items():
            for s in (1, 2):
                for _ in range(20):
                    rows.append({"cohort": coh, "Patient": p, "Batch": f"{coh}.{p}.t{s}", "Response": r})
    for k in range(4, 10):   # unique, non-colliding patients
        for s in (1, 2):
            for _ in range(20):
                rows.append({"cohort": "A", "Patient": f"P{k}", "Batch": f"A.P{k}.t{s}", "Response": "R" if k % 2 else "NR"})
    o = pd.DataFrame(rows)
    o["pid"] = o.cohort + "." + o.Patient
    return o
obs = make_obs()
obs_nocoll = obs.copy()
cell_split = pd.Series(np.where(rng.random(len(obs)) < 0.2, "test", "train"), index=obs.index)
donor_split = obs.pid.map({p: ("test" if i % 5 == 0 else "train") for i, p in enumerate(sorted(obs.pid.unique()))})
obs["rand_split"] = cell_split.values
obs["grp_split"] = donor_split.values

case("random cell split on donor-level label", "",
     lambda: scpf_check_donor_split(obs, ["Response"], "pid", split_col="rand_split"), True)
case("bare Patient id collides across cohorts (different Response)", "",
     lambda: scpf_check_donor_split(obs, ["Response"], "Patient", cohort_col="cohort"), True)
case("bare Patient id collides, no cohort_col given -> conflicting labels caught", "",
     lambda: scpf_check_donor_split(obs, ["Response"], "Patient"), True)
case("composite key + donor-grouped split", "",
     lambda: scpf_check_donor_split(obs, ["Response"], "pid", cohort_col="cohort", sample_col="Batch", split_col="grp_split"), False)
case("sample maps to >1 donor", "",
     lambda: scpf_check_donor_split(obs.assign(Batch="S0"), ["Response"], "pid", sample_col="Batch"), True)
case("split leakage direct", "", lambda: scpf_check_split_leakage(obs, "pid", "rand_split"), True)
case("split leakage grouped is clean", "", lambda: scpf_check_split_leakage(obs, "pid", "grp_split"), False)
case("missing donor column raises KeyError", "", lambda: scpf_check_donor_split(obs, ["Response"], "nope"), True)

# nesting [266]
case("Batch determines Response (nested pair counted)", "",
     lambda: scpf_check_nesting(obs, ["Batch", "Response"]), True)
case("non-nested pair passes", "",
     lambda: scpf_check_nesting(pd.DataFrame({"a": list("xy" * 15), "b": list("pqr" * 10)}), ["a", "b"]), False)

# split function guarantees [304, 305]
def shuffle_split(g, seed):
    r = np.random.default_rng(seed); u = np.unique(g); r.shuffle(u)
    n = len(g); test = set(); acc = 0
    for x in u:
        if acc < 0.2 * n:
            test.add(x); acc += (g == x).sum()
    return np.isin(g, list(test))
grp_eq = np.repeat(np.arange(10), 20)
def cell_level_split(g, seed):
    return np.random.default_rng(seed).random(len(g)) < 0.2
grp = np.repeat(np.arange(10), [16, 23, 16, 30, 20, 12, 25, 18, 22, 14])
case("cell-level split straddles groups", "", lambda: scpf_check_split_fn(cell_level_split, grp), True)
case("grouped shuffle split, loose tol", "", lambda: scpf_check_split_fn(shuffle_split, grp_eq), False)
sizes_bad = np.repeat([0, 1, 2], [16, 23, 16])
case("greedy split not optimal on [16,23,16] when optimality claimed", "",
     lambda: scpf_check_split_fn(lambda g, s: np.isin(g, [1]), sizes_bad, test_frac=0.2, tol=1.0, check_optimal=True), True)
case("optimal partition on [16,23,16]", "",
     lambda: scpf_check_split_fn(lambda g, s: np.isin(g, [0]), sizes_bad, test_frac=0.2, tol=1.0, check_optimal=True), False)

# species [342, 454, 456]
human = ["ACTB", "GAPDH", "C1orf112", "MT-CO1", "HLA-DRB1"] * 40
mixed = human[:142] + ["Actb", "Gapdh", "Xist", "Gm12345"] * 15 + ["mt-Co1"] * 8   # ~29% mouse-style
case("29% mouse symbols in human panel", "", lambda: scpf_check_species(mixed, "human"), True)
case("mouse Ensembl ids configured as human", "", lambda: scpf_check_species([f"ENSMUSG{i:011d}" for i in range(500)], "human"), True)
case("human symbols incl. C1orf pass", "", lambda: scpf_check_species(human, "human"), False)
case("human Ensembl ids with version pass", "", lambda: scpf_check_species([f"ENSG{i:011d}.5" for i in range(300)], "human"), False)
case("mouse panel passes expected=mouse", "", lambda: scpf_check_species(["Actb", "Gapdh", "Xist", "Gm1"], "mouse"), False)
case("numeric var_names cannot be classified", "", lambda: scpf_check_species([str(i) for i in range(100)], "human"), True)
case("species via AnnData-like var_names", "",
     lambda: scpf_check_species(type("A", (), {"var_names": pd.Index(mixed)})(), "human"), True)

# vocab coverage [454, 456]
vocab = [f"ENSG{i:011d}" for i in range(1000)]
case("mouse ensembl vs human vocab 0% overlap", "", lambda: scpf_check_vocab_coverage([f"ENSMUSG{i:011d}" for i in range(200)], vocab, model="UCE"), True)
case("case mismatch is diagnosed and fails", "", lambda: scpf_check_vocab_coverage(["actb", "gapdh", "tp53"], ["ACTB", "GAPDH", "TP53"]), True)
case("full coverage passes", "", lambda: scpf_check_vocab_coverage(vocab[:300], vocab), False)

# noop perturbation [443, 430, 461]
raw = rng.poisson(1.0, (200, 50)).astype(float)
pert_bad = raw.copy(); idx = rng.choice(raw.size, 25, replace=False); pert_bad.flat[idx] += 0.4
case("intensity=0 differs in 25 entries (dense)", "", lambda: scpf_check_noop_perturbation(raw, pert_bad), True)
case("intensity=0 differs (sparse)", "", lambda: scpf_check_noop_perturbation(sp.csr_matrix(raw), sp.csr_matrix(pert_bad)), True)
case("intensity=0 drops cells (shape change)", "", lambda: scpf_check_noop_perturbation(raw, raw[:30]), True)
case("exact identity passes (dense vs sparse)", "", lambda: scpf_check_noop_perturbation(raw, sp.csr_matrix(raw)), False)

# cell counts [431, 430]
tab = pd.DataFrame({"dataset": ["dataset_D"] * 4 + ["dataset_A"] * 4, "noise": [0, .25, .5, 1] * 2,
                    "n_cells": [52055, 9000, 1200, 375, 20000, 19500, 19000, 18000],
                    "median_genes": [37, 30, 20, 14, 800, 790, 780, 760]})
case("cells collapse 140x along noise axis", "", lambda: scpf_check_cell_counts(tab, "noise", group_cols="dataset"), True)
case("median genes/cell collapse 37->14 (extra_cols)", "",
     lambda: scpf_check_cell_counts(tab[tab.dataset == "dataset_A"].assign(median_genes=[37, 30, 20, 14]), "noise", extra_cols=["median_genes"], max_ratio=2.0), True)
case("stable counts pass", "", lambda: scpf_check_cell_counts(tab[tab.dataset == "dataset_A"], "noise", extra_cols=["median_genes"]), False)

# empty cells [435, 460]
Xd = rng.poisson(0.05, (500, 30)).astype(float); Xd[3] = 0; Xd[77] = 0
case("dropout produced empty cells", "", lambda: scpf_check_empty_cells(Xd), True)
case("dropout empty cells (sparse)", "", lambda: scpf_check_empty_cells(sp.csr_matrix(Xd)), True)
case("no empty cells", "", lambda: scpf_check_empty_cells(rng.poisson(3, (100, 30)).astype(float) + 1), False)

# gene counts / HVG [224, 429, 463]
case("1000-gene panels arrive as 992/994", "", lambda: scpf_check_gene_counts({"Limb|scVI": 992, "Brain|scVI": 1000, "Heart|scVI": 994}, expected_n=1000), True)
case("n_top_genes=2000 on 1000-gene input", "", lambda: scpf_check_gene_counts({"a": 1000}, expected_n=1000, hvg_n=2000, n_vars_input=1000), True)
case("hvg == panel size disables variance tripwire", "", lambda: scpf_check_gene_counts({"a": 1000}, expected_n=1000, hvg_n=1000, n_vars_input=1000), True)
case("HVG applied in two stages", "", lambda: scpf_check_gene_counts({"a": 1000}, expected_n=1000, n_hvg_stages=2), True)
case("consistent counts and real HVG pass", "", lambda: scpf_check_gene_counts({"a": 1000, "b": 1000}, expected_n=1000, hvg_n=500, n_vars_input=1000, n_hvg_stages=1), False)

# obs keys [47, 451]
meta = pd.DataFrame({"donor.id": ["D4", "D1", "D2", "D4"] * 5, "cytokine.condition": ["UNS", "Th0", "UNS", "Th0"] * 5,
                     "Age": [20, 31, 44, 55] * 5, "Batch": ["b1", "b2", "b1", "b2"] * 5})
case("positional iloc[:,2]-style pick returns wrong column", "",
     lambda: scpf_check_obs_keys(meta, {"condition": "cytokine.condition"}, used={"condition": meta.columns[0]}), True)
case("expected condition values missing (donor ids instead)", "",
     lambda: scpf_check_obs_keys(meta, {"condition": "donor.id"}, expected_values={"condition": ["UNS", "Th0"]}), True)
case("first-candidate heuristic picks Age as batch_key", "",
     lambda: scpf_check_obs_keys(meta, {"batch_key": "Batch"}, used={"batch_key": "Age"}), True)
case("missing column by name -> KeyError", "", lambda: scpf_check_obs_keys(meta, {"batch_key": "batch"}), True)
case("explicit names with expected values pass", "",
     lambda: scpf_check_obs_keys(meta, {"condition": "cytokine.condition", "batch_key": "Batch"}, expected_values={"condition": ["UNS", "Th0"]},
                                 level_bounds={"batch_key": (2, 60)}, used={"condition": "cytokine.condition", "batch_key": "Batch"}), False)

# block scoring / pairing [440, 352, 12, 349, 150]
layouts = {"scDisInFact": {"Batch": [0, 1, 2], "Cond": [3, 4, 5], "Shared": [6, 7]}, "biolord": None, "scVI": None}
case("block model forced per-dimension (DCI)", "",
     lambda: scpf_check_block_scoring(layouts, {"scDisInFact": "per_dimension", "biolord": "per_dimension", "scVI": "per_dimension"}), True)
case("biolord categorized as block-structured", "",
     lambda: scpf_check_block_scoring(layouts, {"scDisInFact": "block", "biolord": "block", "scVI": "per_dimension"}), True)
case("native bases pass", "",
     lambda: scpf_check_block_scoring(layouts, {"scDisInFact": "block", "biolord": "per_dimension", "scVI": "per_dimension"}), False)
blocks = {"b_batch": [0, 1], "b_cond": [2, 3], "b_shared": [4, 5]}
case("shared subspace credited to Batch", "",
     lambda: scpf_check_block_pairing(blocks, ["Batch", "Cond"], {"b_batch": "Batch", "b_cond": "Cond", "b_shared": "Batch"}, shared_blocks=["b_shared"]), True)
case("pairing with unknown factor", "",
     lambda: scpf_check_block_pairing(blocks, ["Batch", "Cond"], {"b_batch": "Batch", "b_cond": "Condx", "b_shared": None}, shared_blocks=["b_shared"]), True)
case("explicit pairing, shared unowned", "",
     lambda: scpf_check_block_pairing(blocks, ["Batch", "Cond"], {"b_batch": "Batch", "b_cond": "Cond", "b_shared": None}, shared_blocks=["b_shared"]), False)

# knn validity [146, 448]
case("trustworthiness k=15 with n=20", "", lambda: scpf_check_knn_validity({"sub20": 20, "full": 5000}, 15, "trustworthiness"), True)
case("kBET k=12500 with 4999 cells", "", lambda: scpf_check_knn_validity(4999, 12500, "kbet"), True)
case("valid trustworthiness", "", lambda: scpf_check_knn_validity({"a": 400, "b": 5000}, 15, "trustworthiness"), False)

# pooled coverage [457, 369]
cov = pd.DataFrame([(m, d, v) for m, vals in {"scVI": [.5, .6, .9, .8, .7], "PCA": [.45, .55, .85, .75, .65], "scGPT": [.9, .8, .1, None, None]}.items()
                    for d, v in zip("ABCDE", vals) if v is not None], columns=["model", "dataset", "score"])
case("scGPT covers 3 of 5 datasets, naive mean", "", lambda: scpf_check_pool_coverage(cov, value_col="score"), True)
case("balanced coverage passes", "", lambda: scpf_check_pool_coverage(cov[cov.model != "scGPT"], value_col="score"), False)
case("unbalanced allowed with explicit flag", "", lambda: scpf_check_pool_coverage(cov, allow_unbalanced=True), False)

# allowed values [461, 148]
case("stale var_shift files carry the key explicitly", "",
     lambda: scpf_check_allowed_values(["dropout"] * 40 + ["var_shift"] * 15, ["dropout", "noise"], name="perturbation type"), True)
case("missing field also rejected", "", lambda: scpf_check_allowed_values(["dropout", None], ["dropout"], name="perturbation type"), True)
case("only current types pass", "", lambda: scpf_check_allowed_values(["dropout", "noise"], ["dropout", "noise"]), False)

# fallback scan [453, 460]
case("runner refit on full matrix after project() error", "",
     lambda: scpf_scan_fallbacks("fit ok\nRcppML::project failed ... refitting on the full matrix\ndone"), True)
case("retry-then-ignore in log", "", lambda: scpf_scan_fallbacks("task 12 failed: retry-then-ignore"), True)
case("clean log passes", "", lambda: scpf_scan_fallbacks("epoch 1 loss 0.3\nepoch 2 loss 0.2\nheld-out projection ok"), False)

# non-strict mode returns dict instead of raising
def nonstrict():
    r = scpf_check_species(mixed, "human", strict=False)
    assert r["ok"] is False and r["problems"], r
    r["ok"] = True
    return r
case("strict=False returns structured failure", "", nonstrict, False)

# items schema
def items_schema():
    its = scpf_items()
    keys = {"id", "area", "question", "severity", "applies_if", "how_to_check", "auto"}
    ids = [i["id"] for i in its]
    assert len(set(ids)) == len(ids) and all(i.startswith("SC-") for i in ids)
    for i in its:
        assert set(i) == keys, i["id"]
        assert i["severity"] in ("blocking", "major", "minor")
        assert i["auto"] is None or i["auto"] in globals(), (i["id"], i["auto"])
    return {"ok": True}
case("scpf_items schema and auto helpers exist", "-", items_schema, False)

npass = sum(r[0] for r in RESULTS); nfail = len(RESULTS) - npass
lines = ["# experiment-preflight-singlecell tests", "status\tkind\tincident\tcase\tmessage"]
for p, n, inc, kind, msg in RESULTS:
    lines.append("%s\t%s\t%s\t%s\t%s" % ("PASS" if p else "FAIL", kind, inc, n, msg))
lines.append("TOTAL pass=%d fail=%d" % (npass, nfail))
open("scpf_test_results.txt", "w").write("\n".join(lines))
print("\n".join(l for l in lines if l.startswith("FAIL") or l.startswith("TOTAL")))
