---
name: cli-subagents
description: "Use the user's Codex CLI (ChatGPT login) and Claude Code CLI (school account) on the Claude Science host machine as stand-in sub-agents, via an SSH compute target pointing at that machine (`cs-local`). Two modes per engine: (1) general task mode (any well-specified task, closing JSON declaration, resume for follow-ups) and (2) the guarded code loop (independent acceptance check, protected tests, escalation ladder). Also: specialist agents (`--agent`), bulk reading of large files and folders (read-only recipe), scheduled headless jobs with a results inbox. Replaces codex-offload. Apply AUTOMATICALLY to large self-contained code or text-editing work that meets the criteria below, when the user says offload/delegate to Codex or to the school Claude, or when Claude Science cannot run its own sub-agents (e.g. the school account has no host.delegate)."
---

# cli-subagents

Two command-line agents run on the user's machine as stand-in sub-agents: engine `codex` (`codex exec`, ChatGPT login) and engine `claude` (`claude -p`, Johns Hopkins enterprise account, user config in `~/.claude`). Both run OUTSIDE the Claude Science network sandbox. Correctness comes before speed and quota: a loop result counts only if an independent check that the model did not write passes; a task result counts only after Claude Science has checked it itself.

Policy for codex (kept from codex-offload): use Codex until it runs out, then switch to Claude; credits are used freely and only recorded. Claude engine: no gate (see Usage).

