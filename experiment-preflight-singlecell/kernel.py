"""experiment-preflight-singlecell sidecar (prefix scpf_).

Domain pack for the generic `experiment-preflight` skill: SC-NN checklist items plus
auto-check helpers for single-cell / spatial / perturbation omics experiments.
Inputs may be AnnData-like objects (duck-typed: .obs/.X/.var_names) or plain
pandas / numpy objects. Every check returns a dict
{"ok", "item", "check", "problems", "warnings", "details"} and, with strict=True
(default), raises ValueError (failed check) or KeyError (required column missing).
"""
import os
import re

import numpy as np
import pandas as pd

SCPF_VERSION = "1.0"

SCPF_FALLBACK_PATTERNS = (
    r"refit(ting)?\s+on\s+(the\s+)?full",
    r"fall(ing)?[\s-]*back",
    r"using\s+(the\s+)?full\s+(data|matrix)",
    r"projection\s+(failed|abandoned)",
    r"ignor(ed|ing)\s+(the\s+)?(error|exception|failure)",
    r"silently",
    r"retry[\s-]*then[\s-]*ignore",
)

SCPF_ITEMS = [
    {"id": "SC-01", "area": "splits", "severity": "blocking",
     "question": "Are any evaluated labels constant within a donor/patient (donor-level), and if so is the train/test split grouped by donor rather than by cell?",
     "applies_if": "A probe, classifier or held-out metric uses labels such as response, treatment time, disease, genotype or any per-sample/per-patient annotation.",
     "how_to_check": "Run scpf_check_donor_split(adata, label_cols, donor_col, split_col=...) on the realized split; a label with one value per donor is donor-level and needs a donor-grouped split.",
     "auto": "scpf_check_donor_split"},
    {"id": "SC-02", "area": "splits", "severity": "blocking",
     "question": "Is the grouping key unique across cohorts (composite cohort.patient), with the sample-to-patient mapping verified rather than assumed?",
     "applies_if": "Data from more than one cohort/study/assay is pooled, or the grouping column is a sample/batch id that is assumed to equal one patient.",
     "how_to_check": "Run scpf_check_donor_split(..., cohort_col=..., sample_col=...) and inspect id collisions and sample-to-donor nesting; check prior notes/memory for recorded collisions before writing guidance.",
     "auto": "scpf_check_donor_split"},
    {"id": "SC-03", "area": "splits", "severity": "blocking",
     "question": "On the realized train/test assignment, does any donor/group appear on both sides?",
     "applies_if": "Any train/test, held-out or cross-validation split exists for data with repeated cells per donor/sample.",
     "how_to_check": "Run scpf_check_split_leakage(adata, group_col, split_col) on the saved split labels, not on the splitting code.",
     "auto": "scpf_check_split_leakage"},
    {"id": "SC-04", "area": "splits", "severity": "major",
     "question": "Are guarantees claimed for a custom grouped-split function (no straddling, target fraction, optimality) verified empirically over many seeds?",
     "applies_if": "A custom grouped/stratified split function is written or its docstring states a guarantee.",
     "how_to_check": "Run scpf_check_split_fn(split_fn, group_ids, n_seeds=10, check_optimal=True) and compare reported side stability and optimality gap with the docstring.",
     "auto": "scpf_check_split_fn"},
    {"id": "SC-05", "area": "gene panel", "severity": "blocking",
     "question": "Does every feature in the gene panel / program gene set belong to the intended species (ENSG vs ENSMUSG, symbol case)?",
     "applies_if": "A gene panel, feature set or program gene set is built from a reference or dataset that may combine species, or a dataset's species is assumed from its name.",
     "how_to_check": "Run scpf_check_species(adata_or_var_names, expected='human') on the final panel, the program gene set and each model input.",
     "auto": "scpf_check_species"},
    {"id": "SC-06", "area": "gene panel", "severity": "major",
     "question": "Does each model's required gene vocabulary cover the dataset's genes, and are models with incompatible species/ID types excluded or reported on the compatible subset?",
     "applies_if": "A foundation model with a fixed gene vocabulary (token dictionary, species-specific ID list) is evaluated.",
     "how_to_check": "Run scpf_check_vocab_coverage(var_names, vocab, min_frac=...) per model x dataset; list which datasets each model is excluded from and why.",
     "auto": "scpf_check_vocab_coverage"},
    {"id": "SC-07", "area": "perturbation", "severity": "blocking",
     "question": "Is the zero-intensity perturbation exactly the raw input (a true no-op), for every dataset and every perturbation type, including default dispersion/shape parameters?",
     "applies_if": "A perturbation axis (noise, dropout, variance shift, batch effect, etc.) includes a level that is meant to be the unperturbed baseline.",
     "how_to_check": "Run scpf_check_noop_perturbation(raw, perturbed_at_zero) per dataset; any nonzero difference or changed shape blocks the run.",
     "auto": "scpf_check_noop_perturbation"},
    {"id": "SC-08", "area": "perturbation", "severity": "blocking",
     "question": "Are cell counts (and per-cell QC statistics) reported along each perturbation axis, and does the retained sample size stay within a stated ratio?",
     "applies_if": "A perturbation or filtering step varies in severity and downstream metrics are compared across its levels.",
     "how_to_check": "Tabulate n_cells (and median genes/cell) per dataset x level and run scpf_check_cell_counts(table, axis_col, max_ratio=...).",
     "auto": "scpf_check_cell_counts"},
    {"id": "SC-09", "area": "perturbation", "severity": "blocking",
     "question": "Can the perturbation generator produce empty cells or genes, and is that guarded before the model runners see the data?",
     "applies_if": "Dropout, thinning, subsampling or count-level noise is applied before model fitting.",
     "how_to_check": "Run scpf_check_empty_cells(perturbed, min_genes=1) over the full perturbation grid, not one setting.",
     "auto": "scpf_check_empty_cells"},
    {"id": "SC-10", "area": "gene panel", "severity": "major",
     "question": "Do the number of genes reaching each model match the declared panel size, and is HVG selection a real ranking (n_top_genes < n_vars) applied exactly once?",
     "applies_if": "HVG selection or a fixed gene-panel size is configured and results are compared across models or datasets.",
     "how_to_check": "Record n_genes at model input per model x dataset and run scpf_check_gene_counts(n_genes_by_model, expected_n, hvg_n, n_vars_input, n_hvg_stages).",
     "auto": "scpf_check_gene_counts"},
    {"id": "SC-11", "area": "inputs", "severity": "blocking",
     "question": "Are batch key, condition and donor columns resolved by explicit name and validated by expected values, never by column position or a heuristic first match?",
     "applies_if": "A script, smoke test or loader selects obs/metadata columns (batch_key, condition, donor, label).",
     "how_to_check": "Run scpf_check_obs_keys(adata, required={role: column}, expected_values=..., used={role: resolved column}) in the loader and the smoke test.",
     "auto": "scpf_check_obs_keys"},
    {"id": "SC-12", "area": "claims", "severity": "major",
     "question": "Does the claimed finding (e.g. batch demoted below biology) go beyond what the model's batch/covariate conditioning mechanism does by construction?",
     "applies_if": "A claim of disentanglement, invariance or integration is made for a model whose decoder or encoder is explicitly conditioned on the covariate in question.",
     "how_to_check": "List each model's conditioning inputs from its source; label such results as conditioning/integration effects, and require an unconditioned comparison or ablation before claiming disentanglement.",
     "auto": None},
    {"id": "SC-13", "area": "metrics", "severity": "major",
     "question": "Are factor pairs deterministically nested (one factor fully determines the other) and excluded from collision/overlap counts and dependence metrics?",
     "applies_if": "Several covariates (batch, response, time, cancer type, donor) are scored against each other or against latent dimensions.",
     "how_to_check": "Run scpf_check_nesting(adata, factors, pairs=...) and drop or separately report nested pairs.",
     "auto": "scpf_check_nesting"},
    {"id": "SC-14", "area": "metrics", "severity": "blocking",
     "question": "Is each model scored in its native concept basis (block metrics for block-structured latents, per-dimension only for one-dimension-per-factor), and are block-sensitive probes and reproducibility scores valid for residual blocks?",
     "applies_if": "Models with multi-dimensional concept blocks (shared/private/residual subspaces) are compared with per-dimension disentanglement metrics (MIG, DCI, SAP, completeness).",
     "how_to_check": "Build layouts from each model's source (None if no block structure) and run scpf_check_block_scoring(layouts, bases); fail on silent fallback to per-dimension.",
     "auto": "scpf_check_block_scoring"},
    {"id": "SC-15", "area": "metrics", "severity": "major",
     "question": "Is block-to-factor pairing an explicit name map, with unowned/shared blocks not credited to any factor?",
     "applies_if": "A block-aware metric pairs latent blocks with annotated factors.",
     "how_to_check": "Run scpf_check_block_pairing(blocks, factors, pairing, shared_blocks).",
     "auto": "scpf_check_block_pairing"},
    {"id": "SC-16", "area": "metrics", "severity": "blocking",
     "question": "Are kNN-based metric parameters valid for the smallest sample at every condition (trustworthiness k < n/2; kNN k < n; kBET k <= n)?",
     "applies_if": "A kNN-derived metric (trustworthiness, kNN purity/NMI, kBET, LISI) is computed on subsampled, filtered or perturbed data of varying size.",
     "how_to_check": "Run scpf_check_knn_validity(n_cells_by_condition, k, kind) before computing; ensure invalid cells are NaN with a status string.",
     "auto": "scpf_check_knn_validity"},
    {"id": "SC-17", "area": "metrics", "severity": "major",
     "question": "Are non-computable metric cells reported as NaN with a status string (never a placeholder value), and are NaN columns traced to their true cause?",
     "applies_if": "Any metric can be undefined for some dataset/model/condition.",
     "how_to_check": "Scan result tables for constants such as 1.0000/0.0 on infeasible cells; confirm each NaN has a status and the cause is read from the code path, not guessed.",
     "auto": None},
    {"id": "SC-18", "area": "aggregation", "severity": "major",
     "question": "Do all pooled models cover the same datasets; if not, is pooling restricted to the common subset (or coverage shown) and rank stability checked?",
     "applies_if": "Metrics are averaged across datasets per model and some models run on a subset (species restriction, memory, failed fits).",
     "how_to_check": "Run scpf_check_pool_coverage(table, model_col, dataset_col, value_col); report coverage next to every pooled figure.",
     "auto": "scpf_check_pool_coverage"},
    {"id": "SC-19", "area": "inputs", "severity": "major",
     "question": "Do results loaders reject retired/stale perturbation types and result files by their value, not only by a missing field?",
     "applies_if": "Result JSON/CSV files from earlier code versions can exist in the same directory as current outputs.",
     "how_to_check": "Run scpf_check_allowed_values(pert_types_in_files, allowed, name='perturbation type') over every loaded file; confirm baseline slices contain only current-run files.",
     "auto": "scpf_check_allowed_values"},
    {"id": "SC-20", "area": "runners", "severity": "blocking",
     "question": "Did any runner catch an exception and continue with a different method (e.g. refit on all cells instead of held-out projection, retry-then-ignore)?",
     "applies_if": "Model runners wrap fits/projections in try/except, retry-then-ignore, or version-sensitive library calls.",
     "how_to_check": "Run scpf_scan_fallbacks(log_text_or_path) on all runner logs and grep runner code for broad excepts around projection/transform; confirm every expected cell of the result grid exists.",
     "auto": "scpf_scan_fallbacks"},
    {"id": "SC-21", "area": "metrics", "severity": "minor",
     "question": "Are continuous factors (program scores, pseudotime, dose) kept as ordered axes, not binned into unordered classes for classifier-based metrics?",
     "applies_if": "A classifier- or MI-based disentanglement metric is applied to a continuous factor.",
     "how_to_check": "Confirm the metric variant used for continuous factors is regression/ordered; document any binning and its justification.",
     "auto": None},
    {"id": "SC-22", "area": "claims", "severity": "minor",
     "question": "Is every statement that a metric is standard in single-cell benchmarking backed by a cited single-cell source?",
     "applies_if": "A metric suite is justified by field convention in a plan, README or manuscript.",
     "how_to_check": "Find a single-cell benchmark paper using the metric (open the full text); otherwise word it as borrowed from general ML disentanglement.",
     "auto": None},
]


