---
name: experiment-preflight-singlecell
description: "Single-cell, spatial and perturbation-omics domain pack for experiment-preflight (AnnData, scanpy, scvi-tools). Load it together with experiment-preflight BEFORE planning or launching an experiment that uses donor/patient-level labels, train/test or held-out splits, gene panels or HVG selection, foundation-model gene vocabularies, perturbation/noise/dropout axes, batch keys, disentanglement or kNN metrics, block-structured latents, or per-model averages pooled over datasets. Adds checklist items SC-01..SC-22 and scpf_ helpers (donor-grouped split and patient-ID collision, species ENSG vs ENSMUSG, zero-intensity no-op, cell-count collapse, obs keys by name, kNN validity, coverage-balanced pooling). Trigger on: preflight, sanity-check before launch, is this split leaky, is the panel human, is the baseline a no-op, before a cluster run on omics data."
---

# experiment-preflight-singlecell

Domain pack for `experiment-preflight`. The generic skill supplies the PF-NN
items, the per-experiment record and the GO/NO-GO verdict; this pack adds SC-NN
items that catch the failures seen in single-cell benchmarking work, and
`scpf_` helpers that answer many of them with a measurement instead of an
opinion. Each blocking SC item traces to a known failure pattern (see
`references/checklist.md`).

## Workflow

1. Load `experiment-preflight` and this skill (the sidecar defines `scpf_*`).
2. Create the record: `rec = pf_new(experiment_id, plan)` (generic skill).
3. Pick the applicable SC items: `for it in scpf_items(): print(it["id"], it["severity"], it["applies_if"])`.
   Mark non-applicable ones `n/a` with a reason; do not skip silently.
4. For every item with `auto`, run the helper on the real objects (the
   realized split, the final panel, the perturbed matrix), not on the code
   that is supposed to produce them. Record the result:
   `pf_answer(rec, "SC-05", "pass" if r["ok"] else "fail", scpf_evidence(r))`.
5. Items with `auto=None` need a manual answer with evidence (file path and
   line, table row, or source excerpt). Open the primary source; do not answer
   from recall.
6. `pf_save(rec)`; `v = pf_verdict(rec, extra_items=scpf_items())`. Launch only
   on `v["go"]`.

## Helper contract

Every `scpf_check_*` helper returns
`{"ok", "item", "check", "problems", "warnings", "details"}` and, by default
(`strict=True`), raises `ValueError` listing the problems (`KeyError` for a
missing column). Inputs: AnnData-like objects (duck-typed on `.obs`, `.X`,
`.var_names`) or plain pandas/numpy/scipy objects. Pass `strict=False` to
collect results for several checks, then answer each item from its result.
Empty input raises rather than passing. `scpf_evidence(result)` formats a
result as the one-line evidence string.

