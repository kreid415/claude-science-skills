---
name: fail-loud-pipelines
description: "Fail-loud coding standard and gates for experiment runners, launchers, manifests, Nextflow/SLURM pipelines and metric code. Load BEFORE writing or launching a multi-config / multi-seed / multi-hour run, and whenever you write run_*.py, a launcher, a .done marker, a resume key, a config-to-CLI mapping, an output filename scheme, a log or CSV parser, a verification script or test, or a conda/GPU environment for a job. Also load when something 'finished' but results are NaN, missing, duplicated, overwritten, or produced by the wrong file/column/env, or when a fix 'had no effect'. Provides fl_lint (static scan for silent-failure patterns), fl_args_consumed, fl_unique_outputs, fl_completion_gate, fl_columns, fl_env_guard, fl_mutation_check. Triggers: silent failure, swallowed exception, .done marker, resume skipped, all-NaN embeddings, output overwritten, config key ignored, wrong column, parse_known_args, vacuous test."
---

# Fail-loud pipelines

A pipeline that fails silently is worse than one that crashes: the crash costs minutes, the silent failure costs
the run (plus whatever conclusions were drawn from it). Silent failures in runner/metric/launcher code are a
common and costly failure pattern. The pattern is always the same: **a failure was converted into a value**
(fallback file, NaN, empty row, `.done` marker, exit 0, green test) and nothing downstream could tell.

The standard below makes every failure either (a) raise, or (b) become a recorded failure row **and** a non-zero
exit **and** a blocked completion marker. Helpers (loaded from `kernel.py`) enforce it; each raises a specific
exception carrying `.result` (a dict) and returns the same dict when `strict=False`.

## When to use which helper

| Moment | Call | Raises |
|---|---|---|
| Code written/edited (runner, launcher, `.nf`, `.sh`, `.R`) | `fl_lint(paths)` | `FLLintError` |
| Config/manifest -> CLI args | `fl_args_consumed(config_or_argv, parser)` | `FLArgsError` |
| Before submitting a sweep | `fl_unique_outputs(rows, path_template_or_fn, swept_keys)` | `FLUniqueOutputsError` |
| Before launching hours of GPU/CPU work | `fl_env_guard(require_cuda=True, packages=..., imports=..., env_vars=...)` | `FLEnvError` |
| Reading a column/label you did not create | `fl_columns(df_or_adata, names, values=...)` | `FLColumnsError` |
| End of a shard, BEFORE writing `.done` | `fl_completion_gate(expected, found_dir, results_csv=...)` | `FLCompletionError` |
| Writing any test, CI gate, regex check, smoke test | `fl_mutation_check(check_fn, good, bad_inputs)` | `FLMutationError` |

`fl_rules()` lists every lint rule; `fl_exc("FLLintError")` returns the exception class for `except`.

## Pre-launch checklist (do in this order, paste the outputs into the lab notebook / run log)

1. `fl_lint("src/ experiments/")` - zero blocking findings, or each remaining one carries a reasoned `fl: allow`.
2. `fl_args_consumed(cfg, parser)` for **every runner** each config row will be sent to (use the runner's own
   `build_parser()`; do not re-declare the parser in the launcher).
3. `fl_unique_outputs(rows, template, swept_keys)` over the **full expanded manifest**, with `reserved_paths`
   = outputs of other experiments sharing the directory. Pass the resume/dedup key function as a second call.
4. `fl_env_guard(...)` inside the job (first lines of the batch script), not only on the login node.
5. Smoke-run ONE config end to end, then `fl_completion_gate` on its outputs. Only then fan out.
6. Per shard, last statement: `fl_completion_gate(expected, out_dir, results_csv=..., done_marker=".done")`.

## Coding standard

Each rule says what to do and why.

