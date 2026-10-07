---
name: codex-offload
description: "Offload well-specified work (refactors, scripts, tests, bulk edits, long repo edits) from Claude Science to the user's Codex ChatGPT subscription running on the Claude Science host machine (an SSH compute target pointing at that machine, `<HOST_TARGET>`), gated on live Codex 5-hour and weekly usage, with an independent acceptance check, protected tests and model/effort escalation. Policy: use Codex until a window is exhausted, then do the work in Claude and return to Codex after the reset. Apply AUTOMATICALLY, without being asked, to any large self-contained code or text-editing task that meets the criteria below; also when the user says offload/delegate to Codex or wants to save Claude quota."
---

# codex-offload

Policy: use Codex until it runs out, then switch to Claude. Codex credits are used freely (user decision): there is no gate or stop on the credit balance; it is only recorded. Correctness comes before speed and quota: a result counts only if an independent check that the model did not write passes.

## Setup
- `<HOST_TARGET>`: an SSH compute target registered in Claude Science that points at the machine running the daemon (sshd on, key-based login to itself). Replace the placeholder with its name.
- Codex CLI installed on that host and logged in with ChatGPT (`codex login`, or `codex login --device-auth` on a headless box). The login stays on the host, outside the sandbox.
- Copy the six scripts in `scripts/` (`codex_usage.py`, `codex_run.py`, `codex_loop.py`, `codex_ledger.py`, `codex_canary.py`, `codex_mutate.py`) to `~/codex-offload/` on the host (call_command + base64 works; keep them in one directory) and write the manifest there: `cd ~/codex-offload && sha256sum codex_usage.py codex_run.py codex_loop.py codex_ledger.py codex_canary.py codex_mutate.py > MANIFEST.sha256`. Redo this after every script change; the canary compares against it.
- `codex_usage.py` starts `codex app-server` and calls the JSON-RPC method `account/rateLimits/read`. Windows are labelled by `windowDurationMins` (300 -> 5h, 10080 -> weekly), never by primary/secondary. Written against Codex CLI 0.159.x; the app-server schema changes between versions, so re-run it after upgrades.
- `codex_run.py` is one gated `codex exec` attempt with provenance. `codex_loop.py` wraps it with the guards and the escalation ladder; use the loop for every offloaded task. `codex_ledger.py` logs every loop; `codex_canary.py` checks that the path works.

## Before the first offload in a session
Run `python3 ~/codex-offload/codex_canary.py --auto` on the host (call_command, login shell). It checks that Codex is installed and logged in, the usage read returns windows, and the scripts match the manifest; it also runs one known-answer task through the whole loop when the Codex version changed or the last full pass is over 7 days old. If it exits 1, do NOT offload: tell the user which check failed, plainly, and do the task in Claude. A silent fallback would hide a dead offload path.

## Automatic use
Offload without being asked when ALL hold: (1) code or text editing work, roughly >10 Claude tool turns or >200 changed lines; (2) the spec fits one task file and you can write an acceptance command for it (see below), or it is a tests-for-existing-code task with a `--tests-only` configuration; (3) no need for host.*, the kernel, MCP connectors or other Claude skills; (4) no credentials/secrets and no hazardous-bio material in the task; (5) the repo is under ~500 MB (the loop works on copies). Otherwise do it in Claude; tiny edits always stay in Claude. State in one line that you are offloading and why. Each submit raises an approval card unless the user has set Always-Allow for the host target.

## Write the acceptance check first
Claude writes it, never Codex. A model grading its own tests can pass erroneously.
1. A command that exits 0 only when the task is done, run with `ACCEPT_WORKDIR=<tree>` set. Put its files OUTSIDE the repo (a separate `acc/` directory) and pass them with `--accept-file` so the loop hashes them before and after.
2. It must FAIL on the untouched repo. The loop runs it on the baseline first and stops with exit 20 (no Codex quota spent) if it already passes. For behavior-preserving refactors use `--accept-before pass`; the guard is then the protected existing tests.
3. Cover cases beyond the examples in the task text: edge values, error paths, unusual inputs. State the rules in the task, but keep the exact cases out of it.
4. Write one or two known-bad implementations as `patch -p1` files and pass them with `--bad-variant`; the acceptance must reject each (exit 20 otherwise). Checklist before offloading: deterministic; fails on the baseline; rejects the bad variants; covers edge values, error paths and an unusual input; does not depend on anything the model can edit; does not reuse the task's examples verbatim.
5. If the task is "write tests", the acceptance is still an independent Claude-written check of the implementation, plus `--new-tests-cmd "<command that runs only the new tests>"`.

