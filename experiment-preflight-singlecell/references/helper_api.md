# scpf_ helper API

All `scpf_check_*` accept `strict=True` (raise) or `strict=False` (return).
Result: `{"ok": bool, "item": str, "check": str, "problems": [str], "warnings": [str], "details": dict}`.
Exceptions: `ValueError` for a failed check or invalid arguments (empty input,
unknown option); `KeyError` for a column not found by name (message lists the
available columns).

## Splits

- `scpf_check_donor_split(adata, label_cols, donor_col, cohort_col=None, sample_col=None, split_col=None, min_donors=3, donor_level_frac=0.5)`.
  Donor-level label = at least `donor_level_frac` of donors carry one value. Donor ids with several values of a
  donor-level label are reported as probable cross-cohort collisions. With `cohort_col`, ids occurring in several
  cohorts are listed (problem if their labels differ). `sample_col` must nest in one donor.
  `split_col` is passed to `scpf_check_split_leakage` with the composite key.
- `scpf_check_split_leakage(adata, group_col, split_col, cohort_col=None)`.
- `scpf_check_split_fn(split_fn, group_ids, n_seeds=10, test_frac=0.2, tol=0.05, check_optimal=False, opt_tol=0.02)`.
  `split_fn(group_ids_array, seed) -> bool array (True = test)`. Details give per-group test rate across seeds and
  the exact best whole-group partition gap (subset-sum, up to 200,000 cells).

## Gene panel

- `scpf_check_species(x, expected="human", max_other_frac=0.01, min_classified_frac=0.5)`; `scpf_classify_gene(name)`.
  Rules: `ENSMUSG` mouse, `ENSG` human, other Ensembl prefixes `other`, `C#orf#` human, lower-case letters mouse-style,
  all upper-case human-style.
- `scpf_check_vocab_coverage(var_names, vocab, min_frac=0.5, model="model")`: exact, case-insensitive and
  version-stripped overlap are all reported.
- `scpf_check_gene_counts(n_genes_by_model, expected_n=None, hvg_n=None, n_vars_input=None, n_hvg_stages=None, tol=0)`.

## Perturbation

- `scpf_check_noop_perturbation(raw, perturbed, atol=0.0)`: shape, obs/var names (if AnnData-like), values.
- `scpf_check_cell_counts(table, axis_col, count_col="n_cells", group_cols=None, max_ratio=2.0, extra_cols=None)`.
- `scpf_check_empty_cells(x, min_genes=1)`.
- `scpf_check_allowed_values(values, allowed, name="value")`.
- `scpf_scan_fallbacks(text_or_path, patterns=None)`: default patterns in `SCPF_FALLBACK_PATTERNS`; accepts text, a path or a list of paths. A clean scan does not prove absence of a fallback; it only finds logged ones. Also compare the result grid with the expected grid.

## Metrics and aggregation

- `scpf_check_obs_keys(adata, required, expected_values=None, level_bounds=None, used=None)`.
- `scpf_check_nesting(adata, factors, pairs=None)`.
- `scpf_check_block_scoring(layouts, bases)`; `scpf_check_block_pairing(blocks, factors, pairing, shared_blocks=None)`.
- `scpf_check_knn_validity(n_cells, k, kind="trustworthiness")`; kinds: `trustworthiness` (k < n/2), `knn` (k < n), `kbet` (k <= n).
- `scpf_check_pool_coverage(table, model_col="model", dataset_col="dataset", value_col=None, allow_unbalanced=False)`:
  details include `common_datasets` and `rank_flip` between naive and common-dataset pooling.

## Limits

Heuristics, not proofs: species from symbols, nesting on observed cells only, log scans on known phrases. A pass
is evidence for the item, recorded with the helper's details; it does not replace reading the primary source for manual items.