**R1. A failure is never a fallback value.** No `except Exception:` / bare `except:` unless it re-raises or exits
non-zero. Catch the narrowest type, and only where you can fully recover with *identical semantics*. A fallback
that computes something different (refit on all data, `os.listdir()[0]`, constant metric, zeros) is a wrong
answer that looks like a result. If you must record-and-continue per config (sweeps), write a failure row
(`status`, `error`) and make the process exit non-zero at the end (`sys.exit(1 if failures else 0)`).

**R2. Exit status is the contract.** Scripts: `set -euo pipefail`; never `set +e`, `|| true`, `exit 0` after a
failure branch. Python: check `subprocess.run(..., check=True)`; call `git`/`rsync`/`conda activate` without
`2>/dev/null`. After a tool reports success, verify the *state* (`git status`, file exists, row count) - success
messages are not evidence (`git add` skipping files, `git push` rejected, `conda activate` failing).

**R3. Never pick a file by position.** No `os.listdir()[0]`, `glob()[0]`, `ls | head -1`, `next(iter(...))`. Build the
exact expected name; if you must glob, `assert len(matches) == 1` and **validate structure** (the array/obsm key
you need exists, is finite, has the expected shape). The first match is often an intermediate file.

**R4. Every config key is consumed or rejected.** No `parse_known_args` (use `parse_args`). No `argparse type=bool`
(use `action=store_true` or an explicit `str2bool` that rejects unknown strings; Groovy/Nextflow has the same
trap: the string `"false"` is truthy - compare `== 'true'` or call `.toBoolean()`). When code is edited so a flag is
renamed/removed, `fl_args_consumed` must still pass for every stored config. Regenerating a manifest must not
drop previously required flags: diff old vs new manifest flags before submitting.

**R5. Output paths encode every swept key.** Same for cache keys and resume keys: if a parameter varies in the sweep
it must appear in the filename and in the dedup key. Zero/None/empty levels must still produce a suffix (a skipped
`if idx:` branch overwrote another experiment's files). Run `fl_unique_outputs` on the expanded manifest.

**R6. `.done` means verified complete.** Marker only after `fl_completion_gate` passes: all expected files (per
seed/config, enumerated - no globs), non-empty, finite, no failed rows. A stale marker from an earlier attempt is
a bug source: the gate reports it. Merge/glob steps must assert expected counts (rows == configs x seeds, every
experiment id present), never silently filter (`dropna`, `notna`, flat `glob`).

**R7. Verify the environment you assume.** Conda forks/solves can swap `torch` for a CPU build; a missing optional
dependency silently thins a model roster; an env var read by a child is only set if exported. `fl_env_guard` at job
start. Cap BLAS/OpenMP threads (`OMP_NUM_THREADS` etc.) when running many CPU jobs in parallel, then confirm with
the gate that each job wrote output.

**R8. Durable, big outputs go to scratch or the granted durable path, never `$HOME`, `/tmp` or the ephemeral
workspace.** Set cache dirs (`APPTAINER_CACHEDIR`, `TMPDIR`, `XDG_CACHE_HOME`) in every shell that runs
jobs, not only in the pipeline. Decide up front which artifacts downstream metrics need (embeddings,
latents, per-seed predictions) and persist them *before* a long wave starts. Commit code that generates results
(configs under `experiments/` must not be gitignored) - see `reproducible`.

**R9. Parse by name, never by position.** Use header names (`df["col"]`, not `iloc[:, 2]`), `fl_columns` on every
column/label/value you did not create, exact-case labels, and `pd.to_numeric(errors="raise")` for metric columns.
Parse logs with named capture groups and a positive control (a real line must produce the expected tuple).
Config parsing by regex must skip comments (strip `#`/`//` first). Grouping keys for a "best config" must be the
same in exploration and final code - import one function, do not retype the `groupby` list.

**R10. Metric code is checked at both ends.** Assert inputs finite and in range, outputs finite; no `fastmath=True`;
no silent constant fallback when a denominator collapses (raise instead); after any dropout/subsample/perturbation,
re-filter and assert no empty cells/rows. Missing implementations (e.g. a metric that cannot resolve a block
layout) must raise, not fall back to a different definition.

**R11. A verifier must be shown to fail.** Every test, CI gate, regex check, smoke test and "reproduces exactly"
comparison gets `fl_mutation_check`: pass the real producer's output as `good_input` and at least one known-bad
input; the check must reject it **for the right reason**. Build fixtures from the real producer's convention, not
the checker's. Never chain a conclusion with `&&` into an `echo`. Run a verification from the directory/path
where the artifact lives, and print the thing counted (`n=...`) so a vacuous zero is visible.

**R12. Fixes must be proven live.** A fix that depends on config keys, env vars or flags (retry hardening, worker
counts, thread sizing) is inert if the key is wrong or not propagated. Show the effect (value read inside the
child process, task log, `nextflow config`/`-preview` against the *installed* version) before reporting it fixed;
check keys against the installed tool's schema, not web docs. Nextflow: no `executor` in process bodies (use
profiles), no `errorStrategy 'ignore'`, no `ifEmpty([])` on channels feeding `collect()`.

