---
name: manuscript-to-codex
description: "Route every manuscript update to the user's Codex CLI (engine codex of cli-subagents) instead of writing it in Claude Science or Claude Code. Use whenever a change to a paper is requested or implied: main text, supplement, abstract, captions, tables, bibliography, figure placement, change log, number-ledger rows tied to the text, applying PDF annotations or reviewer comments, propagating a changed result, outline-to-journal conversion passes, wording or layout passes, of any size including one-sentence edits. Claude Science keeps the brief, the hidden pass/fail check, verification, build, artifacts and talking to the user. Overrides the Claude-Code-first writer order in outline-to-manuscript and cli-subagents for manuscript work only."
---

# Manuscript updates go to Codex

User instruction (2026-11-21): defer any manuscript update to Codex. This skill changes WHO edits the manuscript files. It
does not change WHAT a pass must do: `outline-to-manuscript` (procedure, narrative style, checks, delivery) and
`cli-subagents` (scripts, safety rules, exit codes) still apply in full. Where they say "Claude Code first" for writer runs,
this skill wins for manuscript work.

## 1. Scope
In scope (always Codex, whatever the size): any edit to a manuscript or supplement source (`.tex`, `.bib`, captions, tables
typeset in the paper, `CHANGES.md`, `GUIDELINES` notes), ledger rows and small scripts added only to back new text, figure
placement and layout passes, annotation passes, result-change propagation, conversion passes.
Out of scope (unchanged routing): generating figure data or rerunning analyses (the analysis decides the numbers, not the
writer); fact review of the written text (section 5); author-only text (funding, contributions, acknowledgements, author
list), which nobody writes; anything a content-safety filter blocked or a model refused (never offloaded, tell the user).

## 2. What stays in Claude Science
Talking to the user and asking the author's decisions; writing the brief; writing the hidden pass/fail check (kept outside
the repository, must fail on the untouched tree); reading the declaration, deviations and full diff; rerunning every check;
spot-checking numbers against data files; building the saved zip with `doc-build-gate` and viewing changed pages; saving
artifact versions; committing and fetching the branch into the user's repository. Claude Science does not edit manuscript
files itself, except when every engine in section 6 is out.

## 3. Before the first Codex run in a session
`python3 ~/cli-subagents/agent_canary.py --engine codex --auto` on `cs-local` (call_command, login_shell=True). Exit 1:
tell the user which check failed and go to section 6. Confirm the profile list once if unsure:
`python3 ~/cli-subagents/make_codex_profiles.py --check` (exit 0 = profiles current). Seen 2026-11-21: codex-cli 0.159.3,
profiles `MANUSCRIPT_WRITER` (gpt-6-luna, high), `RIGOR_REVIEWER` and `EXTERNAL_REFEREE` (gpt-6.1-sol). There is no Codex
`MANUSCRIPT_CONVERTER` profile; use `MANUSCRIPT_WRITER` with the rules pasted in.

## 4. Running the pass
Mode:
| Pass | Mode | Model, effort |
|---|---|---|
| Narrative rewrite, conversion, annotation pass, result propagation over several files | loop (`agent_loop.py`) with the hidden check | `--ladder gpt-6.1-sol:high,gpt-6.1-sol:xhigh` |
| Ledger rows, small scripts and their tests, change log, layout moves | loop with the hidden check | default ladder (`gpt-6-luna:high,gpt-6-luna:xhigh`) |
| Small wording edit where a check would only restate the edit | task (`agent_task.py`, `--access workspace-write`) and read the full diff | `gpt-6-luna`, `high` |
| Read-only scouting (what must change when a result changes) | task, `--access read-only` | `gpt-6-luna`, `high` |

