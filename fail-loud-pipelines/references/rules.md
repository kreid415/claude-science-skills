# Lint rules and rationale

`fl_lint` severities: high / medium / low; default `fail_on='medium'`.

| rule | severity | what it flags |
|---|---|---|
| FL000 | high | suppression comment is malformed: needs known rule id(s) and a reason of >=3 chars |
| FL001 | high | file could not be parsed, so it was NOT linted |
| FL101 | high | broad/bare except without re-raise or non-zero exit: failure becomes a fallback |
| FL102 | medium | except block only passes/continues: error swallowed |
| FL103 | high | R try/tryCatch error handler returns a value instead of stop() |
| FL110 | high | output/input file picked by position (listdir/glob [0], next(iter())): wrong-file risk |
| FL111 | medium | shell picks a file with ls / head -1 / $(ls ...) |
| FL120 | high | set +e disables fail-fast: later failures are invisible |
| FL121 | medium | `\|\| true` / `\|\| :` swallows a non-zero exit |
| FL122 | low | shell script has no 'set -e' (or -euo pipefail) |
| FL123 | medium | stderr discarded on a command whose failure matters (activate/git/conda/pip/sbatch/rsync/scp) |
| FL130 | high | parse_known_args silently discards unknown/misspelled options |
| FL131 | medium | argparse type=bool: bool('false') is True |
| FL140 | medium | subprocess.run/os.system result never checked (no check=True and no returncode use) |
| FL150 | high | numba fastmath=True can silently change metric results (NaN/inf assumptions) |
| FL160 | high | executor hardcoded in a Nextflow process body overrides profile/config |
| FL161 | medium | Nextflow ifEmpty([]) / ifEmpty(null) can silently skip downstream consumers |
| FL162 | high | Nextflow errorStrategy 'ignore' silently drops failed tasks |
| FL163 | medium | Nextflow boolean-like param used bare: CLI value 'false' is a truthy string |
| FL170 | high | exit 0 / sys.exit(0) in a script that records failures |
| FL171 | high | script records failures in handlers but has no non-zero exit path |
| FL180 | medium | output/cache path under $HOME or ~: home quota / non-scratch storage |
| FL181 | low | output path in /tmp: ephemeral, may be swept before harvest |
| FL230 | high | name used but never imported/defined in this file |
| FL240 | medium | shlex.quote on a string containing $: variable is never expanded |
| FL250 | medium | lenient parsing (errors='coerce'/'ignore', on_bad_lines='skip') turns bad data into NaN/drops rows |

## Rationale and design choices

- **Heuristic, not a proof.** Python rules use `ast`; shell/Nextflow/R rules use line regexes plus brace matching for Nextflow processes. A clean lint means none of these known patterns occur, not that the pipeline is correct.
- **FL101 is the central rule.** A high severity failure pattern is exceptions turned into plausible values (swallowed latent-extraction error; silent refit on full data). A handler passes the rule if it re-raises or exits non-zero; logging alone does not.
- **FL110 flags `glob(...)[0]` directly and via a variable, unless `len(var)` is checked anywhere in the file** (a `len` check or `assert len(x) == 1` counts as asserting uniqueness). It cannot know the assert is on the right path; still validate the file's structure.
- **FL170/FL171** fire only in files that have an `except` handler recording failures (the handler text mentions fail/error/exception/traceback) without re-raising. FL171 further requires the file to be a script (`__name__` guard or `ArgumentParser`) with no non-zero exit path.
- **FL230** is a flow-insensitive check (a name bound anywhere in the file counts as defined), so it only reports names that are never imported/assigned/defined. `host`, `get_ipython`, `display` are treated as injected.
- **FL180/FL181** need both a home/tmp path and an output-ish word on the same line; they will miss paths built across lines. Set cache env vars with `fl_env_guard(not_under_home=...)` for those.
- **FL160** inspects only the directive section of a Nextflow process (before `script:`/`shell:`/`exec:`), so the word `executor` inside a script block is not flagged.
- **Suppression** needs a reason so that reviewers can audit `result['suppressed']`; reasonless allows are FL000 and do not suppress.

## Gate semantics (fl_completion_gate)

- `expected` must enumerate exact files; globs raise `ValueError`, because a glob-defined expectation passes when the run produced nothing.
- Checks per extension: `.npy/.npz` finite + non-empty + required keys; `.h5ad` obsm finite (requires `anndata`; otherwise `unverifiable` = failure unless `allow_unchecked=True`); `.csv/.tsv` has a header, `>= min_rows` rows, NaN columns warned (failure when `finite: True`); `.json` parses and is non-empty; any other type: size only.
- `results_csv` rows are scanned for status/state/outcome values containing fail/error/oom/timeout/killed/cancel/diverg/nan, non-empty error/exception columns, failed=True, success=False, non-zero returncode/exit_code. If none of those columns exist a warning says failed rows cannot be detected - add a `status` column to the runner output.
- With `done_marker`, the marker is written only on success and contains a JSON summary; if the gate fails and a marker already exists, it is reported as stale (never deleted automatically).

## fl_mutation_check semantics

A check *passes* when it returns without raising and does not return `False` / `{'ok': False}`. It *fails* when it raises or returns that. Harness-type errors (NameError, FileNotFoundError, KeyError, ...) on a bad input are not counted as catches - they show the check cannot run, which is exactly the failure mode of a wrong-path verifier. Supply `(input, regex)` to require a specific failure message.