def scpf_items():
    """Return the SC-NN checklist items (shared schema, see experiment-preflight)."""
    return [dict(i) for i in SCPF_ITEMS]


def scpf_obs(x):
    """Return the obs DataFrame of an AnnData-like object, or x if it is a DataFrame."""
    if isinstance(x, pd.DataFrame):
        return x
    obs = getattr(x, "obs", None)
    if isinstance(obs, pd.DataFrame):
        return obs
    raise TypeError("scpf_obs: expected an AnnData-like object (with .obs DataFrame) or a pandas DataFrame, got %s" % type(x).__name__)


def scpf_names(x):
    """Return a list of str gene names from AnnData-like (.var_names), Index, Series, list or array."""
    vn = getattr(x, "var_names", None)
    if vn is not None:
        x = vn
    names = [str(v) for v in list(x)]
    if len(names) == 0:
        raise ValueError("scpf_names: empty gene list; refusing to pass an empty panel")
    return names


def scpf_matrix(x):
    """Return the expression matrix (dense ndarray or scipy sparse) from AnnData-like, DataFrame or array."""
    if isinstance(x, pd.DataFrame):
        return x.values
    if hasattr(x, "X") and not isinstance(x, np.ndarray):
        return x.X
    return x


def scpf_finish(item, check, problems, warnings, details, strict, exc=None):
    """Assemble the result dict; raise `exc` listing problems when strict and any problem exists."""
    res = {"ok": len(problems) == 0, "item": item, "check": check,
           "problems": list(problems), "warnings": list(warnings), "details": details}
    if exc is None:
        exc = ValueError
    if strict and problems:
        shown = "; ".join(problems[:6])
        more = "" if len(problems) <= 6 else " (+%d more)" % (len(problems) - 6)
        raise exc("[%s] %s FAILED: %s%s" % (item, check, shown, more))
    return res