Task mode command (submit from `repl`; the positional argument is the PATH of the brief file; `--out` must be outside
`--workdir`; work in a fresh clean clone per pass so it is used in place and the diff is recorded):
```python
c = host.compute.create('cs-local')
job = c.submit_job(intent='Codex manuscript pass: <short descriptive name>',
  command='python3 ~/cli-subagents/agent_task.py ./task.md --engine codex --agent MANUSCRIPT_WRITER '
          '--model gpt-6-luna --effort high --workdir <ABS_FRESH_CLONE> --access workspace-write '
          '--out $PWD/out --timeout 3600',
  inputs=[{'src': 'task.md', 'dst': 'task.md'}], outputs=['out/**'], run_timeout_s=3600 + 900)
```
Loop mode: `agent_loop.py` has no `--agent` flag (checked 2026-11-21), so a loop run does not load the
`MANUSCRIPT_WRITER` role: open the brief with two sentences of role ("You are the writer revising a journal manuscript...")
and paste all section 5 rules. Use the `agent_loop.py` command in cli-subagents with `--engine codex`, the hidden check as `--accept` plus
`--accept-file`, `--protect 'paper/outline/*'` (and any data or figure-data folder the pass must not touch), and the ladder
from the table. One Codex loop runs at a time (a second exits 40); do not run several Codex passes in parallel, the usage
pool is shared with the user's own clients. End the turn and wait for the compute notification (no polling cells).
`--add-dir` on Codex makes the folder WRITABLE: pass read-only inputs (data, PDFs of annotations) inside the clone or by
absolute path in the brief; a Codex sandbox can read any path.

## 5. The brief (Codex sees no Claude skills and no chat)
Follow `outline-to-manuscript/references/briefs.md` items 1-9, and paste these rules into every brief, as text:
1. Form, not content: never change a result, number, analysis or figure value; report a number that disagrees with the data
   with its file and line instead of editing it. Text the lead fixed is quoted and must not change.
2. The narrative rules of `outline-to-manuscript/references/narrative-style.md` that the pass needs (paste the lines, not the
   file name), plus the project's CONSTRAINTS.md path and the instruction to obey every rule in it.
3. Plain language for a PhD student in a neighbouring field; no metaphors; spell out abbreviations at first use.
4. LaTeX: no new packages without a stated reason; floats `[H]`; do not deliver or rename a bare `.tex` outside the repo.
5. Code (ledger scripts, checks): smallest complete change; keep seeds, provenance and tests; never delete or weaken an
   existing test; add the fewest tests that run every changed line; run the repository's standing checks and report them.
6. Do not build with network access assumptions: the lead builds the PDF; Codex may run the local build only if it works
   offline, and reports the result either way.
7. Commit on the named branch with the named message; do not push; exclude your run folder in `.git/info/exclude`.
8. Closing declaration (cli-subagents appends the JSON form): per item done, deviations, files changed, commits, checks
   run with results; at most 150 words plus a detail file in the repo.
Never put credentials in the brief or the clone.

## 6. When Codex is out (no question to the user, tell them once per switch)
Codex out = canary exit 1, exit 10 (no usage and no credits; retry after `route.retry_at`), exit 12 (usage limit mid-run),
or exit 30 (both ladder rungs failed). Then: Claude Code with the same brief (`--engine claude --agent MANUSCRIPT_WRITER`,
`claude-opus-5-5` for narrative passes; add the Claude Code tool limits of briefs.md item 8) -> Claude Science
(`MANUSCRIPT_CONVERTER` profile sub-agent, or the lead itself). State engine, which window, and its reset time; return to
Codex for the next pass once it has reset. An UNDECLARED result (exit 13) is not used: rerun once on Codex with `xhigh`,
then switch.

## 7. Fact review stays independent
The fact review of a Codex-written pass runs on Claude Code (`--agent RIGOR_REVIEWER`, read-only, schema from
`outline-to-manuscript/references/claims-and-review.md`), so the reviewer is a different model from the writer. If Claude
Code is out, use the Codex `RIGOR_REVIEWER` profile (gpt-6.1-sol) and say in the report that writer and reviewer came from
the same model maker.

## 8. After every run
1. Read `result.json` (status, `declaration.deviations`), `diff.patch` or `final.patch` in full.
2. Rerun the hidden check and the standing checks yourself on the Codex commit; spot-check five printed numbers against
   data files; view every changed figure.
3. Build the saved zip in an empty folder (`doc-build-gate`), view the title page and changed pages.
4. `python3 ~/cli-subagents/agent_ledger.py mark <id> --clean` or `--defect --note ...`.
5. Report: engine, CLI version, model, effort, tokens, the usage line copied verbatim, run folder, branch head, what was not
   done. Say plainly if anything could not be verified.
