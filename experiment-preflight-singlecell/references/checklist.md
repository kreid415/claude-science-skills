# SC checklist: rationale and recipes

Each item is returned by `scpf_items()` with the shared schema
(id, area, question, severity, applies_if, how_to_check, auto).

| Item | Sev | Auto | Why it exists |
|---|---|---|---|
| SC-01 donor-level labels need grouped splits | blocking | donor_split | Labels such as response, treatment time or cancer type are often constant per patient; a random cell-level split puts the same patient on both sides and inflates probe results. |
| SC-02 composite, verified grouping key | blocking | donor_split | A batch or sample column is not a patient column (several samples per patient), and bare patient ids can collide across cohorts. Build a composite key and check it against every finding already recorded. |
| SC-03 realized split has no straddling group | blocking | split_leakage | Check the saved split labels, not the splitting code. |
| SC-04 split function guarantees tested | major | split_fn | Split helpers can claim guarantees (deterministic side assignment, closest-possible fraction) that fail on counterexamples. Test the guarantee directly; compute subset-sum optima exactly. |
| SC-05 species of every feature | blocking | species | Panels built from combined references can carry a large share of another species' genes; datasets can be configured with the wrong species. |
| SC-06 gene-vocabulary coverage per model | major | vocab_coverage | A human-vocabulary model has zero overlap with mouse Ensembl ids. Report which datasets each model is excluded from, and why. |
| SC-07 zero intensity is an identity | blocking | noop_perturbation | A zero-intensity baseline can differ from the raw input, and a default meant to be a no-op can cut genes per cell sharply and fail QC. Check every dataset and perturbation type, and check default shape parameters. |
| SC-08 sample size along the axis | blocking | cell_counts | Cell counts can fall sharply along a noise axis while kNN metrics compare fits on very different n. Choose max_ratio in the plan. |
| SC-09 no empty cells after perturbation | blocking | empty_cells | min_genes=0 lets dropout produce empty cells; runners then raise, and a retry-then-ignore wrapper leaves a silently missing column. Scan the full grid, not one setting. |
| SC-10 gene counts and HVG | major | gene_counts | Panels can arrive with fewer genes than intended; n_top_genes larger than the input silently falls back; HVG selection re-applied per stage changes the feature set. |
| SC-11 keys by name | blocking | obs_keys | Positional column access (`iloc[:, k]`) or 'first column with few levels' heuristics pick the wrong covariate (e.g. age instead of batch) while the smoke test still passes. |
| SC-12 conditioning vs disentanglement | major | none | scVI/LDVAE demote batch because the decoder is conditioned on the batch key. List each model's conditioning inputs from source before calling a result disentanglement; require an ablation without conditioning. |
| SC-13 nested factors | major | nesting | When one factor determines others (e.g. batch determines response), their 'collisions' are arithmetic and must not be counted as findings. |
| SC-14 native metric basis | blocking | block_scoring | Per-dimension metrics penalise block-structured models for their design; block metrics can silently resolve to per-dimension; continuous-score correlations can rank a perfectly separating block model below null. Group models by their latent structure correctly. |
| SC-15 pairing by name | major | block_pairing | Positional block-to-factor pairing can credit an unowned shared subspace to a factor and flip the sign of the score. |
| SC-16 kNN parameter validity | blocking | knn_validity | Trustworthiness needs k < n/2 and returns NaN silently otherwise; kBET-style tests can require k larger than n and still report a perfect score. |
| SC-17 non-computable = NaN + status | major | none | Infeasible cells must carry NaN and a status string; trace NaN columns to their real cause by reading the code path. |
| SC-18 coverage-balanced pooling | major | pool_coverage | Some models (e.g. human-only foundation models) run on a subset of datasets; a bare groupby-mean over unequal coverage can flip ranks. Pool over common datasets or show coverage, and disclose exclusions. |
| SC-19 allowed values, not field presence | major | allowed_values | A rejection rule that tests for an absent key misses stale files that carry the key; stale rows then land in a baseline slice. |
| SC-20 no silent fallback | blocking | scan_fallbacks | A projection call can fail on a newer runtime, be swallowed, and the model refit on all cells (test-cell leakage). Also confirm every expected cell of the result grid exists. |
| SC-21 continuous factors stay ordered | minor | none | Binning a continuous score into classes can be incoherent with the metric design. |
| SC-22 single-cell source for "standard metric" | minor | none | Disentanglement metrics are sometimes called standard in single-cell benchmarking when the evidence supports only general ML use. |

## Manual item recipes

- SC-12: for each model write the conditioning inputs found in its encoder/decoder source (file:line). If the covariate under claim is among them, the result is an integration effect.
- SC-17: grep result tables for constant 1.0 / 0.0 columns on cells whose parameter regime is infeasible; confirm a status column exists.
- SC-21: record the metric variant per factor type (categorical vs continuous) in the plan.
- SC-22: open the cited benchmark's full text and quote where the metric appears; otherwise phrase as borrowed from general ML.

## Extending the pack

Add an item to `SCPF_ITEMS` in `kernel.py` (next free SC-NN id), add the helper
(`scpf_` prefix, structured result, strict raising), a known-bad and a
known-good test fixture, and a row here.
