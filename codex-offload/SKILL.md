---
name: codex-offload
description: "Offload well-specified work (refactors, scripts, tests, bulk edits, long repo edits) from Claude Science to the user's Codex ChatGPT subscription running on the Claude Science host machine (an SSH compute target pointing at that machine, `<HOST_TARGET>`), gated on live Codex 5-hour and weekly usage, with an independent acceptance check, protected tests and model/effort escalation. Policy: use Codex until a window is exhausted, then do the work in Claude and return to Codex after the reset. Apply AUTOMATICALLY, without being asked, to any large self-contained code or text-editing task that meets the criteria below; also when the user says offload/delegate to Codex or wants to save Claude quota."
---

# codex-offload

Policy: use Codex until its quota runs out, then switch to Claude. Correctness comes before speed and quota: a result counts only if an independent check that the model did not write passes.

## Setup
- `<HOST_TARGET>`: an SSH compute target registered in Claude Science that points at the machine running the daemon (sshd on, key-based login to itself). Replace the placeholder with its name.
- Codex CLI installed on that host and logged in with ChatGPT (`codex login`, or `codex login --device-auth` on a headless box). The login stays on the host, outside the sandbox.
- Copy `scripts/codex_usage.py`, `scripts/codex_run.py` and `scripts/codex_loop.py` to `~/codex-offload/` on the host (call_command + base64 works; keep them in one directory).
- `codex_usage.py` starts `codex app-server` and calls the JSON-RPC method `account/rateLimits/read`. Windows are labelled by `windowDurationMins` (300 -> 5h, 10080 -> weekly), never by primary/secondary. Written against Codex CLI 0.159.x; the app-server schema changes between versions, so re-run it after upgrades.
- `codex_run.py` is one gated `codex exec` attempt with provenance. `codex_loop.py` wraps it with the guards and the escalation ladder; use the loop for every offloaded task.

## Automatic use
Offload without being asked when ALL hold: (1) code or text editing work, roughly >10 Claude tool turns or >200 changed lines; (2) the spec fits one task file and you can write an acceptance command for it (see below); (3) no need for host.*, the kernel, MCP connectors or other Claude skills; (4) no credentials/secrets and no hazardous-bio material in the task; (5) the repo is under ~500 MB (the loop works on copies). Otherwise do it in Claude; tiny edits always stay in Claude. State in one line that you are offloading and why. Each submit raises an approval card unless the user has set Always-Allow for the host target.

## Write the acceptance check first
Claude writes it, never Codex. A model grading its own tests can pass erroneously.
1. A command that exits 0 only when the task is done, run with `ACCEPT_WORKDIR=<tree>` set. Put its files OUTSIDE the repo (a separate `acc/` directory) and pass them with `--accept-file` so the loop hashes them before and after.
2. It must FAIL on the untouched repo. The loop runs it on the baseline first and stops with exit 20 (no Codex quota spent) if it already passes. For behavior-preserving refactors use `--accept-before pass`; the guard is then the protected existing tests.
3. Cover cases beyond the examples in the task text: edge values, error paths, unusual inputs. State the rules in the task, but keep the exact cases out of it.
4. If the task is "write tests", the acceptance is still an independent Claude-written check of the implementation, plus `--new-tests-cmd "<command that runs only the new tests>"`.

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

## Escalation ladder
Default `--ladder default,gpt-6-astra:xhigh,gpt-6-astra:max`. Rung 1 is the host default (currently `gpt-6.1-sol` at `xhigh`). When an attempt fails (acceptance fails, a protected file changed, vacuous or unverified tests, exec error), the next rung starts from a fresh baseline copy and its task text includes the failure reasons and the acceptance output tail. Astra is OpenAI's strongest-capability option for multi-step, multi-tool work and costs more quota. Higher effort is not shown to be more accurate; the evidence for any rung is its acceptance result. If a rung's model or effort is not available to the account, the exec fails and the loop moves on. Override with `--ladder default` (single attempt) or a different list; never lower model or effort to save quota. Rung syntax: `default`, `MODEL`, `MODEL:EFFORT`.

| Task | Start rung |
|---|---|
| Most coding: refactors, multi-file edits, scripts | default |
| Hardest work (large cross-module changes, subtle algorithms, wrong result is costly) | `--ladder gpt-6-astra:xhigh,gpt-6-astra:max` |
| Life-science research code where domain knowledge matters | `--ladder gpt-rosalind-5.5:xhigh,gpt-6-astra:xhigh` (Rosalind passed one small smoke task; no quality comparison exists) |

`codex debug models` on the host lists what the account offers. Narrow high-volume models such as `gpt-6-luna` exist but are not used while correctness is the priority.

## Exit codes and what to do
- 0: accepted. Read `final.patch` completely and the `needs_review` list (model-written tests: read them). Apply with `patch -p1` from the repo root (try `--dry-run` first), re-run the acceptance command on the real repo yourself, then report that Codex did the work with the rung used and token totals from the attempt `run.json`.
- 10: gated (a usage window is below the floor). Do the task in Claude; return to Codex after `retry_at` in `route.json` of the attempt.
- 12: Codex hit its usage limit mid-run. Do the task in Claude.
- 20: acceptance did not fail on the baseline. Fix the acceptance (your job), not the model.
- 30: every rung failed. Claude takes over. Read `loop.json` for the reasons; do not reuse or report partial results.
- 2: bad input (missing workdir, repo too large).

## Provenance
`<repo>/.codex-offload/loop_<ts>/` holds `loop.json` (per attempt: rung, reasons, protected violations, new-tests check, acceptance tail, tokens), `a/` (baseline copy), `b1..bN/` (attempt trees, each with the `codex_run.py` record: route, usage before/after, events, last message, `run.json` with model, effort, codex version, token totals), and `final.patch`. Keep `loop.json` and `final.patch` with the commit.

## Known limits
- The guards show that the check you wrote passes and the tests were not tampered with. They do not show the code is correct beyond that check. A weak acceptance check passes weak code; writing a discriminating one is the main correctness lever.
- The Codex sandbox can read files outside the working copy, so keep the acceptance path out of the task text. Hashes detect edits, not reads.
- Usage is account-wide: the Codex desktop app and any other Codex client draw from the same pool. `used_percent_delta` in `run.json` is integer-rounded (0 or 1 for small tasks); use the token totals for cost.
- `--sandbox workspace-write` is the default; use read-only for review-only tasks. Avoid danger-full-access.