## Guards (enforced by `codex_loop.py`)
- Every attempt runs in a fresh copy of the pristine baseline. The real repo is never edited; an accepted result is written as `final.patch`.
- Pre-existing test and config files (`tests/*`, `test_*.py`, `*_test.py`, `conftest.py`, `*.test.*`, `*.spec.*`, `pytest.ini`, `tox.ini`, `.github/*`, plus any `--protect GLOB`) must be byte-identical afterwards. Edits, even ones that make the check pass, fail the attempt.
- New test files written by the model make the attempt unacceptable unless `--new-tests-cmd` is given. With it, the tests must FAIL on the baseline (with the new test files added) and PASS on the attempt. Tests that pass on the baseline are vacuous and rejected.
- If the baseline failure of new tests is an ImportError, SyntaxError or collection error, the result carries `suspicious_red` and `needs_review`: read `new_tests_check.baseline_tail`, because an import error is a red for the wrong reason.
- `--accept-file` hashes detect an attempt that rewrites the acceptance script.

## Run (repl tool; host.compute is not in `python`)
```python
c = host.compute.create('<HOST_TARGET>')
job = c.submit_job(
  intent='Codex offload (guarded loop): <task, repo, size>',
  command='''mkdir -p acc && cp accept.py acc/
python3 ~/codex-offload/codex_loop.py ./task.md <ABS_REPO> --accept "python3 $PWD/acc/accept.py" --accept-file $PWD/acc/accept.py --timeout 3000
rc=$?
L=$(ls -dt <ABS_REPO>/.codex-offload/loop_* | head -1); mkdir -p out; cp $L/loop.json $L/final.patch out/ 2>/dev/null
exit $rc''',
  inputs=[{'src': 'task.md', 'dst': 'task.md'}, {'src': 'accept.py', 'dst': 'accept.py'}], outputs=['out/**'],
  run_timeout_s=3 * 3000 + 900)   # up to three attempts
```
Add `--new-tests-cmd`, `--protect`, `--accept-before pass` or `--ladder` as needed. End the turn and park on the compute notification.

## Tests for existing code (`--tests-only`)
For "write tests for this module" there is no failing baseline, so the acceptance check cannot be red first. The loop uses different guards:
```
python3 ~/codex-offload/codex_loop.py task.md <ABS_REPO> --tests-only --tests-cmd "python3 tests/test_x.py" \
  --mutation-target pkg/mod.py [--mutation-target ...] [--bug-patch $PWD/acc/bug1.patch ...] [--mutation-min 0.75]
```
- `--tests-cmd` runs the whole suite including the new tests (exit 0 = pass); `--mutation-target` names the Python source files the tests are for.
- Every pre-existing file must stay unchanged (tests only), at least one new test file must appear, and the tests must pass on the current code.
- Each `--bug-patch` (a `patch -p1` file you write: a realistic bug in the code under test) must make the new tests FAIL. Write two or three; they are the part the model cannot see or tune to.
- `codex_mutate.py` (stdlib, Python only) changes the target code one step at a time (flipped comparisons, swapped operators, changed constants, negated conditions, return value to None) and counts how many the tests catch. The score must reach `--mutation-min` (default 0.75) on two different random samples. Survivors from the first sample go into the next attempt's feedback; the second sample is fresh, so listing them does not let the model tune to them.
- Survivors can be equivalent mutants (changes that do not alter behavior, such as counts that all shift by the same amount). The accepted result lists them in `needs_review`: read them, and lower `--mutation-min` only after judging that the survivors are equivalent. Other languages need a different mutation tool; none is built.
- A thorough-looking suite proves nothing by itself; this is real evidence of strength: it failed on every known bug and on 44 of 46 changed versions of the code. Still read the tests: check they assert exact values and are not brittle (for example, control-character inputs or checks on exact error messages).