| Item | Helper | Fails when |
|---|---|---|
| SC-01/02 | `scpf_check_donor_split(adata, label_cols, donor_col, cohort_col=, sample_col=, split_col=)` | donor-level label with a leaky split; donor ids that collide across cohorts; sample maps to several donors |
| SC-03 | `scpf_check_split_leakage(adata, group_col, split_col, cohort_col=)` | a group has cells on both sides |
| SC-04 | `scpf_check_split_fn(split_fn, group_ids, n_seeds=10, check_optimal=)` | groups straddle, fraction misses target, claimed optimality false |
| SC-05 | `scpf_check_species(x, expected="human")` | > `max_other_frac` of features look like another species |
| SC-06 | `scpf_check_vocab_coverage(var_names, vocab, min_frac=, model=)` | vocabulary covers too little of the panel |
| SC-07 | `scpf_check_noop_perturbation(raw, perturbed)` | intensity-0 output differs from raw |
| SC-08 | `scpf_check_cell_counts(table, axis_col, group_cols=, max_ratio=)` | n_cells (or extra stat) varies by more than the ratio |
| SC-09 | `scpf_check_empty_cells(x, min_genes=1)` | cells with too few expressed genes |
| SC-10 | `scpf_check_gene_counts(n_genes_by_model, expected_n=, hvg_n=, n_vars_input=, n_hvg_stages=)` | gene counts drift; n_top_genes >= n_vars; HVG applied twice |
| SC-11 | `scpf_check_obs_keys(adata, required, expected_values=, used=)` | key missing, wrong values, or resolved to a different column |
| SC-13 | `scpf_check_nesting(adata, factors, pairs=)` | one factor determines another |
| SC-14 | `scpf_check_block_scoring(layouts, bases)` | block-structured model scored per-dimension, or the reverse |
| SC-15 | `scpf_check_block_pairing(blocks, factors, pairing, shared_blocks=)` | pairing is not a name map, or shared block credited |
| SC-16 | `scpf_check_knn_validity(n_cells, k, kind=)` | trustworthiness k >= n/2, kNN k >= n, kBET k > n |
| SC-18 | `scpf_check_pool_coverage(table, model_col, dataset_col, value_col=)` | models cover different datasets |
| SC-19 | `scpf_check_allowed_values(values, allowed, name=)` | a value (not just a missing field) is outside the allowed set |
| SC-20 | `scpf_scan_fallbacks(text_or_path)` | log shows refit/fallback/swallowed error |

Manual items: SC-12 (batch conditioning vs disentanglement claim), SC-17
(non-computable metric must be NaN + status), SC-21 (continuous factors not
binned), SC-22 (field-standard claims need a single-cell source).
Supporting helpers: `scpf_items`, `scpf_classify_gene`, `scpf_obs`,
`scpf_names`, `scpf_matrix`, `scpf_finish`.

## Reading results

- `problems` block the item; `warnings` do not but belong in the evidence.
- `scpf_check_donor_split` without `split_col` only warns that a grouped split
  is required. Pass the realized split labels to turn that into a measurement.
- Species detection is heuristic for symbols (all-caps mouse symbols such as
  `H2-K1` are classed human). Prefer Ensembl ids or an orthology table, and
  state which was used.
- `scpf_check_cell_counts` defaults to `max_ratio=2.0`; choose the ratio in the
  plan before looking at results, and record it.
- Thresholds are arguments: change them in the plan, not after a failure.

## Typical sequence for a perturbation benchmark

```python
rec = pf_new("07_noise_axis", plan)
r = scpf_check_species(adata_panel, "human");               pf_answer(rec, "SC-05", "pass", scpf_evidence(r))
r = scpf_check_noop_perturbation(adata_raw, adata_i0);      pf_answer(rec, "SC-07", "pass", scpf_evidence(r))
r = scpf_check_cell_counts(counts_tbl, "intensity", group_cols="dataset", max_ratio=2.0, extra_cols=["median_genes"])
pf_answer(rec, "SC-08", "pass", scpf_evidence(r))
r = scpf_check_knn_validity(counts_by_cond, k=15, kind="trustworthiness"); pf_answer(rec, "SC-16", "pass", scpf_evidence(r))
pf_save(rec); print(pf_verdict(rec, extra_items=scpf_items()))
```

A failing helper raises before `pf_answer`; fix the cause, or run with
`strict=False` and answer `fail` with the evidence so the verdict records it.

## Provenance

The checks in this skill come from a review of real failures in AI-assisted research sessions. `tests/` in the repository holds synthetic fixtures that reproduce each failure pattern: known-bad inputs must fail, known-good inputs must pass.

## Related

`references/checklist.md` (rationale, check recipes), `references/helper_api.md`
(arguments, return fields, limits).
Interlocks: `reproducible` (experiments/NN_name/ holds PREFLIGHT.md and
preflight.json), `lab-notebook` (log a failed blocking item as an ISSUE),
`rigor-review` (post-hoc check of outputs; preflight is the pre-hoc side).
