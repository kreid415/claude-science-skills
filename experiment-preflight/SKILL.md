---
name: experiment-preflight
description: "Pre-launch go/no-go gate for any computational experiment (ML, statistics, simulation, bioinformatics, NLP, imaging). Load BEFORE launching a run, sweep, wave, array job or benchmark, before adopting a new metric, and before quoting a cost, runtime or walltime. Triggers: 'ready to launch', 'kick off the sweep', 'submit the wave', 'is this metric valid', 'how long will this take', 'compare method A vs B', 'best lambda then report', 'train/test split', 'add a no-op baseline', 'benchmark the timing'. Checks split unit vs sample unit (leakage), winner's-curse selection, matched tuning across arms, unequal coverage in pooled means, identity baselines, sample-size collapse, metric vs source equation, timing confounds, pilot-based cost, launch flags vs intended settings, provenance filters. Writes experiments/ID/PREFLIGHT.md + preflight.json with a GO/NO-GO verdict; domain packs plug in."
---

# experiment-preflight

Runs **before compute is spent**. Produces a go/no-go record in `experiments/<id>/` (`PREFLIGHT.md` + `preflight.json`). Every checklist item traces to a real failure pattern it is designed to catch. The kernel helpers are generic and data-agnostic; domain-specific checks live in separate packs (`<prefix>_items()`), e.g. `experiment-preflight-singlecell` (prefix `sc_`).

Principle: a check that cannot fail is not a check. Helpers raise a specific exception on failure (`strict=True`, default) and return a result dict either way. `pass` needs evidence; `n/a` needs a reason.

## When to run

- Launching any run/sweep/wave/array job that costs more than a few CPU/GPU-hours or more than ~1 h of wall time.
- Adopting, rewriting or porting a metric.
- Before any cost, runtime, walltime or speed-up figure is given to the user or written into a request.
- Before a comparison table or claim that ranks methods/arms.
- Re-run on any launch after the manifest generator, grid, metric, split or filter changed.

## Workflow

1. Write the plan (pre-declaration), then create the record. `pf_new` refuses an incomplete plan and never overwrites silently:
   ```python
   plan = {"question": "...", "primary_outcome": "ONE metric, e.g. ARI on held-out donors",
           "unit_of_analysis": "donor", "split_unit": "donor",
           "selection_rule": "lambda chosen on seeds 0-4, reported on seeds 5-14",
           "n_comparisons": 3, "multiple_comparison_correction": "Holm",
           "data_filters": "human genes only; cells >= 200 genes"}
   rec = pf_new("07_lambda_sweep", plan, root=".")        # optional extra_items=sc_items()
   ```
2. List what is open: `for it in pf_open(rec): print(it["id"], it["severity"], it["question"], it["how_to_check"])`.
3. For each item whose `applies_if` is true, run its `auto` helper on the REAL inputs (not a toy) and record it: `pf_record_check(rec, "PF-03", pf_check_grouped_split(train, test, groups, strict=False))`. Items with `auto=None` are manual: do the check, then `pf_answer(rec, "PF-11", "pass", "<what you ran and saw>")`.
4. Items whose condition is false: `pf_answer(rec, "PF-16", "n/a", "no timing claim is made in this experiment")`.
5. Fix every `fail`, re-run the helper, re-record (the failed answer stays in `history`).
6. `v = pf_verdict(rec)` (pass `extra_items=` for a domain pack). Launch only if `v["go"]`; otherwise work through `v["blocking"]`. Failed *major* items do not block but are listed in `v["major_fail"]`: state them in the report.
7. `pf_save(rec)`, commit `experiments/<id>/PREFLIGHT.md` + `preflight.json` with the run code, and log GO/NO-GO as a DECISION in the lab notebook (`nb_entry`) when that skill is in use.

## Verdict rules

- GO iff every blocking item (generic + extra) is `pass` or `n/a` with a reason, **and** the plan hash equals the hash taken at `pf_new` (editing the plan after the fact gives NO-GO; revise with `pf_new(..., overwrite=True)`, which archives the old record).
- `v = {'go', 'blocking' (blocking items failed OR unanswered), 'unanswered' (all severities), 'major_fail', 'minor_fail', 'plan_changed', 'n_items', 'n_pass', 'n_na'}`.
- Evidence must be concrete (>= 15 chars, not 'ok'/'done'); a path to a log/notebook entry plus the number seen is best.

## Helpers (all in kernel.py; prefix `pf_`)