## Setup
- `cs-local`: SSH compute target registered in Claude Science pointing at the machine running the daemon. Jobs run in a login shell (non-login shells lack `~/.local/bin`; the scripts also fall back to `~/.local/bin/<cli>`).
- Codex CLI logged in (`codex login`). Claude Code CLI logged in (`claude auth login`, done by the user in a terminal; never log in on the user's behalf and never read tokens).
- Scripts live in `~/cli-subagents/` on the host (flat) with `MANIFEST.sha256`; the skill's `scripts/` folder is the source. Deploy: copy `scripts/*.py` there (submit_job `inputs` are flat filenames), then `cd ~/cli-subagents && sha256sum codex_usage.py agent_run.py agent_loop.py agent_task.py agent_ledger.py agent_canary.py agent_mutate.py agent_schedule.py agent_inbox.py > MANIFEST.sha256`; copy `jobs/` too. Redo after every script change; the canary compares against it. Offline tests: `python3 tests/test_cli_subagents.py` (claude replaced by a stub; the codex path is covered by `agent_canary.py --engine codex --full`).
- `codex_usage.py` calls `codex app-server` JSON-RPC `account/rateLimits/read`; windows are labelled by `windowDurationMins` (300 -> 5h, 10080 -> weekly). Written against codex-cli 0.159.x; re-run after upgrades.
- Scripts: `agent_run.py` (one gated attempt, both engines), `agent_task.py` (general mode), `agent_loop.py` (guarded loop; uses `agent_run.py`), `agent_ledger.py`, `agent_canary.py`, `agent_mutate.py` (mutation testing for `--tests-only`), `agent_schedule.py` + `agent_inbox.py` (scheduled jobs, see below), `codex_usage.py`. The old `codex_*` names are gone; `~/codex-offload/` is the previous deployment.

## Before the first use of an engine in a session
`python3 ~/cli-subagents/agent_canary.py --engine codex --auto` (or `--engine claude`). It checks CLI present, login, usage read (codex), and scripts vs manifest; with `--auto` it also runs one known-answer task through the whole loop when the CLI version changed or the last full pass is over 7 days old (`--full` forces it). Exit 1 = do NOT use that engine: tell the user which check failed, plainly (for claude logged out: the user must run `claude auth login`), and do the task in Claude Science. A silent fallback hides a dead path.

## Which engine, which mode, or a normal sub-agent
| Situation | Use |
|---|---|
| Needs `host.*`, the kernel, artifacts, Claude Science MCP connectors, other Claude Science skills, or mid-run steering | normal Claude Science sub-agent (CLI agents have none of these; they can only be resumed after they finish) |
| Code or text edits with a checkable outcome (>10 Claude tool turns or >200 changed lines) | loop mode, either engine |
| Read-only review, analysis of files in a repo, drafting, or any task without a pass/fail check | task mode, `--access read-only` where possible |
| Codex included usage left | codex first (policy); Claude-school engine when codex is gated/out, when a second independent opinion is wanted, or when the user names it |
| Claude Science cannot spawn its own sub-agents (school account) | claude engine in task mode is the sub-agent substitute |
The claude engine loads the user's migrated `~/.claude` setup (skills, agents, CLAUDE.md, settings); the codex engine loads `~/.codex/config.toml` (host default model `gpt-6.1-sol` at `xhigh`). Neither sees this conversation: the task file must be self-contained.

## Automatic use
Offload without being asked when ALL hold: (1) code/text editing, roughly >10 Claude tool turns or >200 changed lines, or a task from the table above; (2) the spec fits one task file and, for edits, you can write an acceptance command (loop) or the result can be checked by reading (task); (3) no need for host.*, kernel, MCP or other Claude Science skills; (4) no credentials/secrets and no hazardous-bio material in the task; (5) repo under ~500 MB (the loop works on copies). Otherwise do it in Claude; tiny edits stay in Claude. State in one line that you are offloading, to which engine, and why. Each submit raises an approval card unless Always-Allow is set for the host target.

## Safety rules (enforced where noted)
1. Never send a task to either engine that a content-safety filter blocked in Claude Science or that a model refused; stop and tell the user instead. Never send credentials, tokens, or hazardous-bio material in the task text or the workdir.
2. Both CLIs run outside the sandbox with the user's file permissions. Default workdir handling (enforced by `agent_task.py`): a clean git repo is used in place; a plain directory or dirty repo is copied to `<out>/work`, baselined with a git commit, so the original is untouched and the diff is recorded (`--in-place` skips this; `--resume` implies it). The loop always works on copies.
3. Dangerous modes are not reachable: `--access` is `read-only` or `workspace-write` only (codex `-s`; never danger-full-access or bypass flags); claude runs with `--permission-mode dontAsk|acceptEdits|plan` and an explicit `--allowedTools` list (never `--dangerously-skip-permissions`, `bypassPermissions`, `auto`). Claude default allow-list for workspace-write: Read, Glob, Grep, Edit, Write plus `Bash(python3 *)`, `Bash(python *)`, `Bash(pytest *)`, `Bash(git *)`, `Bash(ls *)`, `Bash(cat *)`, `Bash(mkdir *)`, `Bash(diff *)`; read-only: Read, Glob, Grep. Limit: an allowed `python3 *` can run arbitrary code, so this is a permission list, not an OS sandbox; keep sensitive paths out of reach and use scratch copies.
4. Claude Science must check every result itself before reporting it: read `declaration.deviations`, read the changed files and `diff.patch`, rerun the checks yourself. Report which engine and settings did the work.

## General task mode (`agent_task.py`)
```
python3 ~/cli-subagents/agent_task.py TASK.md --engine codex|claude --workdir DIR [--access read-only|workspace-write] [--model M] [--effort E]
   [--schema SCHEMA.json] [--resume SESSION_ID] [--allowed-tools CSV] [--permission-mode dontAsk|acceptEdits|plan] [--out DIR] [--timeout 3600] [--in-place]
   [--agent NAME] [--add-dir PATH ...]
```
- `--agent NAME` (claude only; codex exits 2): runs `claude --agent NAME`, i.e. a specialist profile from `~/.claude/agents/`. Recorded as `agent` in `result.json` and the ledger. The `--allowedTools` list still applies on top of the agent's own tools.
- `--add-dir PATH` (repeatable, must exist): extra folders the agent may read. claude: `--add-dir`. codex: its `--add-dir` makes the folder WRITABLE, so it is passed only with `--access workspace-write` (not on `--resume`); a read-only codex sandbox already reads every path, so nothing is passed.
- Effort: codex `low|medium|high|xhigh|max|ultra` (`-c model_reasoning_effort=`); claude `low|medium|high|xhigh|max` (`--effort`). Models: codex `-m` (list with `codex debug models`); claude aliases `opus`, `sonnet`, `fable` or a full name. Omitted = the CLI's own default.
- Every task gets a closing declaration appended: a JSON object with `result`, `deviations` (what was done less than or other than asked; `["none"]` = full coverage) and `files_changed`. Missing or invalid = status `UNDECLARED` (exit 13): do not use the result. With `--schema` (a JSON schema for `result`), the CLI's native option is used (codex `--output-schema`, claude `--json-schema`) on a wrapper schema `{result, deviations, files_changed}` and the result is validated again with `jsonschema` when installed. Codex requires OpenAI-strict schemas (`additionalProperties: false`, all properties required).
- Follow-up: `--resume SESSION_ID` (from `result.json`) with the SAME `workdir_used`; claude `-p --resume`, codex `exec resume`. Sandbox/permissions are re-applied on resume. There is no mid-run steering.
- Output dir `<out>` (default `~/cli-subagents/runs/<ts>_<engine>_<pid>/`): `result.json` (engine, CLI version, model, effort, session_id, status, exit code, engine exit code, final message, parsed declaration, tokens, cost reported by claude, permission denials, usage before/after (codex) or rate-limit events (claude), usage report line, wall seconds, git head/diff stat/untracked, workdir used), `events.jsonl` (raw), `last_message.md`, `task_sent.md`, `diff.patch`, `run.json`.
- Statuses and exit codes: 0 DECLARED | 10 GATED (codex usage below floor and no credits; do it elsewhere, retry after `route.retry_at`) | 11 EXEC_FAILED | 12 USAGE_LIMIT (the CLI itself refused mid-run; do it elsewhere) | 13 UNDECLARED | 2 bad input.

## Bulk reading of large inputs (recipe, no extra script)
Rule: Claude Science does not read large logs, result tables, folders of outputs or PDFs that sit on disk. It sends them to this recipe and reads only the summary. (PDFs: ask for the text to be extracted and summarised; page images cost about 4K tokens each.)
1. Write a self-contained task file: the absolute paths to read, and what to extract. Ask for exact numbers (copied as shown, never rounded or recomputed), the file path (and line number for log lines) beside every fact, and no conclusions beyond what the files show. Ask that the closing declaration's `result` be the summary as one markdown string, and that it end with a list "Not read" naming every file not opened or opened only in part, with the reason (`none` only if everything was read in full).
2. Run read-only: claude `--access read-only` (tools Read, Glob, Grep only); codex `--access read-only` (sandbox read-only). With `--add-dir PATH` for each folder outside the workdir (claude). Use `--workdir` of a small empty folder with `--in-place` so nothing is copied.
```
python3 ~/cli-subagents/agent_task.py TASK.md --engine claude --workdir ~/scratch/readwork --in-place --access read-only --add-dir /path/to/folder --effort high
```
3. Read only `declaration.result` in `result.json` (and `declaration.deviations`). The agent has no shell, so it cannot list modification times or run code: give it the file list (compute it yourself or let a scheduled job do it, see `watch_dirs`). It cannot write a summary file either; the summary travels in `result`. Spot-check a few numbers and paths against the source before relying on the summary, and report the "Not read" list. Remember it is the agent's own claim.

## Scheduled jobs and results inbox (`agent_schedule.py`, `agent_inbox.py`)
Headless, recurring `agent_task.py` runs via cron (the host's cron daemon must be running; `systemctl is-active cron`).
```
python3 ~/cli-subagents/agent_schedule.py add NAME --cron "30 2 * * *" --spec ~/cli-subagents/jobs/NAME.json   # one crontab line tagged  # cli-subagents:NAME ; re-adding replaces it
python3 ~/cli-subagents/agent_schedule.py list | remove NAME | run NAME --spec FILE   # run = what cron calls
python3 ~/cli-subagents/agent_inbox.py list [--unread] | mark-read [ID ... | --all]
```
- Only lines tagged `# cli-subagents:NAME` are ever added, listed or removed; other crontab entries are untouched. Adding or removing a job is changing the user's crontab: do it only when the user (or their standing instruction) says to, and show the line.
- Spec (JSON): `engine`, `task` (file), `workdir` (required); `agent`, `access` (default read-only), `allowed_tools`, `permission_mode`, `add_dirs`, `model`, `effort`, `timeout`, `schema`, `in_place`, `watch_dirs`. Task-file tokens: `{since}` (time of the last DECLARED/NO_CHANGES run, else `never`), `{now}`, `{file:PATH}`, `{changed_files_path}`, `{changed_count}`. With `watch_dirs`, `run` lists the files modified since the last good run into `changed_files.txt`; an empty list records NO_CHANGES and does not call the agent (no usage spent).
- A run still going makes the next trigger exit with status SKIPPED_LOCKED (logged). Each run writes `~/agent-inbox/NAME/<UTC timestamp>/` (`result.json`, `summary.md` = declaration result plus deviations, `task.md`, `changed_files.txt`, ...) and one line in `~/agent-inbox/index.jsonl`: `id, name, time, status, exit_code, result, summary, usage, read`. Statuses are agent_task's (DECLARED, UNDECLARED, EXEC_FAILED, USAGE_LIMIT, GATED), plus NO_CHANGES, SKIPPED_LOCKED, FAILED.
- Starter specs in `jobs/` (not enabled by default): `results-digest` (nightly 02:30; bulk reading of the changed files under the two benchmarking results folders) and `literature-watch` (Mondays 07:00; `--agent LIT_REVIEWER`, topics one per line from `~/agent-inbox/literature-topics.txt`, read-only plus WebSearch/WebFetch). Crontab lines use `--cron "30 2 * * *"` and `--cron "0 7 * * 1"`.
- Inbox rule: at the start of a Claude Science session, and whenever the user asks what ran, run `agent_inbox.py list --unread`, read each entry's `summary` (or, if there is none, `result.json` status and `error.txt`), tell the user what was found including any not-DECLARED status, then `mark-read`. A digest is the agent's own claim: spot-check what you act on.
- Scheduled runs use the account's usage with nobody watching; the usage line is stored in each index entry. Cron runs outside the sandbox with the user's file permissions: keep specs read-only unless there is a reason, and never put secrets in task files.

## Guarded code loop (`agent_loop.py`)
Write the acceptance check first. Claude Science writes it, never the engine: a model grading its own tests can pass erroneously.
1. A command that exits 0 only when the task is done, run with `ACCEPT_WORKDIR=<tree>`. Keep its files OUTSIDE the repo (a separate `acc/` directory) and pass them with `--accept-file` so the loop hashes them before and after.
2. It must FAIL on the untouched repo. The loop runs it on the baseline first and stops with exit 20 (no quota spent) if it already passes. For behavior-preserving refactors use `--accept-before pass`; the guard is then the protected existing tests.
3. Cover cases beyond the examples in the task text: edge values, error paths, unusual inputs. State the rules in the task, keep the exact cases out of it.
4. Write one or two known-bad implementations as `patch -p1` files and pass them with `--bad-variant`; the acceptance must reject each (exit 20 otherwise). Checklist: deterministic; fails on the baseline; rejects the bad variants; covers edge values, error paths and an unusual input; does not depend on anything the model can edit; does not reuse the task's examples verbatim.
5. If the task is "write tests", the acceptance is still an independent check of the implementation, plus `--new-tests-cmd "<command that runs only the new tests>"`.

Guards (code, not model): every attempt runs in a fresh copy of the pristine baseline; the real repo is never edited; an accepted result is written as `final.patch`. Pre-existing test and config files (`tests/*`, `test_*.py`, `*_test.py`, `conftest.py`, `*.test.*`, `*.spec.*`, `pytest.ini`, `tox.ini`, `.github/*`, plus `--protect GLOB`) must be byte-identical afterwards. New test files written by the model make the attempt unacceptable unless `--new-tests-cmd` is given; with it they must FAIL on the baseline and PASS on the attempt (vacuous tests rejected). A baseline failure that is an ImportError/SyntaxError/collection error sets `suspicious_red` and `needs_review`: read `new_tests_check.baseline_tail`. `--accept-file` hashes detect a rewritten acceptance script.

Run (repl tool; `host.compute` is not in `python`):
```python
c = host.compute.create('cs-local')
job = c.submit_job(
  intent='cli-subagents loop (<engine>): <task, repo, size>',
  command='''mkdir -p acc && cp accept.py acc/
python3 ~/cli-subagents/agent_loop.py ./task.md <ABS_REPO> --engine <codex|claude> --accept \"python3 $PWD/acc/accept.py\" --accept-file $PWD/acc/accept.py --timeout 3000
rc=$?
L=$(ls -dt <ABS_REPO>/.cli-subagents/loop_* | head -1); mkdir -p out; cp $L/loop.json $L/final.patch out/ 2>/dev/null
exit $rc''',
  inputs=[{'src': 'task.md', 'dst': 'task.md'}, {'src': 'accept.py', 'dst': 'accept.py'}], outputs=['out/**'],
  run_timeout_s=3 * 3000 + 900)
```
Add `--new-tests-cmd`, `--protect`, `--accept-before pass`, `--ladder` as needed. For task mode, submit `agent_task.py` the same way with `outputs=['out/**']` and `--out $PWD/out`. Several jobs in parallel are fine as separate submit_job calls: loops are locked per engine (one codex loop and one claude loop at a time; a second of the same engine exits 40), task runs are not locked. The usage pool of each account is shared with the user's own clients, so do not fan out wide on codex. End the turn and park on the compute notification.

### Tests for existing code (`--tests-only`)
```
python3 ~/cli-subagents/agent_loop.py task.md <ABS_REPO> --engine <E> --tests-only --tests-cmd "python3 tests/test_x.py" \
  --mutation-target pkg/mod.py [--mutation-target ...] [--bug-patch $PWD/acc/bug1.patch ...] [--mutation-min 0.75]
```
`--tests-cmd` runs the whole suite including new tests; every pre-existing file must stay unchanged, at least one new test file must appear, tests must pass on the current code, each `--bug-patch` (a realistic bug you write as `patch -p1`) must make them FAIL, and `agent_mutate.py` (stdlib, Python only; flipped comparisons, swapped operators, changed constants, negated conditions, return to None) must be killed at least `--mutation-min` on two different random samples. Survivors can be equivalent mutants: the accepted result lists them in `needs_review`; read them and lower `--mutation-min` only after judging them equivalent. Read the tests: exact values, not brittle.

### Escalation ladder
`--ladder` rung syntax: `default`, `MODEL`, `MODEL:EFFORT`. A failed attempt (acceptance fails, protected file changed, vacuous or unverified tests, exec error) starts the next rung from a fresh baseline copy with the failure reasons and acceptance output tail in the task text. Never lower model or effort to save quota; evidence for any rung is its acceptance result.

| Engine | Default ladder | Notes |
|---|---|---|
| codex | `default,gpt-6-astra:xhigh,gpt-6-astra:max` | Rung 1 is the host default (`gpt-6.1-sol` at `xhigh`). Astra: OpenAI's strongest option for multi-step work, more quota. |
| claude | `opus:xhigh,opus:max` | Highest non-max effort first, then max. `opus` was `claude-opus-5-5` on 2026-10-17. |

| Task (codex) | Start rung |
|---|---|
| Most coding | default |
| Hardest work, wrong result is costly | `--ladder gpt-6-astra:xhigh,gpt-6-astra:max` |
| Life-science research code | `--ladder gpt-rosalind-5.5:xhigh,gpt-6-astra:xhigh` (one small smoke task passed; no quality comparison) |

`codex debug models` lists what the codex account offers; narrow high-volume models (`gpt-6-luna`) are not used while correctness is the priority.

## Exit codes (loop)
- 0 accepted: read `final.patch` completely and the `needs_review` list; apply with `patch -p1` from the repo root (`--dry-run` first), re-run the acceptance yourself on the real repo, then `python3 ~/cli-subagents/agent_ledger.py mark <id> --clean` (or `--defect --note ...` as soon as a defect is found, even weeks later), and report which engine did the work, rung, tokens and the usage line.
- 10 gated (codex only): window below floor (2% 5h, 1% weekly) or ordinary usage off, AND no credits; do it in Claude; retry after `retry_at` in the attempt's `route.json`.
- 12 the CLI itself reported a usage limit mid-run; do it in Claude Science.
- 20 acceptance did not fail on the baseline (or passes a bad variant): fix the acceptance, not the model.
- 30 every rung failed: Claude takes over; read `loop.json`; never reuse partial results.
- 2 bad input | 40 another loop of that engine is running.

## Usage gating and report
- codex: gated on live usage via `codex_usage.py` (included windows first, then credits). Report line example: `Codex remaining: 5h 94% (resets in 3h 8m), weekly 87% (resets in 5d 0h), credits none`. A failed read says `USAGE READ FAILED`; say so plainly.
- claude: gate = none. Finding (live, claude-code 2.1.302, enterprise account, 2026-10-17): headless `claude -p --output-format stream-json --verbose` emits one `rate_limit_event` per run with `rate_limit_info.unifiedWindows.five_hour|seven_day.{utilization,resetsAt}` plus `status`; nothing is emitted before the run, so it cannot gate a run, only report afterwards. The scripts store the events in `run.json` / `result.json` (`rate_limit_events`) and print `Claude (school) usage from CLI (...): 5h used X% (resets in ...), weekly used Y% (...), status allowed`. The unit of `utilization` is undocumented; the scripts read it as a fraction of the limit (0.02 = 2%), unverified. If no event appears the line is `Claude (school) remaining: not reported by CLI`. A usage-limit error mid-run maps to exit 12. Only what Claude Code itself emits is used: no OAuth usage endpoint, no stored token.
- Copy the report line verbatim into your message after every use of an engine, whatever the outcome. `agent_loop.py` stores it as `final.usage_report.report_line` in `loop.json`; `agent_task.py` as `usage_report` in `result.json`.

## Ledger
One ledger `~/cli-subagents/ledger.jsonl` for both engines and modes; entries carry `engine` and `mode` (`loop`|`task`; loops also `loop_mode` implement|tests_only). `agent_ledger.py summary|list|mark|migrate`. Old `~/codex-offload/ledger.jsonl` entries were copied once with `agent_ledger.py migrate` (idempotent, old file untouched) and are also normalised on read (engine=codex, mode=loop). Use the summary to judge whether higher rungs and the acceptance checks pay off; do not change routing without it.

## Provenance
Loop: `<repo>/.cli-subagents/loop_<ts>/` with `loop.json`, `a/` (baseline), `b1..bN/` (attempt trees with each `run.json`), `final.patch`. Task: `<out>/result.json` etc. Keep `loop.json`/`result.json` and `final.patch` with the commit; every report names engine, CLI version, model, effort and run directory.

## Known limits
- Claude Code Stop hooks (the migrated `claim-gate` hook in `~/.claude/settings.json`) can add a turn after the agent's answer; that turn's reply becomes the CLI's final `result`, so a declaration the agent wrote earlier would be lost. `parse_events` therefore falls back to the last assistant message that contains a ```json block (seen live in the cron test run, 2026-10-17); `events.jsonl` keeps the raw stream. Hook-added turns also cost usage.
- The guards show that the check you wrote passes and tests were not tampered with, not that the code is correct beyond it. A weak acceptance passes weak code.
- Task mode has no independent check: a declaration is the agent's own claim. Verify.
- The codex sandbox and claude can read files outside the workdir; keep acceptance paths out of the task text. Hashes detect edits, not reads.
- Usage is account-wide (other clients draw from the same pool). Codex `used_percent_delta` is integer-rounded; use token totals. Claude token fields: `input_tokens`, `cached_input_tokens` (cache reads), `cache_creation_input_tokens`, `output_tokens`; `cost_usd_reported` is the CLI's own estimate.
- CLI output schemas change across versions (codex app-server, claude stream-json); the canary reruns the full check when the version changes.