def scpf_evidence(result):
    """One-line evidence string for pf_answer() from a check result."""
    d = result["details"]
    keys = [k for k in d if isinstance(d[k], (int, float, str, bool))][:6]
    brief = ", ".join("%s=%s" % (k, d[k]) for k in keys)
    status = "PASS" if result["ok"] else "FAIL"
    extra = ""
    if result["problems"]:
        extra = " | problems: " + "; ".join(result["problems"][:3])
    if result["warnings"]:
        extra += " | warnings: " + "; ".join(result["warnings"][:2])
    return "%s %s %s (%s)%s" % (status, result["item"], result["check"], brief, extra)


def scpf_need_cols(df, cols, where):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError("%s: column(s) %s not found by name; available: %s" % (where, missing, list(df.columns)[:30]))


def scpf_check_split_leakage(adata, group_col, split_col, cohort_col=None, strict=True):
    """SC-03: fail if any group (donor) has cells in more than one split value (train/test).

    group_col: donor/patient column; cohort_col (optional) builds the composite key cohort.group.
    split_col: obs column holding the realized split labels (e.g. 'train'/'test').
    """
    obs = scpf_obs(adata)
    need = [group_col, split_col] + ([cohort_col] if cohort_col else [])
    scpf_need_cols(obs, need, "scpf_check_split_leakage")
    if len(obs) == 0:
        raise ValueError("scpf_check_split_leakage: empty obs")
    key = obs[group_col].astype(str)
    if cohort_col:
        key = obs[cohort_col].astype(str) + "." + key
    sides = obs[split_col].astype(str).groupby(key.values).nunique()
    n_sides = obs[split_col].astype(str).nunique()
    problems = []
    if n_sides < 2:
        problems.append("split column %r has only %d value(s); no held-out side exists" % (split_col, n_sides))
    straddle = sides[sides > 1]
    if len(straddle):
        problems.append("%d of %d groups appear in more than one split (e.g. %s)" % (len(straddle), len(sides), list(straddle.index[:5])))
    details = {"n_groups": int(len(sides)), "n_straddling": int(len(straddle)), "n_split_values": int(n_sides),
               "straddling_examples": [str(i) for i in straddle.index[:10]]}
    return scpf_finish("SC-03", "scpf_check_split_leakage", problems, [], details, strict)


