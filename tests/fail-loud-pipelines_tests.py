"""Tests for fail-loud-pipelines kernel.py. Each test reproduces a known failure pattern as a synthetic fixture:
a known-bad case must fail loudly and a known-good case must pass. Run: python fail-loud-pipelines_tests.py [kernel.py path]"""
import os, sys, json, tempfile, textwrap, types, traceback
import numpy as np
import pandas as pd

KPATH = sys.argv[1] if len(sys.argv) > 1 else "fl_kernel.py"
if not os.path.exists(KPATH):
    KPATH = "fl_kernel.py"
G = {}
exec(open(KPATH).read(), G)
globals().update({k: v for k, v in G.items() if k.startswith("fl_") or k.startswith("FL_")})

TMP = tempfile.mkdtemp(prefix="fl_tests_")
TESTS = []

def test(name, incidents):
    def deco(fn):
        TESTS.append((name, incidents, fn)); return fn
    return deco

def w(rel, text):
    p = os.path.join(TMP, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(textwrap.dedent(text)); return p

def raises(kind, fn, contains=None):
    try:
        fn()
    except Exception as e:
        assert type(e).__name__ == kind, "expected %s got %s: %s" % (kind, type(e).__name__, e)
        assert getattr(e, "result", None) is not None, "exception carries no .result"
        if contains:
            assert contains in str(e), "message lacks %r: %s" % (contains, e)
        return e
    raise AssertionError("expected %s but nothing was raised (silent pass)" % kind)

def rules_of(res):
    return sorted({f["rule"] for f in res["findings"]})

# ---------------- fl_lint ----------------
@test("lint: listdir()[0] file picking + broad except fallback (UCE all-NaN; RcppML refit)", [])
def _():
    p = w("l1/run_uce.py", """
        import os
        def get_out(d):
            return os.path.join(d, os.listdir(d)[0])
        def project(x):
            try:
                return fit_heldout(x)
            except Exception:
                return refit_full(x)
        """)
    r = fl_lint(p, strict=False)
    assert {"FL110", "FL101"} <= set(rules_of(r)), rules_of(r)
    e = raises("FLLintError", lambda: fl_lint(p), "FL110")
    assert "run_uce.py:4" in str(e)

@test("lint: glob variable [0] and next(iter(glob))", [])
def _():
    p = w("l1b/pick.py", """
        import glob
        fs = glob.glob("out/*.h5ad")
        a = fs[0]
        b = next(iter(glob.glob("x/*")))
        """)
    r = fl_lint(p, strict=False)
    assert [f["line"] for f in r["findings"] if f["rule"] == "FL110"] == [4, 5], r["findings"]

@test("lint: set +e and || true in shell (merge resolver committed markers)", [])
def _():
    p = w("l2/resolve.sh", """
        #!/bin/bash
        set +e
        git merge origin/main || true
        f=$(ls out | head -1)
        """)
    r = fl_lint(p, strict=False)
    assert {"FL120", "FL121", "FL111"} <= set(rules_of(r)), rules_of(r)

@test("lint: numba fastmath + parse_known_args + argparse type=bool", [])
def _():
    p = w("l3/m.py", """
        import argparse
        from numba import njit
        @njit(fastmath=True)
        def lisi(x): return x
        ap = argparse.ArgumentParser()
        ap.add_argument("--allow_empty", type=bool, default=False)
        args, rest = ap.parse_known_args()
        """)
    r = fl_lint(p, strict=False)
    assert {"FL150", "FL130", "FL131"} <= set(rules_of(r)), rules_of(r)

@test("lint: Nextflow hardcoded executor in process body (bad) vs config-only (good)", [])
def _():
    bad = w("l4/bad.nf", """
        process SCORE {
            executor 'slurm'
            cpus 4
            script:
            \"\"\"
            echo executor slurm is only text here
            \"\"\"
        }
        """)
    good = w("l4/good.nf", """
        process SCORE {
            cpus 4
            script:
            \"\"\"
            echo hi
            \"\"\"
        }
        // executor 'slurm' lives in nextflow.config profiles
        """)
    r = fl_lint(bad, strict=False)
    assert [f["line"] for f in r["findings"] if f["rule"] == "FL160"] == [3], r["findings"]
    assert fl_lint(good)["ok"]

@test("lint: Nextflow ifEmpty([]), errorStrategy ignore, bare boolean param", [])
def _():
    p = w("l5/x.nf", """
        workflow {
            curves.ifEmpty([]).set { c }
            if (params.allow_empty_metrics) { log.info 'x' }
        }
        process A { errorStrategy 'ignore'
          script: "true" }
        """)
    r = fl_lint(p, strict=False)
    assert {"FL161", "FL162", "FL163"} <= set(rules_of(r)), rules_of(r)

@test("lint: records failures but exits 0 (launcher wrote .done despite failed configs)", [])
def _():
    p = w("l6/run_experiment.py", """
        import argparse, sys
        def main():
            rows = []
            for c in range(3):
                try:
                    rows.append(run(c))
                except RuntimeError as e:
                    rows.append({"cfg": c, "status": "failed", "error": str(e)})
            return rows
        if __name__ == "__main__":
            argparse.ArgumentParser().parse_args()
            main()
        """)
    r = fl_lint(p, strict=False)
    assert "FL171" in rules_of(r), r["findings"]
    p2 = w("l6/run2.py", """
        import sys
        try:
            go()
        except RuntimeError as e:
            print("FAILED", e)
        sys.exit(0)
        """)
    assert "FL170" in rules_of(fl_lint(p2, strict=False))
    p3 = w("l6/ok.py", """
        import sys
        failed = []
        try:
            go()
        except RuntimeError as e:
            failed.append(str(e))
        sys.exit(1 if failed else 0)
        """)
    assert "FL170" not in rules_of(fl_lint(p3, strict=False))

@test("lint: name used without import (hashlib, sparse)", [])
def _():
    p = w("l7/cache.py", """
        from scipy.sparse import csr_matrix
        def key(x):
            return hashlib.sha1(x).hexdigest()
        def f(gt):
            return sparse.issparse(gt)
        """)
    r = fl_lint(p, strict=False)
    names = sorted(f["text"].split(" ")[0] for f in r["findings"] if f["rule"] == "FL230")
    assert names == ["hashlib", "sparse"], names

@test("lint: output to $HOME / ~ and /tmp", [])
def _():
    p = w("l8/o.py", """
        import os, numpy as np
        out = os.path.expanduser("~/embeddings")
        np.savez("/tmp/emb.npz", x=1)
        """)
    r = fl_lint(p, strict=False)
    assert {"FL180", "FL181"} <= set(rules_of(r)), rules_of(r)

@test("lint: shlex.quote of $SCRATCH; errors='coerce'", [])
def _():
    p = w("l9/r.py", """
        import shlex, pandas as pd
        wd = shlex.quote("$SCRATCH/jobs")
        df = pd.read_csv("a.csv"); df["v"] = pd.to_numeric(df["v"], errors="coerce")
        """)
    assert {"FL240", "FL250"} <= set(rules_of(fl_lint(p, strict=False)))

@test("lint: R tryCatch swallowing error; silent try", [])
def _():
    p = w("l10/a.R", """
        r <- tryCatch(project(x), error = function(e) NULL)
        s <- try(fit(x), silent = TRUE)
        ok <- tryCatch(f(x), error = function(e) stop("fit failed: ", conditionMessage(e)))
        """)
    f = [x for x in fl_lint(p, strict=False)["findings"] if x["rule"] == "FL103"]
    assert [x["line"] for x in f] == [2, 3], f

@test("lint: suppression requires a reason; valid suppression is recorded", [])
def _():
    p = w("l11/s.py", """
        try:
            import torch
        except Exception:  # fl: allow FL101 optional dependency probe, absence handled by fl_env_guard
            torch = None
        try:
            import numba
        except Exception:  # fl: allow FL101
            numba = None
        import os
        # fl: allow FL110 test fixture directory has exactly one file
        x = os.listdir(".")[0]
        """)
    r = fl_lint(p, strict=False)
    assert [s["rule"] for s in r["suppressed"]] == ["FL101", "FL110"], r["suppressed"]
    rs = rules_of(r)
    assert "FL000" in rs and "FL101" in rs, r["findings"]   # reasonless allow -> FL000 and finding stays
    assert all(s["reason"] for s in r["suppressed"])

@test("lint: clean pipeline code passes; empty path set and bad paths raise", [])
def _():
    p = w("l12/clean.py", """
        import argparse, glob, sys
        def pick(d):
            fs = sorted(glob.glob(d + "/*_uce_adata.h5ad"))
            assert len(fs) == 1, fs
            return fs[0].upper()
        def main():
            ap = argparse.ArgumentParser(); ap.add_argument("--x"); a = ap.parse_args()
            try:
                run(a)
            except ValueError as e:
                print("bad", e)
                raise
        def run(a): pass
        if __name__ == "__main__":
            sys.exit(main() or 0)
        """)
    # a len() check on the matches counts as asserting uniqueness; a bare glob(...)[0] is still flagged (see l1b)
    r = fl_lint(p, strict=False)
    assert r["findings"] == [], r["findings"]
    for bad, kind in ((os.path.join(TMP, "nope"), FileNotFoundError), (w("l12/empty/readme.txt", "x"), ValueError)):
        try:
            fl_lint(bad); raise AssertionError("no error for %s" % bad)
        except kind:
            pass
    d = os.path.join(TMP, "l12", "emptydir"); os.makedirs(d, exist_ok=True)
    try:
        fl_lint(d); raise AssertionError("scan of nothing passed")
    except ValueError:
        pass


@test("lint: R multi-line handler with stop() inside braces passes; one returning NA fails", [])
def _():
    p = w("l10/b.R", """
        a <- tryCatch({
          project(x)
        }, error = function(e) {
          message("failed")
          stop(e)
        })
        b <- tryCatch({
          project(x)
        }, error = function(e) {
          NA
        })
        """)
    f = [x["line"] for x in fl_lint(p, strict=False)["findings"] if x["rule"] == "FL103"]
    assert f == [10], f

# ---------------- fl_args_consumed ----------------
def mk_parser():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n_seeds", type=int, default=3)
    ap.add_argument("--model-output", required=True)
    ap.add_argument("--epochs", type=int, default=150)
    return ap

@test("args: config keys parse_known_args would silently drop (lr, seeds)", [])
def _():
    cfg = {"lr": 2e-5, "seeds": [1, 2, 3], "epochs": 500, "model_output": "x"}
    e = raises("FLArgsError", lambda: fl_args_consumed(cfg, mk_parser()), "n_seeds")
    assert set(e.result["unknown"]) == {"lr", "seeds"}, e.result
    assert "n_seeds" in e.result["unknown"]["seeds"]

@test("args: driver argv uses nonexistent --results-root and omits required --model-output", [])
def _():
    e = raises("FLArgsError", lambda: fl_args_consumed(["--results-root", "/r", "--epochs=5"], mk_parser()))
    assert e.result["unknown"].keys() == {"results_root"} and e.result["missing_required"] == ["model_output"]

@test("args: manifest rewrite dropped --epochs 500 is caught when epochs is required by the experiment", [])
def _():
    # the generic guard: the experiment declares epochs mandatory via provided/required check
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--epochs", type=int, required=True)
    raises("FLArgsError", lambda: fl_args_consumed({"seed": 1}, ap, ignore=["seed"]), "epochs")

@test("args: fully-consumed config passes (dash/underscore equivalent)", [])
def _():
    r = fl_args_consumed({"n-seeds": 3, "model_output": "o", "epochs": 500}, mk_parser())
    assert r["ok"] and len(r["consumed"]) == 3
    assert fl_args_consumed({"n_seeds": 1, "model_output": "o", "tag": "t"}, mk_parser(), ignore=["tag"])["ok"]

# ---------------- fl_unique_outputs ----------------
@test("unique: swept batch_count absent from filename (125 rows -> 95 files)", [])
def _():
    rows = [{"ds": d, "batch_count": b, "seed": s} for d in ("a", "b") for b in (2, 3, 4) for s in (0, 1)]
    e = raises("FLUniqueOutputsError", lambda: fl_unique_outputs(rows, "emb/{ds}_s{seed}.npz", ["batch_count"]), "batch_count")
    assert e.result["n_unique_paths"] == 4 and e.result["n_rows"] == 12
    r = fl_unique_outputs(rows, "emb/{ds}_b{batch_count}_s{seed}.npz", ["batch_count"])
    assert r["ok"] and r["n_unique_paths"] == 12

@test("unique: index-0 suffix skipped -> E4 run overwrites E1 latents", [])
def _():
    rows = [{"exp": "E4", "ref_idx": i, "seed": 0} for i in range(3)]
    fn = lambda r: "lat/ds_s0.npz" if not r["ref_idx"] else "lat/ds_s0_fixed_ref%d.npz" % r["ref_idx"]
    # rows 0 and the E1 file collide
    e1 = {"exp": "E1", "ref_idx": None, "seed": 0}
    raises("FLUniqueOutputsError", lambda: fl_unique_outputs(rows, fn, ["ref_idx"], reserved_paths=["lat/ds_s0.npz"]), "another experiment")
    good = lambda r: "lat/ds_s0_fixed_ref%d.npz" % r["ref_idx"]
    assert fl_unique_outputs(rows, good, ["ref_idx"], reserved_paths=["lat/ds_s0.npz"])["ok"]

@test("unique: model tag left out of metrics JSON (LinearSCVI overwrites SCVI)", [])
def _():
    rows = [{"model": m, "ds": "x", "seed": 1} for m in ("scvi", "linearscvi")]
    e = raises("FLUniqueOutputsError", lambda: fl_unique_outputs(rows, "m/{ds}_s{seed}.json", ["model"]))
    assert e.result["collisions"] and e.result["collisions"][0]["differing_keys"] == ["model"]

@test("unique: resume key omits batch_size/lr -> 18 of 24 configs skipped as done", [])
def _():
    rows = [{"method": "m", "backbone": "b", "d_coef": 1, "seed": s, "batch_size": bs, "lr": lr} for s in (0, 1, 2) for bs in (64, 128) for lr in (1e-3, 1e-4)]
    resume_key = lambda r: "|".join(str(r[k]) for k in ("method", "backbone", "d_coef", "seed"))
    e = raises("FLUniqueOutputsError", lambda: fl_unique_outputs(rows, resume_key, ["batch_size", "lr"]))
    assert set(e.result["keys_not_in_path"]) == {"batch_size", "lr"}
    full = lambda r: resume_key(r) + "|%s|%s" % (r["batch_size"], r["lr"])
    assert fl_unique_outputs(rows, full, ["batch_size", "lr"])["ok"]

@test("unique: duplicate identical configs and single-valued sweep are surfaced", [])
def _():
    rows = [{"a": 1, "s": 0}, {"a": 1, "s": 0}]
    e = raises("FLUniqueOutputsError", lambda: fl_unique_outputs(rows, "o/{a}_{s}", ["a"]))
    assert e.result["duplicates"] and any("single value" in x for x in e.result["warnings"])
    assert fl_unique_outputs(rows, "o/{a}_{s}", ["a"], allow_duplicates=True)["ok"]

# ---------------- fl_completion_gate ----------------
def make_run(dirn, seeds=(0, 1, 2, 3, 4), nan_seed=None, failed=False):
    d = os.path.join(TMP, dirn); os.makedirs(d, exist_ok=True)
    rows = []
    for s in seeds:
        a = np.random.default_rng(s).normal(size=(10, 4))
        if s == nan_seed: a[:] = np.nan
        np.save(os.path.join(d, "emb_s%d.npy" % s), a)
        rows.append({"seed": s, "status": "ok", "error": ""})
    if failed:
        rows[-1] = {"seed": rows[-1]["seed"], "status": "failed", "error": "CUDA OOM"}
    pd.DataFrame(rows).to_csv(os.path.join(d, "results.csv"), index=False)
    return d

EXP5 = ["emb_s%d.npy" % s for s in range(5)]

@test("gate: shard OOM'd on one seed (4 of 5 present) must not get .done", [])
def _():
    d = make_run("g1", seeds=(0, 1, 2, 3))
    marker = os.path.join(d, ".done")
    e = raises("FLCompletionError", lambda: fl_completion_gate(EXP5, d, done_marker=marker), "emb_s4.npy")
    assert e.result["missing"] == ["emb_s4.npy"] and not os.path.exists(marker)

@test("gate: all-NaN embedding file (scGen latent 0% finite) fails; finite passes and writes marker", [])
def _():
    d = make_run("g2", nan_seed=2)
    e = raises("FLCompletionError", lambda: fl_completion_gate(EXP5, d), "non-finite")
    assert "emb_s2.npy" in e.result["nonfinite"][0]
    d2 = make_run("g2b")
    marker = os.path.join(d2, ".done")
    r = fl_completion_gate(EXP5, d2, results_csv="results.csv", expected_rows=5, done_marker=marker)
    assert r["ok"] and os.path.exists(marker)

@test("gate: failed config recorded as a row (launcher exits 0) blocks the marker; stale marker flagged", [])
def _():
    d = make_run("g3", failed=True)
    marker = os.path.join(d, ".done"); open(marker, "w").write("{}")
    e = raises("FLCompletionError", lambda: fl_completion_gate(EXP5, d, results_csv="results.csv", done_marker=marker), "failed")
    assert e.result["failed_rows"] and e.result["stale_marker"] == marker and "STALE" in str(e)

@test("gate: headerless/empty CSV and zero-byte output (row drop removed header)", [])
def _():
    d = make_run("g4")
    open(os.path.join(d, "results.csv"), "w").write("")
    raises("FLCompletionError", lambda: fl_completion_gate(EXP5, d, results_csv="results.csv"))
    open(os.path.join(d, "emb_s0.npy"), "w").write("")
    e = raises("FLCompletionError", lambda: fl_completion_gate(EXP5, d), "emb_s0.npy")
    assert e.result["empty"]

@test("gate: h5ad whose obsm has NaN, or has no obsm at all (wrong UCE file), fails", [])
def _():
    import anndata
    d = os.path.join(TMP, "g5"); os.makedirs(d, exist_ok=True)
    X = np.ones((6, 3), dtype=np.float32)
    good = anndata.AnnData(X); good.obsm["X_uce"] = np.ones((6, 4), dtype=np.float32); good.write_h5ad(os.path.join(d, "good.h5ad"))
    nan = anndata.AnnData(X); nan.obsm["X_uce"] = np.full((6, 4), np.nan, dtype=np.float32); nan.write_h5ad(os.path.join(d, "nan.h5ad"))
    noemb = anndata.AnnData(X); noemb.write_h5ad(os.path.join(d, "proc.h5ad"))
    assert fl_completion_gate([{"path": "good.h5ad", "obsm": ["X_uce"]}], d)["ok"]
    raises("FLCompletionError", lambda: fl_completion_gate([{"path": "nan.h5ad", "obsm": ["X_uce"]}], d), "non-finite")
    raises("FLCompletionError", lambda: fl_completion_gate([{"path": "proc.h5ad", "obsm": ["X_uce"]}], d), "obsm")
    raises("FLCompletionError", lambda: fl_completion_gate([{"path": "proc.h5ad", "obsm": True}], d), "empty")

@test("gate: globs and empty expected lists are rejected (vacuous gate)", [])
def _():
    d = make_run("g6")
    for bad in (["emb_*.npy"], []):
        try:
            fl_completion_gate(bad, d); raise AssertionError("vacuous gate passed")
        except ValueError:
            pass

# ---------------- fl_columns ----------------
@test("columns: 'Infect'/'Inject' vs obs columns 'Infected'/'Injected' (all-zero DAG target)", [])
def _():
    import anndata
    ad = anndata.AnnData(np.ones((4, 2), dtype=np.float32)); ad.obs["Infected"] = list("abab"); ad.obs["Injected"] = list("aabb")
    e = raises("FLColumnsError", lambda: fl_columns(ad, ["Infect", "Inject"]), "Infected")
    assert e.result["missing"]["Infect"][0] == "Infected" and e.result["missing"]["Inject"][0] == "Injected", e.result["missing"]
    assert fl_columns(ad, ["Infected", "Injected"])["ok"]

@test("columns: DRVI_LMS vs DRVI_LMS_mean; label 'PCA' vs 'PCA (baseline)'; 'scVI' vs 'scVI (LDVAE)'", [])
def _():
    df = pd.DataFrame({"DRVI_LMS_mean": [1.0], "model": ["PCA (baseline)"]})
    e = raises("FLColumnsError", lambda: fl_columns(df, ["DRVI_LMS"]), "DRVI_LMS_mean")
    raises("FLColumnsError", lambda: fl_columns(df, ["model"], values={"model": ["PCA"]}), "PCA (baseline)")
    df2 = pd.DataFrame({"model": ["scVI (LDVAE)", "scVI (Standard)"]})
    e = raises("FLColumnsError", lambda: fl_columns(df2, ["model"], values={"model": ["LDVAE", "scVI"]}))
    assert "scVI (LDVAE)" in e.result["bad_values"]["model"]["LDVAE"]
    assert fl_columns(df2, ["model"], values={"model": ["scVI (LDVAE)"]})["ok"]

@test("columns: case-sensitive model-name mismatch; column named like a DataFrame method", [])
def _():
    df = pd.DataFrame({"model": ["NMF", "PCA"], "head": [1, 2]})
    e = raises("FLColumnsError", lambda: fl_columns(df, ["model"], values={"model": ["nmf"]}))
    assert e.result["bad_values"]["model"]["nmf"] == ["NMF"]
    r = fl_columns(df, ["head"])
    assert r["ok"] and r["shadowed"] == ["head"]
    try:
        fl_columns(df, "model"); raise AssertionError("string names accepted")
    except TypeError:
        pass

# ---------------- fl_env_guard ----------------
def fake_torch(version, cuda, avail):
    m = types.ModuleType("torch"); m.__version__ = version
    m.version = types.SimpleNamespace(cuda=cuda)
    m.cuda = types.SimpleNamespace(is_available=lambda: avail, device_count=lambda: 1 if avail else 0, get_device_name=lambda i: "FakeGPU")
    m.zeros = lambda *a, **k: None
    return m

@test("env: conda fork silently replaced CUDA torch 2.4.1+cu121 with CPU-only 2.10.0", [])
def _():
    saved = sys.modules.get("torch")
    try:
        sys.modules["torch"] = fake_torch("2.10.0+cpu", None, False)
        e = raises("FLEnvError", lambda: fl_env_guard(), "CPU-only")
        sys.modules["torch"] = fake_torch("2.4.1+cu121", "12.1", True)
        r = fl_env_guard(packages={"torch": "2.4.1"})
        assert r["ok"] and r["info"]["gpus"] == ["FakeGPU"]
        raises("FLEnvError", lambda: fl_env_guard(packages={"torch": "2.10.0"}), "does not satisfy")
        sys.modules["torch"] = fake_torch("2.4.1+cu121", "12.1", False)
        raises("FLEnvError", lambda: fl_env_guard(), "no GPU")
    finally:
        if saved is None: sys.modules.pop("torch", None)
        else: sys.modules["torch"] = saved

@test("env: roster silently thinned - every missing import is reported, not just the first", [])
def _():
    e = raises("FLEnvError", lambda: fl_env_guard(require_cuda=False, imports=["numpy", "cnmf_nope", "glmpca_nope", "schpf_nope"]))
    assert set(e.result["failed_imports"]) == {"cnmf_nope", "glmpca_nope", "schpf_nope"}
    assert fl_env_guard(require_cuda=False, imports=["numpy", "pandas"])["ok"]

@test("env: unexported fix var (WCD_WORKERS), cache dir defaulting into $HOME, missing thread caps", [])
def _():
    for v in ("FL_T_WORKERS", "FL_T_CACHE", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.pop(v, None)
    e = raises("FLEnvError", lambda: fl_env_guard(require_cuda=False, env_vars=["FL_T_WORKERS"], not_under_home=["FL_T_CACHE"], require_thread_caps=True))
    assert len(e.result["problems"]) == 5, e.result["problems"]
    os.environ.update({"FL_T_WORKERS": "8", "FL_T_CACHE": "/scratch/x/cache", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
    assert fl_env_guard(require_cuda=False, env_vars=["FL_T_WORKERS"], not_under_home=["FL_T_CACHE"], require_thread_caps=True)["ok"]
    os.environ["FL_T_CACHE"] = os.path.expanduser("~/.cache/apptainer")
    raises("FLEnvError", lambda: fl_env_guard(require_cuda=False, not_under_home=["FL_T_CACHE"]), "under $HOME")

# ---------------- fl_mutation_check ----------------
LOG_GOOD = "Output written on main.pdf (10 pages).\n"
LOG_BAD_REF = "LaTeX Warning: Reference `fig:x' on page 3 undefined on input line 88.\n"
LOG_BAD_CITE = "LaTeX Warning: Citation `smith' on page 1 undefined on input line 12.\n"

@test("mutation: CI gate regex blind to \\ref warnings is exposed", [])
def _():
    import re
    blind = lambda log: not re.search(r"Citation .* undefined", log)       # cites only
    e = raises("FLMutationError", lambda: fl_mutation_check(blind, LOG_GOOD, {"ref": LOG_BAD_REF, "cite": LOG_BAD_CITE}), "ref")
    assert e.result["missed"] == ["ref"] and "cite" in e.result["caught"]
    fixed = lambda log: not re.search(r"(Reference|Citation) .* undefined", log)
    assert fl_mutation_check(fixed, LOG_GOOD, {"ref": LOG_BAD_REF, "cite": LOG_BAD_CITE})["ok"]

@test("mutation: gate that always passes (silently failed conda activate => vacuous PASS)", [])
def _():
    always = lambda x: True
    e = raises("FLMutationError", lambda: fl_mutation_check(always, "good", ["broken"]), "vacuous")
    assert e.result["missed"] == ["bad_0"]
    assert fl_mutation_check(lambda x: x == "good" or (_ for _ in ()).throw(AssertionError("broken")), "good", ["broken"])["ok"]

@test("mutation: checker fails for the wrong reason (wrong path / missing module) is not a catch", [])
def _():
    def wrongpath(p):
        open("/nonexistent_parent/" + p).read()
    e = raises("FLMutationError", lambda: fl_mutation_check(wrongpath, "good.txt", ["bad.txt"]), "known-good")
    assert not e.result["good_passed"]
    def pat(x):
        if x == "bad": raise NameError("name 'foo' is not defined")
        return True
    e = raises("FLMutationError", lambda: fl_mutation_check(pat, "good", {"bad": ("bad", "divergence")}), "wrong reason")
    assert "bad" in e.result["wrong_reason"]
    try:
        fl_mutation_check(lambda x: True, "g", []); raise AssertionError("no bad inputs accepted")
    except ValueError:
        pass

@test("mutation: log parser took gpu id token instead of config tag (positive control)", [])
def _():
    import re
    line = "[FAIL] gpu0 cfg=vae_d3_s1 diverged at epoch 12"
    def parse_wrong(l): return l.split()[1]
    def parse_right(l): return re.search(r"cfg=(\S+)", l).group(1)
    expect = lambda parser: (lambda l: parser(l) == "vae_d3_s1" or (_ for _ in ()).throw(AssertionError("wrong token")))
    # good input = the real FAIL line; parser must extract the tag -> wrong parser fails the known-good
    e = raises("FLMutationError", lambda: fl_mutation_check(expect(parse_wrong), line, ["[FAIL] gpu1 cfg=other"]), "known-good")
    assert fl_mutation_check(expect(parse_right), line, ["[FAIL] gpu1 cfg=other"])["ok"]

def main():
    ok = bad = 0; covered = set(); rep = []
    for name, inc, fn in TESTS:
        try:
            fn(); ok += 1; covered.update(inc); rep.append(("PASS", name, inc))
        except Exception:
            bad += 1; rep.append(("FAIL", name, inc)); traceback.print_exc()
    for s, n, i in rep:
        print("%s  %s  incidents=%s" % (s, n, i))
    print("TOTAL passed=%d failed=%d incidents_covered=%s" % (ok, bad, sorted(covered)))
    json.dump({"passed": ok, "failed": bad, "incidents_covered": sorted(covered), "results": rep}, open("fail-loud-pipelines_test_results.json", "w"), indent=1)
    return bad

if __name__ == "__main__":
    sys.exit(1 if main() else 0)
