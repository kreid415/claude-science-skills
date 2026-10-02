import copy
import json
import os
import re
import shlex
import math
import hashlib
import datetime
import numpy as np
import pandas as pd

PF_VERSION = "1.0"
PF_EXC_CACHE = {}
PF_STATUSES = ("pass", "fail", "n/a")
PF_SEVERITIES = ("blocking", "major", "minor")
PF_ITEM_KEYS = ("id", "area", "question", "severity", "applies_if", "how_to_check", "auto")
PF_MIN_EVIDENCE_CHARS = 15
PF_TRIVIAL_TEXT = ("n/a", "na", "none", "ok", "okay", "done", "pass", "passed", "yes", "checked", "lgtm", "looks good", "-", "?", "tbd", "todo", "fine")
PF_PLAN_REQUIRED = ("question", "primary_outcome", "unit_of_analysis", "split_unit", "selection_rule", "n_comparisons", "data_filters")


def pf_exc(name="PreflightError"):
    """Return (creating once) the exception class `name`; all derive from PreflightError."""
    if name not in PF_EXC_CACHE:
        if name == "PreflightError":
            PF_EXC_CACHE[name] = type(name, (Exception,), {})
        else:
            PF_EXC_CACHE[name] = type(name, (pf_exc("PreflightError"),), {})
    return PF_EXC_CACHE[name]


def pf_result(item, check, ok, summary, **details):
    """Uniform structured result of an auto-check."""
    out = {"item": item, "check": check, "ok": bool(ok), "summary": summary}
    out.update(details)
    return out


def pf_finish(result, strict=True, exc="PreflightError"):
    """Raise `exc` when the check failed and strict; otherwise return the result dict."""
    if strict and not result["ok"]:
        raise pf_exc(exc)("[%s %s] %s" % (result["item"], result["check"], result["summary"]))
    return result


def pf_as_list(x, name="ids"):
    if x is None or isinstance(x, (str, bytes)) or not hasattr(x, "__iter__"):
        raise pf_exc("InputError")("%s must be a non-string iterable, got %s" % (name, type(x).__name__))
    out = list(x)
    if not out:
        raise pf_exc("InputError")("%s is empty; an empty set cannot be checked" % name)
    return out


def pf_need_cols(df, cols, name="df"):
    if not hasattr(df, "columns"):
        raise pf_exc("InputError")("%s must be a pandas DataFrame, got %s" % (name, type(df).__name__))
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise pf_exc("InputError")("%s lacks column(s) %s; has %s" % (name, missing, list(df.columns)))
    if len(df) == 0:
        raise pf_exc("InputError")("%s has zero rows" % name)


def pf_to_array(x, name="x"):
    """Dense float ndarray from ndarray / DataFrame / scipy sparse; raise on non-numeric."""
    if hasattr(x, "toarray"):
        x = x.toarray()
    elif hasattr(x, "to_numpy"):
        x = x.to_numpy()
    a = np.asarray(x)
    if a.dtype.kind not in "biuf":
        raise pf_exc("InputError")("%s must be numeric, got dtype %s" % (name, a.dtype))
    return a.astype(float)