def scpf_check_donor_split(adata, label_cols, donor_col, cohort_col=None, sample_col=None, split_col=None,
                           min_donors=3, donor_level_frac=0.5, strict=True):
    """SC-01/SC-02: detect donor-level labels, cross-cohort donor-id collisions and (optionally) split leakage.

    A label is donor-level when >= donor_level_frac of donors carry exactly one value of it. For such labels a
    donor-grouped split is required. Conflicting values for the same donor id (when most donors are single-valued)
    indicate that ids collide across cohorts; group by the composite key (cohort_col) instead.
    split_col, if given, is the realized split and is checked for donor straddling (SC-03).
    """
    obs = scpf_obs(adata)
    labels = [label_cols] if isinstance(label_cols, str) else list(label_cols)
    extra = [c for c in (cohort_col, sample_col, split_col) if c]
    scpf_need_cols(obs, [donor_col] + labels + extra, "scpf_check_donor_split")
    if len(obs) == 0:
        raise ValueError("scpf_check_donor_split: empty obs")
    problems, warnings = [], []
    if obs[donor_col].isna().any():
        problems.append("donor column %r has %d missing values" % (donor_col, int(obs[donor_col].isna().sum())))
    n_donors = int(obs[donor_col].nunique())
    if n_donors < min_donors:
        problems.append("only %d distinct %r values (min_donors=%d); donor-grouped evaluation impossible" % (n_donors, donor_col, min_donors))
    per_label = {}
    needs_grouped = []
    for lab in labels:
        per = obs.groupby(donor_col, observed=True)[lab].nunique(dropna=True)
        frac_single = float((per <= 1).mean())
        n_conf = int((per > 1).sum())
        donor_level = frac_single >= donor_level_frac
        per_label[lab] = {"donor_level": bool(donor_level), "frac_donors_single_valued": round(frac_single, 4),
                          "donors_with_conflicting_values": n_conf, "n_classes": int(obs[lab].nunique())}
        if obs[lab].nunique() < 2:
            problems.append("label %r has fewer than 2 classes" % lab)
        if donor_level:
            needs_grouped.append(lab)
            if n_conf > 0:
                bad = [str(i) for i in per[per > 1].index[:5]]
                problems.append("label %r is donor-level but %d donor id(s) carry several values (e.g. %s): ids probably collide across cohorts; use a composite cohort.donor key" % (lab, n_conf, bad))
    coll_ids = []
    if cohort_col:
        nco = obs.groupby(donor_col, observed=True)[cohort_col].nunique()
        coll_ids = [i for i in nco[nco > 1].index]
        if coll_ids:
            conflict = []
            for lab in labels:
                sub = obs[obs[donor_col].isin(coll_ids)]
                v = sub.groupby(donor_col, observed=True)[lab].nunique()
                conflict += [str(i) for i in v[v > 1].index]
            if conflict:
                problems.append("bare donor id(s) %s occur in several cohorts with different labels; group by composite cohort.donor" % sorted(set(conflict))[:5])
            else:
                warnings.append("%d donor id(s) occur in several cohorts (e.g. %s); use the composite cohort.donor key for grouping" % (len(coll_ids), [str(i) for i in coll_ids[:3]]))
    else:
        warnings.append("cohort_col not given: cross-cohort id collisions only detectable through conflicting label values")
    n_samples = None
    if sample_col:
        n_samples = int(obs[sample_col].nunique())
        nd = obs.groupby(sample_col, observed=True)[donor_col].nunique()
        mixed = nd[nd > 1]
        if len(mixed):
            problems.append("%d %r level(s) map to more than one %r (e.g. %s); sample-to-donor mapping is not one-to-one" % (len(mixed), sample_col, donor_col, [str(i) for i in mixed.index[:5]]))
        if n_samples > n_donors:
            warnings.append("%d samples come from %d donors: hold out by donor, not by sample" % (n_samples, n_donors))
    if split_col:
        r = scpf_check_split_leakage(obs, donor_col, split_col, cohort_col=cohort_col, strict=False)
        if r["problems"]:
            if needs_grouped:
                problems += ["donor-level label(s) %s with leaky split: %s" % (needs_grouped, p) for p in r["problems"]]
            else:
                warnings += ["split not donor-grouped: %s" % p for p in r["problems"]]
    elif needs_grouped:
        warnings.append("donor-level label(s) %s found: a donor-grouped split is required; pass split_col to verify the realized split" % needs_grouped)
    details = {"n_donors": n_donors, "n_samples": n_samples, "donor_level_labels": needs_grouped,
               "per_label": per_label, "colliding_ids": [str(i) for i in coll_ids[:10]]}
    return scpf_finish("SC-01/02", "scpf_check_donor_split", problems, warnings, details, strict)


def scpf_check_split_fn(split_fn, group_ids, n_seeds=10, test_frac=0.2, tol=0.05, check_optimal=False, opt_tol=0.02, strict=True):
    """SC-04: empirically test a grouped split function over seeds.

    split_fn(group_ids: ndarray, seed: int) -> boolean array, True = test cell.
    Fails when a group straddles train/test, or the test fraction misses test_frac by more than tol.
    check_optimal=True also fails if the achieved fraction is worse than the best whole-group partition by > opt_tol.
    Details include per-group test rate across seeds (use it to verify 'deterministic side' claims).
    """
    g = np.asarray(group_ids)
    if g.size == 0 or n_seeds < 1:
        raise ValueError("scpf_check_split_fn: need non-empty group_ids and n_seeds >= 1")
    sizes = pd.Series(g).value_counts()
    problems, fracs, straddled = [], [], {}
    rates = pd.Series(0.0, index=sizes.index)
    for seed in range(n_seeds):
        mask = np.asarray(split_fn(g, seed), dtype=bool)
        if mask.shape != g.shape:
            raise ValueError("scpf_check_split_fn: split_fn returned shape %s, expected %s" % (mask.shape, g.shape))
        cnt = pd.Series(mask).groupby(g).sum()
        part = cnt[(cnt > 0) & (cnt < sizes.reindex(cnt.index))]
        if len(part):
            straddled[seed] = [str(i) for i in part.index[:3]]
        rates = rates.add((cnt > 0).astype(float).reindex(rates.index).fillna(0.0), fill_value=0.0)
        fracs.append(float(mask.mean()))
    rates = rates / n_seeds
    if straddled:
        problems.append("groups straddle train/test in %d of %d seeds (e.g. %s)" % (len(straddled), n_seeds, list(straddled.items())[:2]))
    worst = max(abs(f - test_frac) for f in fracs)
    if worst > tol:
        problems.append("test fraction ranged %.3f-%.3f, misses target %.2f by up to %.3f (tol %.3f)" % (min(fracs), max(fracs), test_frac, worst, tol))
    total = int(sizes.sum())
    reach = 1
    for s in sizes.values:
        reach |= reach << int(s)
    best_gap = min(abs(s / total - test_frac) for s in range(total + 1) if (reach >> s) & 1) if total <= 200000 else None
    opt_gap = None
    if best_gap is not None:
        opt_gap = max(abs(f - test_frac) for f in fracs) - best_gap
        if check_optimal and opt_gap > opt_tol:
            problems.append("achieved fraction is up to %.3f worse than the best whole-group partition (opt_tol %.3f)" % (opt_gap, opt_tol))
    unstable = {str(k): round(float(v), 2) for k, v in rates.items() if 0.0 < v < 1.0}
    details = {"n_seeds": n_seeds, "frac_min": min(fracs), "frac_max": max(fracs), "best_possible_gap": best_gap,
               "optimality_gap": opt_gap, "groups_changing_side_across_seeds": len(unstable),
               "group_test_rate_unstable": dict(list(unstable.items())[:20])}
    return scpf_finish("SC-04", "scpf_check_split_fn", problems, [], details, strict)


