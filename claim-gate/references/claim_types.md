# Claim types: evidence required and wording

For each claim type: what counts as evidence in this turn, which helper produces it, and wording when the evidence is missing. Evidence is the saved artifact as it is now.

| Claim | Evidence required | Helper | If missing, write |
|---|---|---|---|
| 'Edit applied / figure fixed / file updated' | pre-edit hash differs from post-edit hash AND you opened the result | `cg_sha256` before, `cg_changed` after, view the image/file | 'Edit attempted; not confirmed in the saved file.' |
| 'Saved / linked artifact contains X' | read the saved version (not the working copy, not a git commit) | `cg_readback`, read_file on the version id | 'Not saved as an artifact yet; commit only.' |
| 'Tests pass / suite green / N passed' | the job's result file or pytest output read in this turn; exit code not masked | `cg_readback` on the results file, then `cg_stale` | 'Job submitted; result not read yet.' |
| 'Validated / complete' after a check | the check ran to completion on the semantic property claimed (a syntax check does not validate runtime behaviour) | readback of the check's output | 'Syntax-checked only; runtime check not completed.' |
| 'The value is 0.51' | value read back from the file that holds it | `cg_readback` (+ `cg_render` into text) | 'Not recomputed.' |
| 'N items / N of M' | N from `len()` of the list or table you hold, same data the document is built from | `cg_reconcile`, `cg_reconcile_table` | state the list, not the count |
| 'Totals sum / partitions cover the set' | disjointness and union size computed | `cg_reconcile(partitions=...)` | 'Partition not checked for overlap.' |
| 'A beats B / best / only / top-k / monotonic' | the predicate evaluated over the full table in code (sort, rank, diff) | recompute, then `cg_contradictions(..., fail_on_quantifiers=False)` to list others | 'Appears higher in the rows I printed; not tested on all.' |
| 'All datasets / every model / each seed' | every member of the universe is in the verified set | `cg_scope(..., universe=)` | use `scoped_phrase` |
| 'Range is a-b' | min and max of the same column and rows | `cg_contradictions` (range containment) | quote min and max instead |
| 'Percentage / ratio' | computed from raw values, rounded once at render | `cg_render` with `:.1f`/`:.0%` spec on the raw value | give the numerator and denominator |
| 'Output is current' | outputs newer than code/inputs/remediation commit | `cg_stale` | 'May predate the fix; not regenerated.' |
| 'Reference/DOI/venue/citation count' | a lookup this session returned it (Crossref/OpenAlex/publisher page); quote the identifier returned | paper-lookup / web fetch | 'Unverified.' or omit |
| 'Measured/benchmarked X' | the benchmark ran; log read | run it; `cg_readback` the log | do not state a figure; 'not measured' |
| 'Metric is M' for a metric that may be undefined | status of the computation (feasible parameter regime, no NaN) | `cg_readback` (NaN raises) | 'Undefined: <reason>.' |
| 'Findings addressed / items resolved' | each one has a diff, a file read, or a stated reason; count matches the list | `cg_reconcile(items=...)`, per-item readback | 'N of M addressed; remaining: ...' |
| Repeating an earlier claim | file re-read now; if the user corrected it, treat the correction as the working hypothesis | `cg_readback` | do not repeat it |

## Persisted claims

Commit messages, README text, TODO tables, project memory and reviewer replies are re-read later as facts. Gate them like chat messages. Put the scope in the sentence ('on dataset_A and dataset_B, 3 seeds'). Do not write a result into memory before the readback receipt exists. When a later check overturns a persisted claim, fix the persisted copy (`cg_changed`) in the same turn.

## Wording bank when a check fails or cannot run

- Narrowed: 'Verified on 2 of 6 datasets (dataset_A, dataset_B); the rest not run.'
- Unchecked: 'Written but not read back; treat as unverified.'
- Contradicted: 'My earlier statement was wrong: the table shows 1 of 3 seeds positive.' (state the correction first, then fix the persisted copies)
- Pending: 'Submitted; no result yet.'

## What to do with tolerances and ignores

`cg_contradictions(tol=...)` is a relative tolerance on top of rounding-to-written-precision. Prefer rewriting the prose to a number that is in the table over loosening `tol`. `ignore_numbers` is for numbers that are not table values by design (seed counts, k, years); list them and record the list in the receipt's 'not checked' line.