def pf_norm_value(v):
    """Canonical string for comparing a launch value (bool/number/str) across CLI and dict forms."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "none"
    s = str(v).strip()
    try:
        return format(float(s), ".12g")
    except ValueError:
        return s.lower()


def pf_norm_key(k):
    return str(k).strip().lstrip("-").replace("-", "_").lower()


def pf_parse_flags(command):
    """Parse '--flag value', '--flag=value', and bare '--flag' (True) from a command string."""
    toks = shlex.split(command)
    out = {}
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.startswith("--") and len(t) > 2:
            if "=" in t:
                k, v = t[2:].split("=", 1)
                out[pf_norm_key(k)] = v
            elif i + 1 < len(toks) and not toks[i + 1].startswith("--"):
                out[pf_norm_key(t)] = toks[i + 1]
                i += 1
            else:
                out[pf_norm_key(t)] = True
        i += 1
    return out


def pf_hash_plan(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()[:16]


def pf_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pf_items():
    """The generic checklist: list of item dicts (id, area, question, severity, applies_if, how_to_check, auto)."""
    rows = [
        ("PF-01", "Pre-declaration", "blocking", "pf_check_plan",
         "Are the question, ONE primary outcome, unit of analysis, split unit, selection rule and data filters written down before any result is seen?",
         "Always.",
         "Fill the plan dict given to pf_new; pf_check_plan rejects missing/placeholder keys and several primary outcomes, and pf_verdict returns NO-GO if the plan is edited afterwards (plan_sha). A number suggested in the brief is a hypothesis, not a result: derive values from data."),
        ("PF-02", "Multiple comparisons", "major", "pf_check_plan",
         "Is the number of comparisons declared and, if >1, is the correction (or an explicit 'exploratory, uncorrected' label) named?",
         "More than one model/metric/dataset/hyperparameter contrast will be tested or ranked.",
         "Set plan['n_comparisons'] and plan['multiple_comparison_correction'] (e.g. Holm, BH-FDR, or 'none: exploratory'). Secondary outcomes are labelled secondary in every table."),
        ("PF-03", "Unit of analysis", "blocking", "pf_check_grouped_split",
         "Does every unit of analysis (donor, patient, document, molecule family, site, run) fall entirely on one side of each train/test or fit/evaluate split?",
         "A classifier, probe, regressor or resampling scheme is evaluated on samples that are clustered in units.",
         "Map sample id -> unit and run pf_check_grouped_split(train_ids, test_ids, groups). Labels constant within a unit make a sample-level split leak the label outright."),
        ("PF-04", "ID integrity", "blocking", "pf_check_id_collisions",
         "Are unit identifiers unique and do they mean the same entity across sources, and does the assumed hierarchy (sample > subject > site) match the metadata?",
         "Data from more than one source/batch/study is merged, or units are rebuilt from a metadata column.",
         "pf_check_id_collisions(df, group_col, source_col); count distinct subjects per sample/batch level and compare with the assumption (never assume one batch = one subject)."),
        ("PF-05", "Nesting of factors", "major", "pf_check_nesting",
         "Are any labels/factors/covariates deterministic functions of one another (nesting), so that their 'overlap' or 'collision' is arithmetic rather than a result?",
         "Several categorical factors or labels are compared, or a factor is held out while another is predicted.",
         "pf_check_nesting(df, cols); acknowledge intended nesting via allow=[(parent, child)] and exclude nested pairs from pair counts."),
        ("PF-06", "Selection bias", "blocking", "pf_check_selection",
         "Is every selection (best hyperparameter, epoch, checkpoint, feature set) made on data/seeds disjoint from those used to report?",
         "Any 'best of' choice precedes a reported number or confidence interval.",
         "Select on a selection split or selection seeds; report on fresh seeds/held-out units. pf_check_selection(selection_ids, report_ids). Reporting CIs at the argmax of the same runs is winner's-curse bias."),
        ("PF-07", "Matched comparison", "blocking", "pf_check_matched_arms",
         "Do all arms get the same tuning budget, the same selection rule, the same hyperparameter policy (e.g. lambda), and differ only in the factor under test?",
         "Two or more methods/arms/heads are compared in one table or claim.",
         "Write each arm's policy as a dict and run pf_check_matched_arms(arm_policies, varied=[...]). Also compare arm capabilities that are not the factor (scaffolding, coverage, input modality, latent width vs published defaults)."),
        ("PF-08", "Aggregation", "blocking", "pf_check_coverage",
         "Does every method cover every unit (dataset/task/site) that enters a pooled mean, or is the pool restricted to the common units?",
         "Results are averaged or ranked across datasets/tasks/conditions.",
         "pf_check_coverage(df, unit_col, item_col, value_col); pool only over result['common_units'] or report per unit. Never a bare groupby(model).mean() on unequal coverage. Non-computable cells stay missing, never filled."),
        ("PF-09", "No-op baselines", "blocking", "pf_check_identity_baseline",
         "Is every 'zero-intensity', 'alpha=1', 'no perturbation', 'identity' baseline verified to return its input unchanged on the actual data?",
         "A perturbation, noise, preprocessing or transformation knob has a nominal off/neutral setting used as the reference.",
         "pf_check_identity_baseline(fn_at_neutral_setting, x_real, atol) on the real production input, not a toy. Differences at the neutral setting contaminate every comparison along that axis."),
        ("PF-10", "Sample size", "blocking", "pf_check_n_per_condition",
         "Is n per condition above the declared minimum, and does n stay comparable along each perturbation/difficulty axis (no silent collapse)?",
         "Conditions are created by filtering, subsampling, perturbing or stratifying the data.",
         "Tabulate n per cell after all filters: pf_check_n_per_condition(df, cond_cols, min_n, axis_col=..., max_ratio=...). Report n beside every metric; metrics fitted on very different n are not comparable."),
        ("PF-11", "Metric vs model structure", "major", None,
         "Does the metric's assumption (one dimension per factor, linear decoder, continuous score reflects separation, orientation) hold for every model structure in the comparison?",
         "A metric is applied to models with different latent structure (blocks, concepts, linear vs nonlinear, conditioned decoders).",
         "List each model's structure and the metric's implicit assumptions; run the metric on a model that satisfies the target trivially (e.g. a linear map) to see whether it scores high by construction. Substituting a proxy for an unavailable quantity needs an in-table caveat."),
        ("PF-12", "Metric implementation", "blocking", "pf_check_metric_known_answer",
         "Does the metric implementation reproduce the source equation/reference library on hand-computed known answers, including domain limits, NaN behaviour and sign/orientation?",
         "A new, rewritten or ported metric (or a metric from another library with different normalisation) is adopted.",
         "Write >=2 cases with known best/worst outputs (and one in the small-n/edge regime) and run pf_check_metric_known_answer. Compare against the reference implementation on shared inputs before trusting a convention."),
        ("PF-13", "Estimator noise", "major", None,
         "Is the metric's sampling variance (subsampling, seeds) smaller than the effects to be claimed, and is a shuffled/null reference reported?",
         "The metric uses subsampling, random projections, stochastic training or small n.",
         "Repeat the metric on the same fitted model with different draws; average over repeats if the numerator is noise-dominated; report a null (shuffled labels) alongside."),
        ("PF-14", "Test vs control", "major", None,
         "Does the design contain the actual test condition (not only controls), and is the metric non-circular (not satisfied by construction)?",
         "A new experiment design, probe or discovery filter is proposed.",
         "Label each arm as test or control in the plan; state what result would falsify the hypothesis. If a statistic is computed from the same data that defined the selection (e.g. top loadings co-expressed), it is circular."),
        ("PF-15", "Attribution", "major", None,
         "Are causal explanations for a result decomposed against built-in mechanisms (conditioning on the factor, capacity, preprocessing) before being stated?",
         "A mechanism or advantage is attributed to a method.",
         "List mechanisms by which the method could score well trivially and include an ablation or matched control for each, or word the claim as an association."),
        ("PF-16", "Timing confounds", "blocking", "pf_check_timing_repeats",
         "Are timing/throughput/memory benchmarks free of contention, warm-up/JIT, fixed per-run overhead and self-inflicted interruptions?",
         "A runtime, speed-up, scaling-efficiency or peak-memory number will be reported or used to plan.",
         "Run on an otherwise idle machine, record load, discard or separately report the first call, repeat >=3x, and pass the repeats to pf_check_timing_repeats. Separate startup cost from steady-state cost."),
        ("PF-17", "Cost estimate", "blocking", "pf_estimate_cost",
         "Is cost/walltime extrapolated from a pilot run at production settings, with a safety factor, and does each task's walltime exceed its estimate?",
         "Any job, sweep or wave that will use more than a few CPU/GPU-hours or needs an approval/walltime request.",
         "Run a pilot with the production settings (epochs, size, model, batch) and call pf_estimate_cost(pilot_s, pilot_units, total_units, safety, pilot_settings, production_settings, requested_walltime_s, units_per_task). Do not transfer a factor measured on one head/arm to another untested."),
        ("PF-18", "Co-scheduling", "major", None,
         "Is co-scheduling (concurrent lanes/processes) sized from measured per-item footprint (memory, VRAM), not from names or runtime alone?",
         "More than one process shares a GPU, node or memory pool.",
         "Measure peak memory per item size class; cap concurrency so the sum fits; size from true counts (e.g. number of rows), never from a dataset or subset name."),
        ("PF-19", "Launch manifest", "blocking", "pf_check_manifest",
         "Does every row of the launch manifest carry every intended flag with the intended value (nothing falls back to a default), and are boolean strings parsed as intended?",
         "A run, sweep or wave is launched from a generated manifest, script or command line.",
         "Write intended settings as a dict and run pf_check_manifest(rows, intended). A rewritten manifest generator is re-diffed against the previous one."),
        ("PF-20", "Count reconciliation", "major", "pf_check_manifest",
         "Do the number of rows/tasks/conditions in the manifest equal an independent count from the protocol (including exclusions), and do cost totals use that count?",
         "A grid, wave or sweep size feeds a cost estimate or a schedule.",
         "Count by executing the grid generator, not by regex over source text; pass expected_rows to pf_check_manifest; reconcile exclusions the protocol states."),
        ("PF-21", "Replicates vary", "major", "pf_check_replicates_differ",
         "Does each replicate (seed/resample) actually change the stochastic components, and does the launched script accept the seed argument?",
         "Results are aggregated over seeds, folds or resamples.",
         "Run two seeds on a small case and pf_check_replicates_differ(outputs). Grep the script for the seed parameter before promising seed-looping 'with no code changes'."),
        ("PF-22", "Output persistence", "blocking", None,
         "Does the run save everything required by every planned metric and re-analysis (embeddings, per-seed raw outputs, checkpoints), and do failed tasks exit non-zero and stay un-marked as done?",
         "The run is expensive to repeat (more than about an hour or a queue wait) or resumable.",
         "List the planned analyses and the files each needs; smoke-test one task end to end including the late stages (after early stopping, after final epoch); force a failure and confirm exit code and that --resume would retry it."),
        ("PF-23", "Provenance and inclusion", "blocking", "pf_check_inclusion",
         "Were the required inclusion filters (species, language, modality, license, time window, quality) applied and verified on the final feature/sample set, and is the data the one actually named?",
         "Inputs are built from a combined, multi-source or pre-processed reference, or a substitute dataset is used.",
         "Compute the category of every kept item and run pf_check_inclusion(df, col, allowed). Record accession/URL/version; state any substituted source before use."),
        ("PF-24", "Config vs input", "major", None,
         "Are parameters valid for the actual input (top-N <= available features, k < n/2, names legal for the framework) and do independently added guards still fire under this config?",
         "Parameters were carried over from another dataset/stage, or several guards/fixes were added separately.",
         "Compare every size-like parameter with the actual input dimension; sanitize categorical values used as identifiers; for each guard, run one config that should trip it and confirm it does."),
    ]
    out = []
    for r in rows:
        out.append({"id": r[0], "area": r[1], "question": r[4], "severity": r[2],
                    "applies_if": r[5], "how_to_check": r[6], "auto": r[3]})
    return out


def pf_check_plan(plan, strict=True):
    """PF-01/PF-02: plan declares the required keys, one primary outcome, and a correction if n_comparisons > 1."""
    if not isinstance(plan, dict):
        raise pf_exc("InputError")("plan must be a dict with keys %s" % (list(PF_PLAN_REQUIRED),))
    problems = []
    for k in PF_PLAN_REQUIRED:
        v = plan.get(k)
        if v is None or (isinstance(v, str) and (not v.strip() or v.strip().lower() in PF_TRIVIAL_TEXT)):
            problems.append("missing or placeholder plan['%s']" % k)
    po = plan.get("primary_outcome")
    if isinstance(po, (list, tuple, set)) and len(po) != 1:
        problems.append("declare exactly one primary outcome (got %d); list the rest as secondary_outcomes" % len(po))
    nc = plan.get("n_comparisons")
    multi_ok = True
    if nc is not None:
        if isinstance(nc, bool) or not isinstance(nc, int) or nc < 1:
            problems.append("plan['n_comparisons'] must be an integer >= 1, got %r" % (nc,))
        elif nc > 1:
            corr = plan.get("multiple_comparison_correction")
            if corr is None or not str(corr).strip() or str(corr).strip().lower() in PF_TRIVIAL_TEXT:
                multi_ok = False
                problems.append("n_comparisons=%d but plan['multiple_comparison_correction'] is missing (name a correction or 'none: exploratory')" % nc)
    ok = not problems
    summary = ("plan complete (sha %s)" % pf_hash_plan(plan)) if ok else "; ".join(problems)
    return pf_finish(pf_result("PF-01", "pf_check_plan", ok, summary, problems=problems,
                               multiple_comparisons_ok=multi_ok, plan_sha=pf_hash_plan(plan)),
                     strict, "PlanIncompleteError")


def pf_check_grouped_split(train_ids, test_ids, groups, strict=True):
    """PF-03: no unit (group) appears on both sides of a split; ids must not overlap either."""
    tr = pf_as_list(train_ids, "train_ids")
    te = pf_as_list(test_ids, "test_ids")
    if hasattr(groups, "to_dict") and hasattr(groups, "index"):
        if not groups.index.is_unique:
            raise pf_exc("InputError")("groups has a non-unique index; one sample id would map to several units")
        gmap = groups.to_dict()
    elif isinstance(groups, dict):
        gmap = groups
    else:
        raise pf_exc("InputError")("groups must be a dict or pandas Series mapping sample id -> unit")
    missing = [i for i in tr + te if i not in gmap]
    if missing:
        raise pf_exc("MissingGroupError")("%d sample ids have no unit in `groups` (e.g. %s); leakage cannot be assessed" % (len(missing), missing[:5]))
    id_overlap = set(tr) & set(te)
    gtr = {gmap[i] for i in tr}
    gte = {gmap[i] for i in te}
    shared = gtr & gte
    n_leaky = sum(1 for i in te if gmap[i] in gtr)
    warnings = []
    if len(gte) < 3:
        warnings.append("only %d test unit(s); estimates will be unstable" % len(gte))
    ok = not shared and not id_overlap
    if ok:
        summary = "no unit shared: %d train units, %d test units" % (len(gtr), len(gte))
    else:
        summary = ("%d of %d test units also occur in train (%d/%d test samples leak); %d sample ids in both splits; "
                   "split by unit, e.g. sklearn GroupShuffleSplit" % (len(shared), len(gte), n_leaky, len(te), len(id_overlap)))
    return pf_finish(pf_result("PF-03", "pf_check_grouped_split", ok, summary, shared_groups=sorted(shared, key=str)[:20],
                               n_shared_groups=len(shared), n_leaky_test_samples=n_leaky, n_id_overlap=len(id_overlap),
                               warnings=warnings), strict, "SplitLeakageError")


def pf_check_id_collisions(df, group_col, source_col, strict=True):
    """PF-04: a unit id occurring in >1 source is either the same entity (merge) or a collision (namespace it)."""
    pf_need_cols(df, [group_col, source_col])
    per = df.groupby(group_col)[source_col].nunique()
    bad = per[per > 1]
    ok = len(bad) == 0
    if ok:
        summary = "all %d unit ids occur in exactly one source" % len(per)
    else:
        summary = ("%d of %d unit ids occur in >1 source (e.g. %s): decide same-entity vs collision, then namespace as "
                   "'<source>:<id>' or merge" % (len(bad), len(per), list(bad.index[:5])))
    return pf_finish(pf_result("PF-04", "pf_check_id_collisions", ok, summary, n_groups=int(len(per)),
                               colliding_ids=[str(i) for i in bad.index[:50]], n_colliding=int(len(bad))),
                     strict, "IdCollisionError")


def pf_check_nesting(df, cols, allow=None, strict=True):
    """PF-05: report ordered pairs (a, b) where a determines b (each a-level has one b-level)."""
    cols = list(cols)
    if len(cols) < 2:
        raise pf_exc("InputError")("need >= 2 columns to test nesting")
    pf_need_cols(df, cols)
    allowed = {(str(a), str(b)) for a, b in (allow or [])}
    constant = [c for c in cols if df[c].nunique(dropna=False) < 2]
    use = [c for c in cols if c not in constant]
    nested = []
    for a in use:
        for b in use:
            if a == b:
                continue
            if (df.groupby(a, dropna=False)[b].nunique(dropna=False) <= 1).all():
                nested.append((a, b))
    unacknowledged = [p for p in nested if (str(p[0]), str(p[1])) not in allowed]
    ok = not unacknowledged
    warnings = ["constant column(s) ignored: %s" % constant] if constant else []
    summary = ("no unacknowledged nesting among %s" % use) if ok else (
        "deterministic nesting (a determines b): %s; their overlap is arithmetic; acknowledge via allow=[...] "
        "and exclude from pair counts / split by the parent" % ["%s>%s" % p for p in unacknowledged])
    return pf_finish(pf_result("PF-05", "pf_check_nesting", ok, summary, nested=nested, unacknowledged=unacknowledged,
                               warnings=warnings), strict, "NestedFactorError")


def pf_check_selection(selection_split_ids, report_split_ids, strict=True):
    """PF-06: ids (seeds/units/samples) used to select must be disjoint from ids used to report."""
    sel = set(pf_as_list(selection_split_ids, "selection_split_ids"))
    rep = set(pf_as_list(report_split_ids, "report_split_ids"))
    both = sel & rep
    ok = not both
    summary = ("selection and report sets disjoint (%d vs %d ids)" % (len(sel), len(rep))) if ok else (
        "%d of %d report ids were also used for selection (e.g. %s): winner's-curse bias; select on separate ids, "
        "report on fresh seeds/held-out units" % (len(both), len(rep), sorted(both, key=str)[:5]))
    return pf_finish(pf_result("PF-06", "pf_check_selection", ok, summary, n_overlap=len(both),
                               overlap=sorted(both, key=str)[:20]), strict, "SelectionBiasError")


def pf_check_matched_arms(arm_policies, varied=None, strict=True):
    """PF-07: all arms share every policy field (budget, selection rule, lambda policy...) except those in `varied`."""
    if not isinstance(arm_policies, dict) or len(arm_policies) < 2:
        raise pf_exc("InputError")("arm_policies must be a dict {arm: {field: value}} with >= 2 arms")
    var = set(varied or [])
    fields = []
    for p in arm_policies.values():
        if not isinstance(p, dict):
            raise pf_exc("InputError")("each arm policy must be a dict")
        for k in p:
            if k not in fields:
                fields.append(k)
    mismatches = {}
    for f in fields:
        if f in var:
            continue
        vals = {}
        for arm, p in arm_policies.items():
            vals[arm] = pf_norm_value(p[f]) if f in p else "<missing>"
        if len(set(vals.values())) > 1:
            mismatches[f] = vals
    unused = sorted(var - set(fields))
    ok = not mismatches
    summary = ("arms matched on %d fields (varied: %s)" % (len(fields) - len(var & set(fields)), sorted(var))) if ok else (
        "arms differ on non-varied field(s): %s" % "; ".join("%s=%s" % (k, v) for k, v in mismatches.items()))
    return pf_finish(pf_result("PF-07", "pf_check_matched_arms", ok, summary, mismatches=mismatches,
                               warnings=(["varied fields not present in any arm: %s" % unused] if unused else [])),
                     strict, "UnmatchedArmsError")


def pf_check_coverage(df, unit_col, item_col, value_col=None, strict=True):
    """PF-08: each item (method) must have a valid result on every unit (dataset) before pooling."""
    cols = [unit_col, item_col] + ([value_col] if value_col else [])
    pf_need_cols(df, cols)
    units = sorted(df[unit_col].unique(), key=str)
    items = list(df[item_col].unique())
    d = df if value_col is None else df[df[value_col].notna()]
    have = {k: set(g[unit_col]) for k, g in d.groupby(item_col)}
    missing = {}
    for item in items:
        miss = [u for u in units if u not in have.get(item, set())]
        if miss:
            missing[str(item)] = [str(u) for u in miss]
    common = [u for u in units if all(u in have.get(i, set()) for i in items)]
    ok = not missing
    summary = ("all %d items cover all %d units" % (len(items), len(units))) if ok else (
        "unequal coverage: %s; pool only over common units %s or report per unit"
        % ({k: "%d/%d" % (len(units) - len(v), len(units)) for k, v in missing.items()}, [str(u) for u in common]))
    return pf_finish(pf_result("PF-08", "pf_check_coverage", ok, summary, missing=missing,
                               common_units=[str(u) for u in common], n_units=len(units)), strict, "CoverageImbalanceError")


def pf_check_identity_baseline(fn, x, atol=0.0, rtol=0.0, strict=True):
    """PF-09: fn(x) at its neutral setting must return x within tolerance, with identical shape and no mutation."""
    x0 = pf_to_array(x, "x").copy()
    y = fn(x)
    a = pf_to_array(y, "fn(x)")
    mutated = not np.array_equal(pf_to_array(x, "x"), x0, equal_nan=True)
    if a.shape != x0.shape:
        res = pf_result("PF-09", "pf_check_identity_baseline", False,
                        "neutral setting changed shape %s -> %s" % (x0.shape, a.shape), shape_changed=True,
                        n_bad=int(x0.size), max_abs_diff=float("inf"), mutated_input=mutated)
        return pf_finish(res, strict, "NotAnIdentityError")
    with np.errstate(invalid="ignore"):
        same = (a == x0) | (np.isnan(a) & np.isnan(x0))
        close = np.abs(a - x0) <= (atol + rtol * np.abs(x0))
        d = np.abs(a - x0)
    good = same | (close & np.isfinite(a) & np.isfinite(x0))
    n_bad = int((~good).sum())
    max_diff = float("inf") if np.isnan(d[~good]).any() else (float(d[~good].max()) if n_bad else 0.0)
    ok = n_bad == 0 and not mutated
    parts = []
    if n_bad:
        parts.append("%d of %d entries differ from the input (max |diff| %.4g, atol %g)" % (n_bad, a.size, max_diff, atol))
    if mutated:
        parts.append("fn mutated its input in place")
    summary = ("identity verified on %d entries" % a.size) if ok else "baseline is not a no-op: " + "; ".join(parts)
    return pf_finish(pf_result("PF-09", "pf_check_identity_baseline", ok, summary, n_bad=n_bad, max_abs_diff=max_diff,
                               mutated_input=mutated, shape_changed=False), strict, "NotAnIdentityError")


def pf_check_n_per_condition(df, cond_cols, min_n=30, n_col=None, axis_col=None, max_ratio=None,
                             require_full_grid=True, strict=True):
    """PF-10: n per condition >= min_n, no empty grid cells, and bounded max/min n along `axis_col`."""
    cols = [cond_cols] if isinstance(cond_cols, str) else list(cond_cols)
    pf_need_cols(df, cols + ([n_col] if n_col else []))
    if min_n < 1:
        raise pf_exc("InputError")("min_n must be >= 1")
    g = df.groupby(cols, dropna=False)
    n = g.size() if n_col is None else g[n_col].sum()
    small = n[n < min_n]
    expected = 1
    for c in cols:
        expected *= df[c].nunique(dropna=False)
    n_missing = int(expected - len(n))
    ratio_viol = {}
    if axis_col is not None:
        if axis_col not in cols or max_ratio is None:
            raise pf_exc("InputError")("axis_col must be one of cond_cols and max_ratio must be given")
        others = [c for c in cols if c != axis_col]
        parts = [("all", n)] if not others else list(n.groupby(level=(others[0] if len(others) == 1 else others)))
        for key, s in parts:
            mn, mx = float(s.min()), float(s.max())
            r = float("inf") if mn <= 0 else mx / mn
            if r > max_ratio:
                ratio_viol[str(key)] = {"min_n": mn, "max_n": mx, "ratio": r}
    problems = []
    if len(small):
        problems.append("%d condition(s) below n=%d (smallest %d)" % (len(small), min_n, int(n.min())))
    if require_full_grid and n_missing > 0:
        problems.append("%d empty cell(s) in the %d-cell grid" % (n_missing, expected))
    if ratio_viol:
        worst = max(v["ratio"] for v in ratio_viol.values())
        problems.append("n collapses along '%s' by up to %.1fx (limit %s): metrics fitted on different n are not comparable"
                        % (axis_col, worst, max_ratio))
    ok = not problems
    summary = ("%d conditions, min n=%d" % (len(n), int(n.min()))) if ok else "; ".join(problems)
    return pf_finish(pf_result("PF-10", "pf_check_n_per_condition", ok, summary, n_min=int(n.min()), n_cells=int(len(n)),
                               small=[str(k) for k in small.index[:20]], n_empty_cells=n_missing,
                               ratio_violations=ratio_viol), strict, "SampleSizeError")


def pf_check_metric_known_answer(fn, cases, atol=1e-6, strict=True):
    """PF-12: fn(*args, **kwargs) must match hand-computed expected values; NaN, exceptions and constant output fail."""
    cases = pf_as_list(cases, "cases")
    if len(cases) < 2:
        raise pf_exc("InputError")("give >= 2 known-answer cases (e.g. best and worst); one case cannot detect a constant or flipped metric")
    for i, c in enumerate(cases):
        if "expected" not in c:
            raise pf_exc("InputError")("case %s has no 'expected'" % c.get("name", i))
    if len({pf_norm_value(c["expected"]) for c in cases}) < 2:
        raise pf_exc("InputError")("all known answers are identical; include a case whose answer differs")
    failures = []
    for i, c in enumerate(cases):
        name = c.get("name", "case%d" % i)
        try:
            out = fn(*c.get("args", ()), **c.get("kwargs", {}))
        except Exception as e:
            failures.append({"case": name, "problem": "raised %s: %s" % (type(e).__name__, e)})
            continue
        got = np.asarray(out, dtype=float)
        want = np.asarray(c["expected"], dtype=float)
        want_nan = bool(np.isnan(want).all())
        if np.isnan(got).any() and not want_nan:
            failures.append({"case": name, "problem": "returned NaN (expected %s): unmet precondition?" % c["expected"]})
        elif got.shape != want.shape and want.size != 1:
            failures.append({"case": name, "problem": "shape %s != expected %s" % (got.shape, want.shape)})
        elif not np.allclose(got, want, atol=atol, rtol=0.0, equal_nan=want_nan):
            failures.append({"case": name, "problem": "got %s, expected %s (atol %g)" % (np.round(got, 6).tolist(), np.round(want, 6).tolist(), atol)})
    ok = not failures
    summary = ("%d known-answer cases reproduced" % len(cases)) if ok else "%d of %d known-answer cases failed: %s" % (
        len(failures), len(cases), "; ".join("%s: %s" % (f["case"], f["problem"]) for f in failures))
    return pf_finish(pf_result("PF-12", "pf_check_metric_known_answer", ok, summary, failures=failures, n_cases=len(cases)),
                     strict, "MetricMismatchError")


def pf_check_timing_repeats(timings, max_rel_spread=0.25, warmup_ratio=1.5, strict=True):
    """PF-16: repeats of the same timed config must agree; a slow first call = warm-up/JIT, wide spread = contention."""
    t = [float(v) for v in pf_as_list(timings, "timings")]
    if len(t) < 3:
        raise pf_exc("InputError")("need >= 3 repeats of the same configuration, got %d" % len(t))
    if min(t) <= 0:
        raise pf_exc("InputError")("timings must be positive seconds")
    med = float(np.median(t))
    spread = (max(t) - min(t)) / med
    rest_med = float(np.median(t[1:]))
    warm = t[0] > warmup_ratio * rest_med
    wide = spread > max_rel_spread
    problems = []
    if warm:
        problems.append("first call %.3gs is %.1fx the median of the rest (warm-up/JIT/cache): drop or report separately" % (t[0], t[0] / rest_med))
    if wide and not warm:
        problems.append("relative spread %.0f%% > %.0f%% across repeats (contention or unstable machine): rerun on an idle host and record load"
                        % (100 * spread, 100 * max_rel_spread))
    ok = not problems
    summary = ("%d repeats agree (median %.3gs, spread %.0f%%)" % (len(t), med, 100 * spread)) if ok else "; ".join(problems)
    return pf_finish(pf_result("PF-16", "pf_check_timing_repeats", ok, summary, median_s=med, rel_spread=spread,
                               warmup_suspected=warm, contention_suspected=wide), strict, "TimingConfoundError")


def pf_estimate_cost(pilot_seconds, pilot_units, total_units, safety=2.0, pilot_settings=None, production_settings=None,
                     requested_walltime_s=None, units_per_task=None, parallel=1, cores=1, strict=True):
    """PF-17: extrapolate from a pilot; refuse unless the pilot ran at production settings; check requested walltime."""
    for nm, v in (("pilot_seconds", pilot_seconds), ("pilot_units", pilot_units), ("total_units", total_units)):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not v > 0:
            raise pf_exc("InputError")("%s must be a positive number, got %r" % (nm, v))
    if safety < 1 or parallel < 1 or cores < 1:
        raise pf_exc("InputError")("safety, parallel and cores must be >= 1")
    per_unit = pilot_seconds / pilot_units
    est = per_unit * total_units
    problems = []
    warnings = []
    if pilot_settings is None or production_settings is None:
        problems.append("pilot_settings and production_settings are required to prove the pilot ran at production settings")
    else:
        keys = set(pilot_settings) | set(production_settings)
        diff = {k: (pilot_settings.get(k, "<missing>"), production_settings.get(k, "<missing>")) for k in keys
                if pf_norm_value(pilot_settings.get(k, "<missing>")) != pf_norm_value(production_settings.get(k, "<missing>"))}
        if diff:
            problems.append("pilot differs from production settings: %s; the estimate is not valid" % diff)
    if total_units / pilot_units > 1000:
        warnings.append("extrapolating %.0fx beyond the pilot size" % (total_units / pilot_units))
    budget = est * safety
    wall_target = (per_unit * units_per_task * safety) if units_per_task else (budget / parallel)
    if requested_walltime_s is not None and requested_walltime_s < wall_target:
        problems.append("requested walltime %.0fs < estimated %.0fs (incl. safety %gx) for %s"
                        % (requested_walltime_s, wall_target, safety, "one task" if units_per_task else "the whole run"))
    ok = not problems
    rec = int(math.ceil(wall_target / 60.0) * 60)
    summary = ("estimate %.2f h (x%g safety = %.2f h); cpu-hours %.1f; recommended walltime %ds"
               % (est / 3600.0, safety, budget / 3600.0, est * cores / 3600.0, rec)) if ok else "; ".join(problems)
    return pf_finish(pf_result("PF-17", "pf_estimate_cost", ok, summary, per_unit_s=per_unit, estimate_s=est,
                               budget_s=budget, cpu_hours=est * cores / 3600.0, wall_hours=est / parallel / 3600.0,
                               recommended_walltime_s=rec, warnings=warnings), strict, "CostEstimateError")


def pf_check_manifest(manifest_rows, intended, expected_rows=None, strict=True):
    """PF-19/PF-20: every row has every intended flag with the intended value; optional row-count check."""
    rows = pf_as_list(manifest_rows, "manifest_rows")
    if not isinstance(intended, dict) or not intended:
        raise pf_exc("InputError")("intended must be a non-empty dict {flag: value}")
    want = {pf_norm_key(k): v for k, v in intended.items()}
    viol = []
    warns = set()
    by_flag = {}
    for i, r in enumerate(rows):
        if isinstance(r, str):
            have = pf_parse_flags(r)
        elif isinstance(r, dict):
            have = {pf_norm_key(k): v for k, v in r.items() if k != "command"}
            if isinstance(r.get("command"), str):
                have = dict(pf_parse_flags(r["command"]), **have)
        else:
            raise pf_exc("InputError")("manifest row %d must be a str command or a dict, got %s" % (i, type(r).__name__))
        for k, v in want.items():
            if k not in have:
                viol.append({"row": i, "flag": k, "intended": v, "actual": "<absent>"})
                by_flag[k] = by_flag.get(k, 0) + 1
            else:
                if isinstance(v, bool) and isinstance(have[k], str) and have[k].strip().lower() in ("true", "false"):
                    warns.add("flag '%s' is the string %r; many parsers treat any non-empty string as true; confirm the consumer parses it" % (k, have[k]))
                if pf_norm_value(have[k]) != pf_norm_value(v):
                    viol.append({"row": i, "flag": k, "intended": v, "actual": have[k]})
                    by_flag[k] = by_flag.get(k, 0) + 1
    problems = []
    if viol:
        problems.append("%d violation(s) in %d of %d rows; by flag %s; first: %s" % (
            len(viol), len({v["row"] for v in viol}), len(rows), by_flag, viol[0]))
    if expected_rows is not None and len(rows) != expected_rows:
        problems.append("manifest has %d rows, protocol expects %d" % (len(rows), expected_rows))
    ok = not problems
    summary = ("%d rows carry all %d intended flags" % (len(rows), len(want))) if ok else "; ".join(problems)
    return pf_finish(pf_result("PF-19", "pf_check_manifest", ok, summary, n_rows=len(rows), violations=viol[:50],
                               by_flag=by_flag, warnings=sorted(warns)), strict, "ManifestMismatchError")


def pf_check_replicates_differ(outputs, atol=0.0, strict=True):
    """PF-21: replicate outputs (one array per seed) must not all be identical within atol."""
    outs = [pf_to_array(o, "replicate output") for o in pf_as_list(outputs, "outputs")]
    if len(outs) < 2:
        raise pf_exc("InputError")("need >= 2 replicate outputs")
    if any(o.shape != outs[0].shape for o in outs):
        raise pf_exc("InputError")("replicate outputs have different shapes")
    reps = []
    for o in outs:
        if not any(np.allclose(o, r, atol=atol, rtol=0.0, equal_nan=True) for r in reps):
            reps.append(o)
    n_unique = len(reps)
    ok = n_unique > 1
    warnings = [] if (n_unique == len(outs) or not ok) else ["%d of %d replicates duplicate another" % (len(outs) - n_unique, len(outs))]
    summary = ("%d of %d replicates distinct" % (n_unique, len(outs))) if ok else (
        "all %d replicates are identical (atol %g): the seed does not reach the stochastic components or is not a parameter" % (len(outs), atol))
    return pf_finish(pf_result("PF-21", "pf_check_replicates_differ", ok, summary, n_unique=n_unique, warnings=warnings),
                     strict, "ReplicatesIdenticalError")


def pf_check_inclusion(df, col, allowed, max_frac_outside=0.0, strict=True):
    """PF-23: values of `col` (computed category per kept item) must lie in `allowed`; NaN counts as outside."""
    pf_need_cols(df, [col])
    al = set(pf_as_list(allowed, "allowed"))
    outside = ~df[col].isin(al)
    frac = float(outside.mean())
    ok = frac <= max_frac_outside
    top = df.loc[outside, col].astype(str).value_counts().head(5).to_dict()
    summary = ("all %d items inside %s" % (len(df), sorted(al, key=str))) if frac == 0 else (
        "%.1f%% of %d items fall outside %s (limit %.1f%%); top offenders %s" % (100 * frac, len(df), sorted(al, key=str), 100 * max_frac_outside, top))
    return pf_finish(pf_result("PF-23", "pf_check_inclusion", ok, summary, frac_outside=frac, n_outside=int(outside.sum()),
                               top_outside=top), strict, "InclusionFilterError")


def pf_validate_items(items, name="extra_items"):
    """Raise ItemSchemaError unless every item follows the shared checklist-item schema."""
    seen = set()
    for it in pf_as_list(items, name):
        if not isinstance(it, dict):
            raise pf_exc("ItemSchemaError")("%s: each item must be a dict, got %s" % (name, type(it).__name__))
        lacking = [k for k in PF_ITEM_KEYS if k not in it]
        if lacking:
            raise pf_exc("ItemSchemaError")("%s: item %s lacks keys %s" % (name, it.get("id", "?"), lacking))
        if it["severity"] not in PF_SEVERITIES:
            raise pf_exc("ItemSchemaError")("%s: item %s severity must be one of %s" % (name, it["id"], PF_SEVERITIES))
        if not isinstance(it["id"], str) or not it["id"].strip():
            raise pf_exc("ItemSchemaError")("%s: item id must be a non-empty string" % name)
        if it["id"] in seen:
            raise pf_exc("ItemSchemaError")("%s: duplicate item id %s" % (name, it["id"]))
        seen.add(it["id"])
    return True


def pf_all_items(record, extra_items=None):
    """Generic items + domain items registered on the record (+ `extra_items`, which get registered)."""
    generic = pf_items()
    gids = {i["id"] for i in generic}
    known = {i["id"]: i for i in record.get("extra_items", [])}
    if extra_items:
        pf_validate_items(extra_items)
        for it in extra_items:
            if it["id"] in gids:
                raise pf_exc("ItemSchemaError")("domain item id %s collides with a generic PF id" % it["id"])
            known[it["id"]] = it
    record["extra_items"] = list(known.values())
    return generic + record["extra_items"]


def pf_check_text(text, what):
    t = (text or "").strip() if isinstance(text, str) else ""
    if len(t) < PF_MIN_EVIDENCE_CHARS or t.lower() in PF_TRIVIAL_TEXT:
        raise pf_exc("EvidenceRequiredError")(
            "%s must be a concrete statement of what was checked and found (>= %d chars, e.g. command + result), got %r"
            % (what, PF_MIN_EVIDENCE_CHARS, text))
    return t


def pf_answer(record, item_id, status, evidence, extra_items=None):
    """Record pass/fail/n-a for an item. Evidence is required; 'n/a' needs a reason the applies_if condition is false."""
    if status not in PF_STATUSES:
        raise pf_exc("InputError")("status must be one of %s, got %r" % (PF_STATUSES, status))
    ids = {i["id"] for i in pf_all_items(record, extra_items)}
    if item_id not in ids:
        raise pf_exc("InputError")("unknown item id %r; known: %s ... (domain items must be registered via extra_items)" % (item_id, sorted(ids)[:5]))
    ev = pf_check_text(evidence, "evidence" if status != "n/a" else "n/a reason")
    prev = record["answers"].get(item_id)
    entry = {"status": status, "evidence": ev, "at": pf_now(), "history": []}
    if prev:
        entry["history"] = prev.get("history", []) + [{"status": prev["status"], "evidence": prev["evidence"], "at": prev["at"]}]
    record["answers"][item_id] = entry
    return record


def pf_record_check(record, item_id, result, extra_items=None):
    """Store an auto-check result dict (from any pf_check_*) as the answer: pass if ok else fail."""
    if not isinstance(result, dict) or "ok" not in result or "summary" not in result:
        raise pf_exc("InputError")("result must be a dict returned by a pf_check_* helper")
    pf_answer(record, item_id, "pass" if result["ok"] else "fail",
              "[auto:%s] %s" % (result.get("check", "?"), result["summary"]), extra_items)
    record["answers"][item_id]["auto"] = {k: result.get(k) for k in ("check", "ok", "summary", "warnings")}
    return record


def pf_verdict(record, extra_items=None):
    """GO only if every applicable blocking item passed with evidence and the plan is unchanged.

    Returns {'go', 'blocking' (blocking items failed or unanswered), 'unanswered' (all severities),
    'major_fail', 'minor_fail', 'plan_changed', 'n_items', 'n_pass', 'n_na'}.
    """
    items = pf_all_items(record, extra_items)
    ids = {i["id"] for i in items}
    stray = [k for k in record["answers"] if k not in ids]
    if stray:
        raise pf_exc("InputError")("answers exist for unregistered item ids %s; pass the domain pack via extra_items" % stray)
    blocking, unanswered, major_fail, minor_fail = [], [], [], []
    n_pass = n_na = 0
    for it in items:
        a = record["answers"].get(it["id"])
        if a is None:
            unanswered.append(it["id"])
            if it["severity"] == "blocking":
                blocking.append(it["id"])
        elif a["status"] == "fail":
            if it["severity"] == "blocking":
                blocking.append(it["id"])
            elif it["severity"] == "major":
                major_fail.append(it["id"])
            else:
                minor_fail.append(it["id"])
        elif a["status"] == "pass":
            n_pass += 1
        else:
            n_na += 1
    changed = pf_hash_plan(record["plan"]) != record["plan_sha"]
    v = {"go": (not blocking) and (not changed), "blocking": blocking, "unanswered": unanswered,
         "major_fail": major_fail, "minor_fail": minor_fail, "plan_changed": changed,
         "n_items": len(items), "n_pass": n_pass, "n_na": n_na, "at": pf_now()}
    record["verdict"] = v
    return v


def pf_open(record, extra_items=None):
    """Items still unanswered or failed, blocking first, each with its how_to_check text."""
    items = pf_all_items(record, extra_items)
    order = {"blocking": 0, "major": 1, "minor": 2}
    out = [i for i in items if record["answers"].get(i["id"], {}).get("status") in (None, "fail")]
    return sorted(out, key=lambda i: (order[i["severity"]], i["id"]))


def pf_cell(s, n=300):
    s = str(s).replace("|", "/").replace("\n", " ")
    return s if len(s) <= n else s[:n - 3] + "..."


def pf_render(record):
    """PREFLIGHT.md text for a record (verdict recomputed)."""
    v = pf_verdict(record)
    items = pf_all_items(record)
    lines = ["# PREFLIGHT: %s" % record["experiment_id"], "",
             "**Verdict: %s**  (checked %s; plan_sha %s%s)" % ("GO" if v["go"] else "NO-GO", v["at"], record["plan_sha"],
                                                              "; PLAN CHANGED AFTER DECLARATION" if v["plan_changed"] else ""), ""]
    if v["blocking"]:
        lines += ["Blocking, not satisfied: " + ", ".join(v["blocking"]), ""]
    if v["major_fail"]:
        lines += ["Major items failed (state in the report): " + ", ".join(v["major_fail"]), ""]
    lines += ["## Plan", ""]
    for k, val in record["plan"].items():
        lines.append("- **%s**: %s" % (k, pf_cell(val, 500)))
    lines += ["", "## Items", "", "| id | severity | area | status | evidence |", "|---|---|---|---|---|"]
    for it in items:
        a = record["answers"].get(it["id"])
        lines.append("| %s | %s | %s | %s | %s |" % (it["id"], it["severity"], it["area"], a["status"] if a else "UNANSWERED",
                                                    pf_cell(a["evidence"]) if a else ""))
    return "\n".join(lines) + "\n"


def pf_save(record):
    """Write preflight.json and PREFLIGHT.md into record['path']; return both paths."""
    d = record["path"]
    os.makedirs(d, exist_ok=True)
    md = pf_render(record)
    jp = os.path.join(d, "preflight.json")
    mp = os.path.join(d, "PREFLIGHT.md")
    with open(jp, "w") as f:
        json.dump(record, f, indent=2, default=str)
    with open(mp, "w") as f:
        f.write(md)
    return {"json": jp, "md": mp}


def pf_load(experiment_id, root="."):
    """Load experiments/<id>/preflight.json."""
    jp = os.path.join(root, "experiments", experiment_id, "preflight.json")
    if not os.path.exists(jp):
        raise pf_exc("RecordNotFoundError")("no preflight record at %s; create it with pf_new" % jp)
    with open(jp) as f:
        return json.load(f)


def pf_new(experiment_id, plan, root=".", extra_items=None, overwrite=False):
    """Create experiments/<id>/PREFLIGHT.md + preflight.json. Refuses an incomplete plan and never overwrites silently."""
    if not isinstance(experiment_id, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", experiment_id):
        raise pf_exc("InputError")("experiment_id must match [A-Za-z0-9][A-Za-z0-9_.-]*, got %r" % (experiment_id,))
    plan_res = pf_check_plan(plan, strict=True)
    d = os.path.join(root, "experiments", experiment_id)
    jp = os.path.join(d, "preflight.json")
    if os.path.exists(jp):
        if not overwrite:
            raise pf_exc("RecordExistsError")("%s exists; load it with pf_load, or pass overwrite=True to archive it and start a revised record" % jp)
        os.replace(jp, os.path.join(d, "preflight.%s.json" % pf_now().replace(":", "").replace("-", "")))
    record = {"experiment_id": experiment_id, "created_at": pf_now(), "pf_version": PF_VERSION, "plan": copy.deepcopy(plan),
              "plan_sha": plan_res["plan_sha"], "extra_items": [], "answers": {}, "verdict": None, "path": d}
    pf_all_items(record, extra_items)
    pf_record_check(record, "PF-01", plan_res)
    nc = plan.get("n_comparisons")
    pf_answer(record, "PF-02", "pass", "plan declares n_comparisons=%s; correction: %s" % (nc, plan.get("multiple_comparison_correction", "not needed for a single comparison")))
    pf_save(record)
    return record