def scpf_classify_gene(name):
    """Classify a gene id/symbol as 'human', 'mouse', 'other' (non human/mouse Ensembl) or 'unknown'."""
    s = str(name).strip()
    if s.startswith("ENSMUSG"):
        return "mouse"
    if s.startswith("ENSG"):
        return "human"
    if re.match(r"^ENS[A-Z]{2,6}G\d{6,}", s):
        return "other"
    if re.match(r"^C(\d+|[XY])orf\d+$", s):
        return "human"
    if re.search(r"[a-z]", s):
        return "mouse"
    if re.search(r"[A-Z]", s):
        return "human"
    return "unknown"


def scpf_check_species(x, expected="human", max_other_frac=0.01, min_classified_frac=0.5, strict=True):
    """SC-05: fail when more than max_other_frac of gene ids/symbols look like another species.

    x: AnnData-like, Index or list of names. Heuristics: ENSG vs ENSMUSG prefixes; symbols with lower-case letters
    are mouse-style, all-upper are human-style (C#orf# is human). Mouse symbols that are all-caps are missed, so
    prefer Ensembl ids or an orthology table when available.
    """
    if expected not in ("human", "mouse"):
        raise ValueError("scpf_check_species: expected must be 'human' or 'mouse', got %r" % (expected,))
    names = scpf_names(x)
    classes = pd.Series([scpf_classify_gene(n) for n in names])
    counts = {k: int(v) for k, v in classes.value_counts().items()}
    n = len(names)
    problems, warnings = [], []
    classified = n - counts.get("unknown", 0)
    if classified / n < min_classified_frac:
        problems.append("only %d of %d names could be assigned a species; cannot validate (inspect var_names)" % (classified, n))
    off = classes[(classes != expected) & (classes != "unknown")]
    frac = len(off) / n
    if frac > max_other_frac:
        problems.append("%.1f%% of %d features are not %s (%s); e.g. %s" % (100 * frac, n, expected, {k: v for k, v in counts.items() if k != expected}, [names[i] for i in off.index[:5]]))
    elif len(off):
        warnings.append("%d feature(s) not %s (below tolerance): %s" % (len(off), expected, [names[i] for i in off.index[:5]]))
    style = "ensembl" if all(str(v).startswith("ENS") for v in names[:50]) else "symbol"
    details = {"n_features": n, "expected": expected, "counts": counts, "frac_other": round(frac, 4), "id_style": style,
               "offenders": [names[i] for i in off.index[:20]]}
    return scpf_finish("SC-05", "scpf_check_species", problems, warnings, details, strict)


def scpf_check_vocab_coverage(var_names, vocab, min_frac=0.5, model="model", strict=True):
    """SC-06: fail when fewer than min_frac of the dataset's genes are in the model's gene vocabulary.

    Reports exact, case-insensitive and Ensembl-version-stripped overlap to diagnose case or id-type mismatch.
    """
    names = scpf_names(var_names)
    vocab_l = [str(v) for v in vocab]
    if len(vocab_l) == 0:
        raise ValueError("scpf_check_vocab_coverage: empty vocabulary for %s" % model)
    vs = set(vocab_l)
    exact = sum(1 for n in names if n in vs)
    vci = {v.lower() for v in vocab_l}
    ci = sum(1 for n in names if n.lower() in vci)
    vstrip = {v.split(".")[0] for v in vocab_l}
    stripped = sum(1 for n in names if n.split(".")[0] in vstrip)
    n = len(names)
    frac = exact / n
    problems, warnings = [], []
    if frac < min_frac:
        hint = ""
        if max(ci, stripped) / n > frac + 0.2:
            hint = " (overlap rises to %.0f%% ignoring case / Ensembl version: id format mismatch)" % (100 * max(ci, stripped) / n)
        problems.append("%s vocabulary covers %.1f%% of %d genes (min_frac %.2f)%s; missing e.g. %s" % (model, 100 * frac, n, min_frac, hint, [v for v in names if v not in vs][:5]))
    details = {"model": model, "n_genes": n, "n_in_vocab": exact, "frac_exact": round(frac, 4),
               "frac_case_insensitive": round(ci / n, 4), "frac_version_stripped": round(stripped / n, 4)}
    return scpf_finish("SC-06", "scpf_check_vocab_coverage", problems, warnings, details, strict)


def scpf_check_noop_perturbation(raw, perturbed, atol=0.0, strict=True):
    """SC-07: the zero-intensity perturbation must equal the raw input (same shape, same names, |diff| <= atol).

    raw / perturbed: AnnData-like, DataFrame, ndarray or scipy sparse. Reports max |diff| and number of changed entries.
    """
    a, b = scpf_matrix(raw), scpf_matrix(perturbed)
    problems = []
    details = {}
    if a.shape != b.shape:
        problems.append("shape changed %s -> %s (cells/genes dropped or added at zero intensity)" % (a.shape, b.shape))
        return scpf_finish("SC-07", "scpf_check_noop_perturbation", problems, [], {"shape_raw": a.shape, "shape_perturbed": b.shape}, strict)
    for attr in ("obs_names", "var_names"):
        ra, rb = getattr(raw, attr, None), getattr(perturbed, attr, None)
        if ra is not None and rb is not None and list(ra) != list(rb):
            problems.append("%s differ between raw and perturbed" % attr)
    import scipy.sparse as sp
    if sp.issparse(a) or sp.issparse(b):
        d = (sp.csr_matrix(a, dtype=float) - sp.csr_matrix(b, dtype=float)).tocsr()
        d.eliminate_zeros()
        dv = np.abs(d.data)
    else:
        diff = np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float))
        both_nan = np.isnan(np.asarray(a, dtype=float)) & np.isnan(np.asarray(b, dtype=float))
        diff[both_nan] = 0.0
        dv = diff[diff != 0]
    bad = int((~(dv <= atol)).sum()) if dv.size else 0
    mx = float(np.nanmax(dv)) if dv.size else 0.0
    if bad:
        problems.append("zero-intensity output differs from raw in %d entries (max |diff| %.6g, atol %.3g): baseline is not a no-op" % (bad, mx, atol))
    details.update({"shape": tuple(int(s) for s in a.shape), "n_entries_differing": bad, "max_abs_diff": mx})
    return scpf_finish("SC-07", "scpf_check_noop_perturbation", problems, [], details, strict)


