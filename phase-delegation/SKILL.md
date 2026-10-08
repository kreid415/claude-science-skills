---
name: phase-delegation
description: "Use when a chat is about to run a well-specified multi-step phase of work that does not need the user mid-way: launching and waiting on a run or wave (local or SLURM), harvesting outputs, validating a finished run against its manifest, triaging a failed job, or computing a stated result from finished outputs. The main chat stays a thin coordinator and each phase runs in a fresh sub-agent with a brief, a structured-output schema, artifacts in and out, and an audit of the result, so long waits and retries cost small-context steps and the main chat does not grow. Also use when the user asks to keep the main chat small, to run a wave without supervising it, or to hand monitoring to a sub-agent. Not for interactive exploration, back-and-forth decisions, or work under about ten steps."
---

# phase-delegation

The main chat coordinates; each well-specified phase runs in a fresh sub-agent and returns a compact, audited result. Cost per step grows with context, and long chats hold most of the cost, so moving long waits, retries and harvests into small contexts keeps the main chat short without the user opening a new one.

Needs the Delegation toggle (ultra mode); `host.delegate` raises without it. Helpers: `scripts/pd.py` (stdlib), loaded in the `repl` tool, where `host.delegate` lives:

```python
PD = "scripts/pd.py"   # use the absolute path shown when the skill loads
exec(open(PD).read())
```

## When to use it

| use | do not use |
|---|---|
| launch + wait + harvest a run or wave (`run`) | interactive exploration, choosing what to try next with the user |
| check finished outputs against a manifest (`validate`) | phases under ~10 steps: the brief and audit cost more than they save |
| find and fix why a run failed (`triage`) | work whose next step depends on in-memory state of this chat |
| compute a stated result from validated outputs (`analyze`) | anything the user must approve mid-way, other than approval cards |

## Workflow (coordinator)

1. **Gate first, in this chat.** Before a `run` phase that launches compute, the experiment-preflight GO record exists (WORKFLOW GATE rule); pass it to the child as an input artifact. Launch nothing on NO-GO.
2. **Hand files by artifact.** `save_artifacts` every input; the child sees only its brief, so put hard requirements (full data, named reference, settings, compute target) in the brief, not in `context_summary`.
3. **Write the brief.** `pd_brief(phase, objective, inputs={role: version_id}, outputs=[role, ...], acceptance=[...], requirements=[...], host=...)`. Acceptance checks are concrete: a command and the value it must show, a file and its expected row count. It refuses an unknown phase, empty acceptance, inputs without version ids, and text that pre-authorizes simulated or synthetic fallback data.
4. **Dispatch and end the turn.** `fids = host.delegate([pd_request(name, brief, phase), ...], wait=False)`, then end the turn. The session parks and each child's landing wakes it once. Do not loop `host.collect` with timeouts: each timeout is a full-context step of this chat.
5. **Audit each landing.** `r = host.collect([fid])[0]`; `a = pd_audit(r, outputs, acceptance_n=len(acceptance))`. `a['limits']` (declared deviations) travel with every number from that result into anything you report. A child that returns with `live_jobs` broke the contract: its notices are lost, so check those jobs by id on the host.
6. **Follow up with the same child.** A fix or extension of what a child did goes to that child with `host.send_message(fid, ...)`, which resumes it with its context; a new delegation starts from nothing.
7. **Validate with a different sub-agent.** After `run`, a `validate` phase gets the harvested artifacts and the manifest; it does not reuse the runner's conclusions.
8. **Report** through claim-gate: values read back from the returned artifacts, scoped by `limits`.

Default profiles (`pd_request(..., profile="default")`): `run` and `triage` use CLUSTER_OPS; `validate` and `analyze` use the domain specialist for the data (e.g. SC_ANALYST, ML_ANALYST; RIGOR_REVIEWER for a rigor review), passed explicitly.

## Contract for the child (written into every brief)

- Read the target's compute notes before submitting; cluster work loads cluster-rules.
- Jobs longer than ~10 min run under job-watch inside the submitted job, so completion is one notice.
- A job's `compute_done` reaches only the frame that submitted it, so the child that launches a job waits for it (`wait_for_notification`, 1800 s) and harvests it before returning. It never returns with a job live.
- Own job ids only; two failed submits of the same job mean `needs_human`; a denied approval or blocked resource means `needs_human`, never substitute data.
- Returns `PD_SCHEMA`: status (`done`/`failed`/`needs_human`/`partial`), a short summary, jobs (ledger id, host, scheduler id, state, watcher state dir, report line), artifacts (role, version id), checks (name, ok, evidence), next action; plus `deviations`.

## Interlocks

- **session-handoff**: running children make its check not `quiet`, so the chat does not rotate under them; their jobs carry this chat's root frame id, so they appear in its run-state table. Results of a child come back only to the chat that dispatched it.
- **job-watch** runs inside each `run` phase's job; **run-status-board** builds the board a `validate` phase checks; **experiment-preflight** gates before `run`; **claim-gate** before reporting.

## Pitfalls

- A blocking `host.delegate` call is cancelled by an explicit Stop; dispatch with `wait=False`.
- `wait=False` with a single dict returns one descriptor, not a list: pass a list.
- A child that parks on an approval card waits for the user; do not answer for it, and never write a brief that tells it to work around the card.
- Large results come back as artifacts referenced by version id; keep `summary` short.
- End a job command with the watcher (`jw.py watch ...`), not with a later command: `watch ...; cat result.txt` makes the job's exit code that of `cat` (live test 2026-10-08: the child had to report the watcher's exit from state.json instead). Put follow-up reads in the acceptance checks.
- A profile loads only skills in its catalog. CLUSTER_OPS carries job-watch and cluster-rules (attached 2026-10-08); check `host.agents.get(profile)['skillNames']` before a brief relies on a skill.
- CLUSTER_OPS's system prompt asks the user to confirm cluster, partition, account, size and walltime before every cluster submit, so a cluster `run` phase pauses on that question once per submit. The coordinator cannot answer it.

Tests: `tests/phase-delegation_tests.py` (stdlib): brief validation, marker construction, schema shape, and audit of completed, failed, prose-only, undeclared, partial and live-job results.

Live test (2026-10-08): a `run` phase as CLUSTER_OPS submitted a job-watch job on ssh:cs-local, parked, received its own `compute_done`, harvested and returned `PD_SCHEMA` output in 89 s; the coordinator dispatched with `wait=False`, woke once on `child_landed`, and `pd_audit` returned ok. The child's job carried the coordinator's root frame id in the ledger, and the session-handoff check reported not quiet while the child ran. Not yet tested: a cluster `run` phase, `validate`/`triage`/`analyze` phases, a failing child.