## Escalation ladder
Default `--ladder default,gpt-6-astra:xhigh,gpt-6-astra:max`. Rung 1 is the host default (currently `gpt-6.1-sol` at `xhigh`). When an attempt fails (acceptance fails, a protected file changed, vacuous or unverified tests, exec error), the next rung starts from a fresh baseline copy and its task text includes the failure reasons and the acceptance output tail. Astra is OpenAI's strongest-capability option for multi-step, multi-tool work and costs more quota. Higher effort is not shown to be more accurate; the evidence for any rung is its acceptance result. If a rung's model or effort is not available to the account, the exec fails and the loop moves on. Override with `--ladder default` (single attempt) or a different list; never lower model or effort to save quota. Rung syntax: `default`, `MODEL`, `MODEL:EFFORT`.

| Task | Start rung |
|---|---|
| Most coding: refactors, multi-file edits, scripts | default |
| Hardest work (large cross-module changes, subtle algorithms, wrong result is costly) | `--ladder gpt-6-astra:xhigh,gpt-6-astra:max` |
| Life-science research code where domain knowledge matters | `--ladder gpt-rosalind-5.5:xhigh,gpt-6-astra:xhigh` (Rosalind passed one small smoke task; no quality comparison exists) |

`codex debug models` on the host lists what the account offers. Narrow high-volume models such as `gpt-6-luna` exist but are not used while correctness is the priority.

## Exit codes and what to do
- 0: accepted. Read `final.patch` completely and the `needs_review` list (model-written tests: read them). Apply with `patch -p1` from the repo root (try `--dry-run` first), re-run the acceptance command on the real repo yourself, then record the outcome with `python3 ~/codex-offload/codex_ledger.py mark <id> --clean` (or `--defect --note ...` as soon as a defect is found, even weeks later), and report that Codex did the work with the rung used, token totals and any credit use from the attempt `run.json`.
- 10: gated: a window is below the floor (2% for 5h, 1% for weekly) or ordinary usage is off, AND the account reports no credits. Do the task in Claude; return to Codex after `retry_at` in `route.json` of the attempt. With credits available the run proceeds on credits.
- 12: Codex itself refused (usage limit) mid-run. Do the task in Claude.
- 20: acceptance did not fail on the baseline. Fix the acceptance (your job), not the model.
- 30: every rung failed. Claude takes over. Read `loop.json` for the reasons; do not reuse or report partial results.
- 2: bad input (missing workdir, repo too large, a `--bad-variant` patch that does not apply).
- 40: another loop is running (one at a time; the usage pool and host are shared). Wait for it or do the task in Claude.

## Report remaining usage after every offload
Whenever Codex was used (accepted, failed, gated or all rungs failed), finish the message to the user with the remaining 5-hour, weekly and credit amounts. `codex_loop.py` reads them after the loop and stores the line in `loop.json` as `final.usage_report.report_line`, for example `Codex remaining: 5h 94% (resets in 3h 8m), weekly 87% (resets in 5d 0h), credits 4600.15`; copy it verbatim. If the read failed the line says `USAGE READ FAILED`; say that plainly instead of omitting it. `python3 ~/codex-offload/codex_usage.py --line` prints the same line on demand.

## Ledger
Every loop appends one entry to `~/codex-offload/ledger.jsonl` (status, rung, attempts, tokens, credit delta, windows before and after). `codex_ledger.py summary` gives pass rates by rung, token and credit totals and the escaped-defect rate (accepted tasks later marked `--defect`). Use it to decide whether Astra or higher effort pays off and whether the acceptance checks catch errors; do not change routing without it.

## Provenance
`<repo>/.codex-offload/loop_<ts>/` holds `loop.json` (per attempt: rung, reasons, protected violations, new-tests check, acceptance tail, tokens), `a/` (baseline copy), `b1..bN/` (attempt trees, each with the `codex_run.py` record: route, usage before/after, events, last message, `run.json` with model, effort, codex version, token totals), and `final.patch`. Keep `loop.json` and `final.patch` with the commit.

## Known limits
- The guards show that the check you wrote passes and the tests were not tampered with. They do not show the code is correct beyond that check. A weak acceptance check passes weak code; writing a discriminating one is the main correctness lever.
- The Codex sandbox can read files outside the working copy, so keep the acceptance path out of the task text. Hashes detect edits, not reads.
- Usage is account-wide: the Codex desktop app and any other Codex client draw from the same pool. `used_percent_delta` in `run.json` is integer-rounded (0 or 1 for small tasks); use the token totals for cost.
- `--sandbox workspace-write` is the default; use read-only for review-only tasks. Avoid danger-full-access.