def scpf_check_cell_counts(table, axis_col, count_col="n_cells", group_cols=None, max_ratio=2.0, extra_cols=None, strict=True):
    """SC-08: fail when the retained sample size (or another per-level statistic) varies more than max_ratio along an axis.

    table: DataFrame or list of dicts with one row per (group, axis level). group_cols: e.g. ['dataset', 'perturbation'].
    extra_cols: other positive statistics to bound the same way, e.g. ['median_genes_per_cell'].
    """
    df = pd.DataFrame(table)
    cols = [count_col] + list(extra_cols or [])
    gcols = [group_cols] if isinstance(group_cols, str) else list(group_cols or [])
    scpf_need_cols(df, [axis_col] + cols + gcols, "scpf_check_cell_counts")
    if df.empty:
        raise ValueError("scpf_check_cell_counts: empty table")
    groups = df.groupby(gcols, observed=True) if gcols else [("all", df)]
    problems, worst = [], {}
    for key, sub in groups:
        if sub[axis_col].nunique() < 2:
            raise ValueError("scpf_check_cell_counts: group %r has <2 levels of %r" % (key, axis_col))
        for c in cols:
            v = sub[c].astype(float)
            lo, hi = float(v.min()), float(v.max())
            ratio = float("inf") if lo <= 0 else hi / lo
            worst[(str(key), c)] = ratio
            if ratio > max_ratio:
                at_lo = sub.loc[v.idxmin(), axis_col]
                problems.append("%s %s varies %.1fx along %s (%.4g -> %.4g, min at %s; max_ratio %.1f)" % (key, c, ratio, axis_col, hi, lo, at_lo, max_ratio))
    top = sorted(worst.items(), key=lambda kv: -kv[1])[:10]
    details = {"max_ratio_allowed": max_ratio, "n_groups_checked": len(worst),
               "worst": {"%s|%s" % k: (round(v, 2) if v != float("inf") else "inf") for k, v in top}}
    return scpf_finish("SC-08", "scpf_check_cell_counts", problems, [], details, strict)


def scpf_check_empty_cells(x, min_genes=1, strict=True):
    """SC-09: fail if any cell has fewer than min_genes non-zero genes (empty cells crash runners or vanish silently)."""
    X = scpf_matrix(x)
    if X.shape[0] == 0:
        raise ValueError("scpf_check_empty_cells: matrix has zero cells")
    nnz = np.asarray((X != 0).sum(axis=1)).ravel()
    n_bad = int((nnz < min_genes).sum())
    zero_genes = int((np.asarray((X != 0).sum(axis=0)).ravel() == 0).sum())
    problems = []
    if n_bad:
        problems.append("%d of %d cells have < %d expressed genes (%d fully empty)" % (n_bad, X.shape[0], min_genes, int((nnz == 0).sum())))
    warnings = ["%d genes have zero counts in every cell" % zero_genes] if zero_genes else []
    details = {"n_cells": int(X.shape[0]), "n_below_min_genes": n_bad, "n_empty_cells": int((nnz == 0).sum()), "n_zero_genes": zero_genes,
               "min_nnz": int(nnz.min())}
    return scpf_finish("SC-09", "scpf_check_empty_cells", problems, warnings, details, strict)


def scpf_check_gene_counts(n_genes_by_model, expected_n=None, hvg_n=None, n_vars_input=None, n_hvg_stages=None, tol=0, strict=True):
    """SC-10: gene counts reaching each model, and HVG configuration sanity.

    n_genes_by_model: dict key -> genes seen at model input (key may be 'dataset|model').
    expected_n: declared panel size; every entry must equal it within tol.
    hvg_n / n_vars_input: requested n_top_genes vs genes in the (already filtered) input; hvg_n >= n_vars_input
    makes HVG ranking degenerate. n_hvg_stages: how many pipeline stages re-apply HVG (must be 1).
    """
    if not n_genes_by_model:
        raise ValueError("scpf_check_gene_counts: n_genes_by_model is empty")
    vals = {str(k): int(v) for k, v in dict(n_genes_by_model).items()}
    problems = []
    if expected_n is not None:
        off = {k: v for k, v in vals.items() if abs(v - expected_n) > tol}
        if off:
            problems.append("%d entries differ from declared panel size %d: %s" % (len(off), expected_n, dict(list(off.items())[:6])))
    elif max(vals.values()) - min(vals.values()) > tol:
        problems.append("gene counts differ across models/datasets by %d (range %d-%d): %s" % (max(vals.values()) - min(vals.values()), min(vals.values()), max(vals.values()), dict(list(vals.items())[:6])))
    if hvg_n is not None and n_vars_input is not None and hvg_n >= n_vars_input:
        problems.append("n_top_genes=%d >= %d input genes: HVG ranking is degenerate (no-op or NaN-dispersion fallback) and can disable variance guards" % (hvg_n, n_vars_input))
    if n_hvg_stages is not None and n_hvg_stages != 1:
        problems.append("HVG selection applied in %d stages (must be exactly 1)" % n_hvg_stages)
    details = {"n_entries": len(vals), "min_genes": min(vals.values()), "max_genes": max(vals.values()),
               "expected_n": expected_n, "hvg_n": hvg_n, "n_vars_input": n_vars_input}
    return scpf_finish("SC-10", "scpf_check_gene_counts", problems, [], details, strict)


