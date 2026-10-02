---
name: claim-gate
description: "Inline gate to run BEFORE sending any message that says done, fixed, verified, complete, passing, saved, updated or 'all/every X', reports a number or count, summarizes results or a table, writes numbers into a paper/README/commit message/memory note, or repeats an earlier claim after the user pushed back. Use it whenever you are about to tell the user what a run, test, edit, figure or table shows. Reads values back from the saved file, scopes the claim to what was actually checked, reconciles counts, checks staleness, renders numbers from a results dict instead of typing them, and appends a claim receipt. Triggers: 'is it done', 'did the edit land', 'summarize the results', 'update the paper numbers', 'write the commit message', 'verify', 'are you sure', 'that number looks wrong'. Counterpart to reproducible (post-hoc audit)."
---

# claim-gate

Every message you send is read as a statement about files on disk. A common failure is that the statement was written from memory of what a tool call was *supposed* to do, not from the output it produced: edits reported as landed that were byte-identical, test counts quoted before the job returned, 'top-3 on every dataset' above a table showing rank 9, totals that did not sum. This skill is the check you run **before** the message, in the same turn. `reproducible` audits after the fact; `claim-gate` stops the claim from being made.

Load it, then follow the protocol. The helpers (`cg_*`) are already defined in the kernel after load. Each raises a specific `ClaimGateError` subclass (an `AssertionError`) with an actionable message when strict (default), and returns a dict with `ok`, `summary`, `sources`.

## When it applies

Run the gate if the message you are about to send (chat, file, commit message, memory note, TODO, reviewer reply) does any of:

- says done / fixed / complete / verified / passes / saved / updated / applied / matches;
- states a number, count, percentage, range, rank, or a comparison (better, best, only, tops, monotonic);
- says 'all', 'every', 'each', 'no', 'always' about a set of datasets, models, files, seeds, findings;
- summarizes a table, log, test run, job, figure or file you did not just read;
- repeats a claim you or the user made earlier (it may be stale or already corrected);
- writes any of the above into a durable place (paper, README, commit, memory, TODO). Persisted wrong claims were re-read as fact in later sessions.

If none apply (pure planning, asking a question), skip it.

## Protocol (in order, every time)