| helper | item | fails when |
|---|---|---|
| `pf_check_plan(plan)` | PF-01/02 | key missing/placeholder, >1 primary outcome, `n_comparisons>1` without correction |
| `pf_check_grouped_split(train_ids, test_ids, groups)` | PF-03 | a unit (group) or sample id is on both sides; unmapped ids raise `MissingGroupError` |
| `pf_check_id_collisions(df, group_col, source_col)` | PF-04 | a unit id occurs in more than one source |
| `pf_check_nesting(df, cols, allow=None)` | PF-05 | factor a deterministically sets factor b and the pair is not acknowledged |
| `pf_check_selection(selection_split_ids, report_split_ids)` | PF-06 | any id used to select is also used to report |
| `pf_check_matched_arms(arm_policies, varied=None)` | PF-07 | arms differ on a policy field not listed in `varied` (budget, selection rule, lambda policy, width) |
| `pf_check_coverage(df, unit_col, item_col, value_col=None)` | PF-08 | an item lacks a valid result on some unit; returns `common_units` |
| `pf_check_identity_baseline(fn, x, atol)` | PF-09 | `fn(x)` differs from `x`, changes shape, or mutates `x` |
| `pf_check_n_per_condition(df, cond_cols, min_n, n_col, axis_col, max_ratio)` | PF-10 | cell < `min_n`, empty grid cell, or max/min n along `axis_col` > `max_ratio` |
| `pf_check_metric_known_answer(fn, cases, atol)` | PF-12 | wrong value, NaN, exception, or fewer than 2 distinct known answers |
| `pf_check_timing_repeats(timings)` | PF-16 | first call >> rest (warm-up/JIT) or wide spread (contention); needs >= 3 repeats |
| `pf_estimate_cost(pilot_seconds, pilot_units, total_units, safety=2.0, pilot_settings, production_settings, requested_walltime_s, units_per_task)` | PF-17 | pilot settings differ from production (or are not given), or requested walltime < estimate x safety |
| `pf_check_manifest(manifest_rows, intended, expected_rows=None)` | PF-19/20 | an intended flag is absent or differs in any row, or row count differs; warns on boolean strings |
| `pf_check_replicates_differ(outputs)` | PF-21 | all replicate outputs identical |
| `pf_check_inclusion(df, col, allowed, max_frac_outside=0.0)` | PF-23 | share of items outside `allowed` exceeds the limit (NaN counts as outside) |

Record helpers: `pf_items()`, `pf_new`, `pf_load`, `pf_answer`, `pf_record_check`, `pf_verdict`, `pf_open`, `pf_save`, `pf_render`, `pf_validate_items`. Exceptions: `pf_exc("SplitLeakageError")` etc.; all subclass `pf_exc("PreflightError")`.

## Manual items (no auto helper)

PF-11 metric vs model structure; PF-13 estimator noise and null reference; PF-14 test vs control, circularity; PF-15 causal attribution; PF-18 co-scheduling from measured footprint; PF-22 output persistence and failure propagation; PF-24 config valid for the actual input. Full text and how-to-check: `pf_items()`; rationale per item: `references/checklist.md`.

## Pitfalls the helpers cannot see

- A helper only checks what you hand it. Pass the **production** table, split, manifest and input matrix; a toy fixture passing proves nothing.
- `groups` for PF-03 must be the true unit, built from verified metadata (PF-04), not a column assumed to be the subject.
- A pilot (PF-17) must exercise the late stages (after early stopping, final epoch, save/eval), not only the first iterations.
- A green PREFLIGHT is not a result check: still verify outputs after the run (rigor-review).

## Plugging in a domain pack (imaging, NLP, single-cell, ...)

A pack is a skill with its own `kernel.py` exposing `<prefix>_items()` that returns items in the **same schema** as `pf_items()`:

```python
{"id": "SC-01", "area": "...", "question": "...", "severity": "blocking"|"major"|"minor",
 "applies_if": "plain-language condition", "how_to_check": "...", "auto": "sc_check_x" or None}
```

- ids use the pack's prefix + number (`SC-`, `IMG-`, `NLP-`); they must not collide with `PF-`.
- Auto helpers follow the same contract: take real inputs, return `pf_result(...)`-shaped dict (`item`, `check`, `ok`, `summary`), raise a `pf_exc("<Name>Error")` when `strict=True` and failing. They may call `pf_finish` and `pf_result`.
- Users register the pack at creation or later: `pf_new(id, plan, extra_items=sc_items())`, then `pf_answer(rec, "SC-01", ..., extra_items=sc_items())` and `pf_verdict(rec, extra_items=sc_items())`. Answers for unregistered ids raise `InputError`, so a pack can never be silently skipped once registered.
- A pack's blocking items gate GO exactly like generic ones. Details and a skeleton: `references/domain-packs.md`.

## Provenance

The checks in this skill come from a review of real failures in AI-assisted research sessions. `tests/` in the repository holds synthetic fixtures that reproduce each failure pattern: known-bad inputs must fail, known-good inputs must pass.

## Interlocks

`reproducible` (the `experiments/NN_name/` folder holds PREFLIGHT.* beside the run script), `lab-notebook` (log the verdict), `rigor-review` (post-hoc review of outputs; this skill is the pre-hoc gate), `cluster-autoscout` (target choice happens after GO).

Tests: `experiment-preflight_tests.py` (synthetic fixtures reproducing the failure patterns above; known-bad raises, known-good passes).