## Suppression

Inline, same line or the comment-only line directly above (`//` in `.nf`):

    except ImportError:  # fl: allow FL102 optional plotting dep; plots are skipped and logged below

A reason (>=3 chars) is mandatory; a bare `# fl: allow FL102` is itself a high-severity finding (FL000) and does
not suppress. Suppressed findings are returned in `result["suppressed"]` with their reasons for review.

## Usage patterns

```python
res = fl_lint(["src", "experiments", "pipeline/main.nf", "slurm/"])          # raises on medium+ findings
fl_args_consumed(cfg_row, build_parser(), ignore=["shard"])                     # keys only the launcher uses
fl_unique_outputs(rows, "emb/{dataset}_{model}_b{batch_count}_s{seed}.npz",
                  swept_keys=["batch_count", "seed"], reserved_paths=other_exp_paths)
fl_unique_outputs(rows, resume_key_fn, swept_keys=["batch_size", "lr"])         # resume key must separate sweeps
fl_env_guard(require_cuda=True, packages={"torch": "2.4.1"}, imports=ROSTER_MODULES,
             env_vars=["WORKERS"], not_under_home=["APPTAINER_CACHEDIR"], require_thread_caps=True)
fl_columns(adata, ["Infected", "Injected"], values={"model": ["scVI (LDVAE)"]})
expected = [f"emb_s{s}.npy" for s in seeds]
fl_completion_gate(expected, out_dir, results_csv="results.csv", expected_rows=len(seeds), done_marker=out_dir + "/.done")
fl_mutation_check(no_undefined_refs, good_input=clean_log, bad_inputs={"ref": ref_warn_log, "cite": cite_warn_log})
```

Gate specs accept dicts for richer checks: `{"path": "x.h5ad", "obsm": ["X_uce"]}`, `{"path": "z.npz", "keys": ["z"]}`,
`{"path": "m.csv", "min_rows": 10, "finite": True}`. Without `anndata` an `.h5ad` finiteness check is reported as
`unverifiable` (a failure) unless `allow_unchecked=True`.

On a gate/guard failure: do not retry blindly. Log it as an ISSUE entry in the lab notebook (`nb_entry`) with the
exception text, fix the cause, delete the stale marker, rerun the shard.

## What the helpers do not catch (judgement still needed)

- Semantic wrongness that is finite and well-formed (wrong metric definition, wrong grouping); see `rigor-review`.
- Resource oversubscription beyond thread caps; GPU exclusivity logic (test it with a two-job collision).
- Fixes whose effect is never observed - R12 is a procedure, not a lint.
- Lint is heuristic (regex + AST): FL140/FL171 assume `returncode`/non-zero exits are visible in the same file.
  Suppress with a reason when a rule is wrong for that line; do not disable a rule globally.

## Provenance

The checks in this skill come from a review of real failures in AI-assisted research sessions. `tests/` in the repository holds synthetic fixtures that reproduce each failure pattern: known-bad inputs must fail, known-good inputs must pass.