1. **List the atomic claims.** One line each: '8 annotation edits landed in Figure 1', 'MOFA+ is positive on 1 of 3 seeds', 'suite 100/0'. Vague summaries hide the claim that is wrong.
2. **Read back from the artifact, in this turn.** The evidence is the saved file or job output as it is now, not the variable you computed, the plan you wrote, or memory of an earlier read. A tool call finishing (render, save, copy, `|| true` job, commit) is not evidence the content is right. `cg_readback(path, selector)` returns the value with path + sha256.
3. **Scope the claim to what was checked.** If you verified one dataset, say one dataset. `cg_scope(claimed, verified, universe=...)` fails on overreach and returns the phrasing to use.
4. **Reconcile counts.** Stated total = sum of parts; partitions are disjoint; rows = expected configs; the number in prose = `len()` of the list you just built. `cg_reconcile`, `cg_reconcile_table`.
5. **Check staleness.** Outputs must be newer than the code/inputs that make them and than the last remediation commit. Before repeating or defending an earlier claim (yours, the user's, or from memory), re-read the file; the user's correction beats your recollection.
6. **Numbers enter documents only from a results dict.** Compute or read back into variables, render with `cg_render`, format only at render time from raw values (never recompute from rounded display values). No retyping a number you saw in tool output.
7. **External facts need a fetched source.** A DOI, venue, citation count, benchmark timing, GPU utilization, or 'measured' figure is verified only if a tool in this session returned it. Otherwise write 'unverified' or leave it out. Never invent a plausible measurement or a source to back an earlier guess. A non-computable metric is reported as undefined with the reason, not a number.
8. **Summaries are checked against their table.** `cg_contradictions(text, table_df)` before sending any prose about results. Recompute every 'all/every/only/top-k/monotonic' sentence with an explicit predicate over the table; do not trust the impression.
9. **Append the claim receipt** (below). If a check could not be run, say 'not verified: <why>' in the message instead of the claim. Do not rephrase an unchecked claim into softer words that still imply it was checked.

If a helper raises: fix the artifact, or narrow/remove the claim. Never catch the error and send anyway; never relax a tolerance or add `ignore_numbers` just to get green without saying so in the receipt.

## Claim receipt

Append to every message that reports a result or completion. Build it with `cg_receipt(claim, evidence_list, not_checked=[...])`, which refuses failed or hand-typed evidence. Format:

```
CLAIM RECEIPT
claim: <the exact claim as worded in the message>
- readback: <file> {"column": ..., "where": ...} = <value> | <abs path> sha256:<12> @<mtime UTC>
- changed: <file> differs from pre-edit version | <abs path> sha256:<12> @<mtime UTC>
- reconcile: counts agree: {...} | in-memory
not checked: <what the claim does NOT cover, or 'nothing outside the claim'>
```

Keep it to the lines that carry evidence (one per check). For chat replies the receipt can be 2-4 lines; for committed documents put it in the commit body or a `*.receipt.txt` next to the file.

## Helpers

| Helper | Use | Fails when |
|---|---|---|
| `cg_sha256(path)` | hash BEFORE editing a file | not a file |
| `cg_readback(path, selector, allow_nan=False)` | value(s) from csv/tsv/parquet/json/npz/npy; selector keys `column, where, row, expect_rows, key, index` | file missing/empty, unknown column/key, 0 rows, wrong row count, NaN |
| `cg_changed(path, before_sha256)` | after an edit/save/copy | file byte-identical to before |
| `cg_reconcile(total=, parts=, partitions=, items=, **named_counts)` | totals vs parts, disjoint partitions, prose count vs list | any stated quantity differs, partitions overlap, duplicate items |
| `cg_reconcile_table(df, expected_n, by, expected_keys, value_col, balanced)` | table rows vs expected configs | missing/duplicate/unexpected configs, NaN metric, unbalanced groups |
| `cg_scope(claimed, verified, universe=None, noun=)` | 'all' vs what was run | claim covers unverified items; message holds the scoped wording |
| `cg_render(template, values, allow_unused=False, strict_literals=False)` | `{{key}}` / `{{key:.3f}}` into docs; readback results as leaves keep provenance | unresolved/unused placeholder, None/NaN/non-scalar, float without format spec, (strict_literals) typed numbers |
| `cg_stale(outputs, inputs_or_code, since_commit, repo)` | outputs vs code/inputs/commit | an output predates a dependency or commit; dirs use oldest/newest file |
| `cg_contradictions(text, table_df, tol, ignore_numbers, fail_on_quantifiers)` | prose vs table | a prose number matches nothing, matches only elsewhere than the named row/column, matches only in absolute value, or a range excludes a table value |
| `cg_receipt(claim, evidence, not_checked)` | build the receipt | no evidence, failed evidence, hand-typed evidence |

Support: `cg_error(kind)` (exception class), `cg_fail`, `cg_has_nan`, `cg_source`, `cg_native`.

## Recipes

Edit then claim 'fixed':
```python
before = cg_sha256(fig_path)          # BEFORE the edit
# ... edit / render / save ...
ev = [cg_changed(fig_path, before)]   # then LOOK at the file (host.view_image) before saying edits landed
```
Visual claims ('legend no longer overlaps') have no helper: open the saved image and name what you saw in the receipt; a render call completing is not a view.

Results table to prose:
```python
tab = cg_readback('results/summary.csv', {'where': {'dataset': 'dataset_A'}})   # records
text = cg_render(template, {'kang': {'ari': ari_kang, 'n': n_models}})['text']   # template holds only {{kang.ari:.2f}}
cg_contradictions(text, pd.read_csv('results/summary.csv'))                  # numbers in text must be in the table
```

Scoped completion:
```python
s = cg_scope('all', verified_models, universe=all_models, noun='models', strict=False)
msg = s['scoped_phrase']      # use this wording if s['ok'] is False
```

Async/remote job: no claim until the result file is read. Read pass/fail counts from the job's results file (`cg_readback`), check the job exit code is not masked by `|| true`, then `cg_stale(results, [code], since_commit=...)`.

Commit message / memory note / TODO: same gate. Pull numbers from `cg_render` output or readbacks; state scope ('on dataset_A, 3 seeds'); reconcile any list-of-N.

## Known limits (state them, do not paper over them)

- `cg_contradictions` matches numbers to precision as written; small integers can match by coincidence (`matched[*].source` shows what they hit, review it). 'k of n' counts and 'all/every/only/top-k' sentences are returned in `count_claims` / `quantifier_claims` for you to recompute; the helper cannot evaluate the predicate.
- `cg_stale` compares file mtimes; copies that reset mtime defeat it. Prefer comparing against a commit (`since_commit`) or hashes when files were moved.
- `cg_render` flags typed numbers only as warnings unless `strict_literals=True`. Use `strict_literals=True` for results paragraphs; allow figure/table numbers via `allow_literals`.
- No helper judges whether a *method* is valid (that is `rigor-review`), only whether the message matches the output.

## Provenance

The checks in this skill come from a review of real failures in AI-assisted research sessions. `tests/` in the repository holds synthetic fixtures that reproduce each failure pattern: known-bad inputs must fail, known-good inputs must pass.