def scpf_check_obs_keys(adata, required, expected_values=None, level_bounds=None, used=None, strict=True):
    """SC-11: required obs columns must exist by name and hold plausible values.

    required: list of column names or {role: column}. expected_values: {column_or_role: iterable} that must be a subset
    of the observed values. level_bounds: {column_or_role: (min, max)} bounds on number of levels.
    used: {role: column your code actually resolved}; must equal required[role] (catches positional / heuristic picks).
    """
    obs = scpf_obs(adata)
    roles = dict(required) if isinstance(required, dict) else {c: c for c in required}
    scpf_need_cols(obs, list(roles.values()), "scpf_check_obs_keys")
    problems = []
    cols = list(roles.values())
    if len(set(cols)) != len(cols):
        dup = [c for c in set(cols) if cols.count(c) > 1]
        problems.append("several roles map to the same column: %s" % dup)
    for role, col in roles.items():
        if obs[col].isna().all():
            problems.append("column %r (role %s) is entirely missing" % (col, role))
    for key, vals in (expected_values or {}).items():
        col = roles.get(key, key)
        scpf_need_cols(obs, [col], "scpf_check_obs_keys.expected_values")
        miss = sorted(set(map(str, vals)) - set(obs[col].astype(str).unique()))
        if miss:
            problems.append("column %r lacks expected value(s) %s (observed e.g. %s): wrong column?" % (col, miss, list(obs[col].astype(str).unique()[:5])))
    for key, (lo, hi) in (level_bounds or {}).items():
        col = roles.get(key, key)
        scpf_need_cols(obs, [col], "scpf_check_obs_keys.level_bounds")
        nu = int(obs[col].nunique())
        if not (lo <= nu <= hi):
            problems.append("column %r has %d levels, outside [%s, %s]" % (col, nu, lo, hi))
    for role, col in (used or {}).items():
        if role in roles and roles[role] != col:
            problems.append("role %s resolved to column %r but %r was required (positional or heuristic selection?)" % (role, col, roles[role]))
    details = {"roles": roles, "n_levels": {c: int(obs[c].nunique()) for c in cols}}
    return scpf_finish("SC-11", "scpf_check_obs_keys", problems, [], details, strict)


def scpf_check_nesting(adata, factors, pairs=None, strict=True):
    """SC-13: find factor pairs where one factor deterministically determines the other.

    A determines B when every level of A co-occurs with exactly one level of B. Nested pairs yield arithmetic
    'collisions' or dependence that reflects study design, not model behaviour. pairs: list of (a, b) to test
    (default all unordered pairs). Fails if any tested pair is nested in either direction.
    """
    obs = scpf_obs(adata)
    fac = list(factors)
    scpf_need_cols(obs, fac, "scpf_check_nesting")
    if len(fac) < 2:
        raise ValueError("scpf_check_nesting: need at least two factors")
    todo = list(pairs) if pairs is not None else [(fac[i], fac[j]) for i in range(len(fac)) for j in range(i + 1, len(fac))]
    nested = []
    for a, b in todo:
        scpf_need_cols(obs, [a, b], "scpf_check_nesting")
        a_det_b = bool((obs.groupby(a, observed=True)[b].nunique() <= 1).all())
        b_det_a = bool((obs.groupby(b, observed=True)[a].nunique() <= 1).all())
        if a_det_b or b_det_a:
            nested.append({"pair": (a, b), "a_determines_b": a_det_b, "b_determines_a": b_det_a})
    problems = ["%s and %s are nested (%s): exclude from collision/dependence comparisons" % (n["pair"][0], n["pair"][1], "A->B" if n["a_determines_b"] and not n["b_determines_a"] else ("B->A" if n["b_determines_a"] and not n["a_determines_b"] else "identical partitions")) for n in nested]
    details = {"n_pairs_tested": len(todo), "n_nested": len(nested), "nested": nested}
    return scpf_finish("SC-13", "scpf_check_nesting", problems, [], details, strict)


def scpf_check_block_scoring(layouts, bases, strict=True):
    """SC-14: every model must be scored in its native concept basis.

    layouts: {model: None | blocks} where blocks (dict/list of block dims) is derived from the model source;
    None means no block structure. bases: {model: 'block' | 'per_dimension'} actually used by the metric run.
    Fails for block-structured models scored per-dimension, models in layouts without a basis, and models scored
    'block' without any declared block layout.
    """
    if not layouts:
        raise ValueError("scpf_check_block_scoring: layouts is empty")
    problems = []
    for m, lay in layouts.items():
        if m not in bases:
            problems.append("model %s has a layout but no recorded scoring basis" % m)
            continue
        basis = bases[m]
        if basis not in ("block", "per_dimension"):
            raise ValueError("scpf_check_block_scoring: basis for %s must be 'block' or 'per_dimension', got %r" % (m, basis))
        has_blocks = lay is not None and len(lay) > 1
        if has_blocks and basis == "per_dimension":
            problems.append("%s has %d concept blocks but was scored per-dimension (silent fallback or forced basis)" % (m, len(lay)))
        if lay is None and basis == "block":
            problems.append("%s has no block layout in its source but was scored 'block' (miscategorized)" % m)
    extra = sorted(set(bases) - set(layouts))
    if extra:
        problems.append("scored models without a layout entry: %s" % extra)
    details = {"n_models": len(layouts), "block_models": sorted(m for m, l in layouts.items() if l is not None and len(l) > 1)}
    return scpf_finish("SC-14", "scpf_check_block_scoring", problems, [], details, strict)


def scpf_check_block_pairing(blocks, factors, pairing, shared_blocks=None, strict=True):
    """SC-15: block -> factor pairing must be an explicit name map; unowned/shared blocks are credited to no factor.

    blocks: {block_name: dims}; factors: list of factor names; pairing: {block_name: factor_name | None};
    shared_blocks: block names that belong to no single factor (shared / residual subspace).
    """
    fac = set(factors)
    shared = set(shared_blocks or [])
    problems, warnings = [], []
    for b in pairing:
        if b not in blocks:
            problems.append("pairing mentions unknown block %r" % (b,))
    for b in blocks:
        if b not in pairing:
            problems.append("block %r has no pairing entry (use None for unowned)" % (b,))
    for b, f in pairing.items():
        if f is not None and f not in fac:
            problems.append("block %r paired with unknown factor %r" % (b, f))
        if b in shared and f is not None:
            problems.append("shared/unowned block %r is credited to factor %r" % (b, f))
    names = list(blocks)
    if len(names) > 1 and list(factors)[:len(names)] and dict(zip(names, factors)) == dict(pairing) and any(n != f for n, f in zip(names, factors)):
        warnings.append("pairing equals the positional zip of blocks and factors although names differ: confirm it is an intended name map")
    details = {"n_blocks": len(blocks), "n_factors": len(fac), "n_paired": sum(1 for f in pairing.values() if f is not None)}
    return scpf_finish("SC-15", "scpf_check_block_pairing", problems, warnings, details, strict)


