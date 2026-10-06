---
name: codex-offload
description: "Offload well-specified work (refactors, scripts, tests, bulk edits, long repo edits) from Claude Science to the user's Codex ChatGPT subscription running on the Claude Science host machine (an SSH compute target pointing at that machine, `<HOST_TARGET>`), gated on live Codex 5-hour and weekly usage. Policy: use Codex until a window is exhausted, then do the work in Claude and return to Codex after the reset. Apply AUTOMATICALLY, without being asked, to any large self-contained code or text-editing task that meets the criteria below; also when the user says offload/delegate to Codex or wants to save Claude quota."
---

# codex-offload

Policy: use Codex until its quota runs out, then switch to Claude.

## Setup
- `<HOST_TARGET>`: an SSH compute target registered in Claude Science that points at the machine running the daemon (sshd on, key-based login to itself). Replace the placeholder with its name.
- Codex CLI installed on that host and logged in with ChatGPT (`codex login`, or `codex login --device-auth` on a headless box); `codex login status` should report the ChatGPT login. The login stays on the host, outside the sandbox.
- Copy `scripts/codex_usage.py` and `scripts/codex_run.py` to `~/codex-offload/` on the host (call_command + base64 works; keep both in one directory).
- `codex_usage.py` starts `codex app-server` and calls the JSON-RPC method `account/rateLimits/read`. Windows are labelled by `windowDurationMins` (300 -> 5h, 10080 -> weekly), never by primary/secondary. Written against Codex CLI 0.159.x; the app-server schema changes between versions, so re-run it after upgrades.

## Automatic use
Offload without being asked when ALL hold: (1) code or text editing work, roughly >10 Claude tool turns or >200 changed lines; (2) the spec fits one task file with a runnable acceptance command; (3) no need for host.*, the kernel, MCP connectors or other Claude skills; (4) no credentials/secrets and no hazardous-bio material in the task; (5) the repo is a clean git tree, or the work happens in a copy/worktree so Codex cannot clobber uncommitted changes. Otherwise do it in Claude; tiny edits always stay in Claude. State in one line that you are offloading and why. Afterwards review the diff, run the acceptance command, and report that Codex did the work plus its token count from events.jsonl (`turn.completed` usage).
Each submit raises an approval card unless the user has set Always-Allow for the host target; mention this once if cards are blocking automation.

## Route a task
1. Offload to Codex only if the task is self-contained, specified in one markdown file, and needs none of: host.* SDK, the persistent kernel, MCP connectors, Claude-only skills. Otherwise do it in Claude.
2. Write the task file with: goal, files/paths, acceptance test or command, constraints, "do not touch X". Codex gets no conversation context.
3. Submit from the `repl` tool (host.compute is not in `python`):
```python
c = host.compute.create('<HOST_TARGET>')
job = c.submit_job(
  intent='Codex offload: <task, repo, expected size>',
  command='''python3 ~/codex-offload/codex_run.py ./task.md <ABS_REPO_OR_WORKDIR> --timeout 3000
rc=$?
mkdir -p out && cp -r <ABS_REPO_OR_WORKDIR>/.codex-offload out/ 2>/dev/null
exit $rc''',
  inputs=[{'src': 'task.md', 'dst': 'task.md'}], outputs=['out/**'], run_timeout_s=3600)
```
   Then end the turn and park on the compute notification.
4. Read the exit code:
   - 0: Codex ran. Review `last_message.md`, `diff.patch`, and `run.json` (`used_percent_delta`) before saying anything is done; run the acceptance test yourself.
   - 10: gated, route to Claude. `route.json` has `reason` and `retry_at` (Unix seconds of the window reset). Do the work in Claude now; return to Codex after `retry_at`.
   - 12: Codex hit the usage limit mid-run. Treat as 10, then inspect the partial diff before reusing it.
   - 11: Codex exec failed; read `events.jsonl` / `run.json.stderr_tail`, fix the task, retry once, then fall back to Claude.
5. Never mark the work verified from Codex's own summary. Codex output is an untrusted draft until tests/diff review pass.

## Gate thresholds
`--min-5h 2 --min-week 1` (percent remaining). Raise them to keep a reserve; the stated policy is to run to exhaustion.

## Provenance
Each run writes `.codex-offload/<run_id>/` in the workdir: route.json, usage_before/after.json, events.jsonl (token usage), last_message.md, diff.patch, run.json (codex version, task sha256, git head, exit code, usage delta). Keep run.json with the commit.

## Known limits
- Usage is account-wide: the Codex desktop app and any other Codex client draw from the same pool.
- `--sandbox workspace-write` is the default; use read-only for review tasks. Avoid danger-full-access.
- submit_job only harvests files under the job workdir, hence the cp into `out/`.
- If the 5-hour window is empty but weekly has room, waiting for `retry_at` is cheaper than Claude only if the task is not urgent.
- `used_percent_delta` in run.json is integer-rounded, so small tasks read 0 or 1. For per-task cost read the token counts in the `turn.completed` events of events.jsonl.