def scpf_check_knn_validity(n_cells, k, kind="trustworthiness", strict=True):
    """SC-16: kNN metric parameter validity for every condition.

    n_cells: int or {condition: n}. kind: 'trustworthiness' (needs k < n/2), 'knn' (k < n), 'kbet' (k <= n).
    Invalid cells must be reported as NaN + status, not computed.
    """
    rules = {"trustworthiness": lambda n: k < n / 2.0, "knn": lambda n: k < n, "kbet": lambda n: k <= n}
    if kind not in rules:
        raise ValueError("scpf_check_knn_validity: kind must be one of %s, got %r" % (sorted(rules), kind))
    conds = n_cells if isinstance(n_cells, dict) else {"all": n_cells}
    if not conds:
        raise ValueError("scpf_check_knn_validity: no conditions given")
    bad = {str(c): int(n) for c, n in conds.items() if not rules[kind](n)}
    problems = []
    if bad:
        problems.append("%s with k=%d is invalid for %d condition(s) (n=%s); needs %s" % (kind, k, len(bad), dict(list(bad.items())[:5]), {"trustworthiness": "k < n/2", "knn": "k < n", "kbet": "k <= n"}[kind]))
    nmin = min(int(n) for n in conds.values())
    max_k = {"trustworthiness": int(np.ceil(nmin / 2.0)) - 1, "knn": nmin - 1, "kbet": nmin}[kind]
    details = {"kind": kind, "k": k, "n_min": nmin, "max_valid_k_at_n_min": max_k, "n_invalid_conditions": len(bad)}
    return scpf_finish("SC-16", "scpf_check_knn_validity", problems, [], details, strict)


def scpf_check_pool_coverage(table, model_col="model", dataset_col="dataset", value_col=None, allow_unbalanced=False, strict=True):
    """SC-18: models pooled across datasets must cover the same datasets.

    With value_col, also compares the naive per-model mean with the mean over the common datasets and reports
    whether model ranks change (rank_flip). Fails on unequal coverage unless allow_unbalanced=True.
    """
    df = pd.DataFrame(table)
    scpf_need_cols(df, [model_col, dataset_col] + ([value_col] if value_col else []), "scpf_check_pool_coverage")
    if df.empty:
        raise ValueError("scpf_check_pool_coverage: empty table")
    cov = df.groupby(model_col, observed=True)[dataset_col].apply(lambda s: sorted(set(map(str, s))))
    alld = sorted(set(map(str, df[dataset_col])))
    common = sorted(set.intersection(*[set(v) for v in cov.values]))
    partial = {m: v for m, v in cov.items() if len(v) < len(alld)}
    problems, warnings = [], []
    details = {"n_models": int(len(cov)), "n_datasets": len(alld), "common_datasets": common,
               "coverage": {str(m): len(v) for m, v in cov.items()}, "rank_flip": None}
    if partial:
        msg = "unequal dataset coverage: %s of %d datasets" % ({str(m): len(v) for m, v in partial.items()}, len(alld))
        (warnings if allow_unbalanced else problems).append(msg + "; pool over common datasets %s or show coverage" % common)
        if not common:
            problems.append("no dataset is covered by every model; pooled comparison impossible")
    if value_col and common:
        naive = df.groupby(model_col, observed=True)[value_col].mean()
        sub = df[df[dataset_col].astype(str).isin(common)]
        bal = sub.groupby(model_col, observed=True)[value_col].mean()
        flip = list(naive.rank(ascending=False).reindex(bal.index)) != list(bal.rank(ascending=False))
        details["rank_flip"] = bool(flip)
        if flip and partial:
            problems.append("model ranking changes between naive pooling and common-dataset pooling")
    return scpf_finish("SC-18", "scpf_check_pool_coverage", problems, warnings, details, strict)


def scpf_check_allowed_values(values, allowed, name="value", strict=True):
    """SC-19: every observed value must be in `allowed` (checks the value itself, not just field presence).

    values: iterable (e.g. perturbation type read from each result file; None for a missing field).
    """
    vals = list(values)
    if not vals:
        raise ValueError("scpf_check_allowed_values: no %s values supplied" % name)
    allowed_s = set(allowed)
    counts = {}
    for v in vals:
        if v not in allowed_s:
            counts[str(v)] = counts.get(str(v), 0) + 1
    problems = []
    if counts:
        problems.append("%d of %d %s entries not in allowed set %s: %s" % (sum(counts.values()), len(vals), name, sorted(map(str, allowed_s)), counts))
    details = {"n_checked": len(vals), "n_disallowed": sum(counts.values()), "disallowed": counts}
    return scpf_finish("SC-19", "scpf_check_allowed_values", problems, [], details, strict)


def scpf_scan_fallbacks(text_or_path, patterns=None, strict=True):
    """SC-20: scan runner logs (text, file path, or list of paths) for silent-fallback / swallowed-error markers."""
    pats = tuple(patterns) if patterns is not None else SCPF_FALLBACK_PATTERNS
    srcs = text_or_path if isinstance(text_or_path, (list, tuple)) else [text_or_path]
    hits, n_lines = [], 0
    for s in srcs:
        s = str(s)
        if "\n" not in s and os.path.isfile(s):
            with open(s, errors="replace") as fh:
                txt, label = fh.read(), s
        else:
            txt, label = s, "<text>"
        for i, line in enumerate(txt.splitlines(), 1):
            n_lines += 1
            for p in pats:
                if re.search(p, line, flags=re.I):
                    hits.append("%s:%d: %s" % (label, i, line.strip()[:160]))
                    break
    if n_lines == 0:
        raise ValueError("scpf_scan_fallbacks: nothing to scan (empty text / unreadable path)")
    problems = []
    if hits:
        problems.append("%d log line(s) indicate a fallback or swallowed error, e.g. %s" % (len(hits), hits[:3]))
    details = {"n_lines_scanned": n_lines, "n_hits": len(hits), "hits": hits[:20]}
    return scpf_finish("SC-20", "scpf_scan_fallbacks", problems, [], details, strict)
