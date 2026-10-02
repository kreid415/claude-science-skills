---
name: standing-instructions
description: "Keep a per-project ledger (CONSTRAINTS.md) of the user's standing rules and check every deliverable against it. Load at the start of a project session; whenever the user states or corrects a rule or preference ('I have said many times', 'we have been through this', 'don't use itemize', 'PNG not PDF', 'exclude non-human genes', 'use the standard scores', 'you made that up', 'that is not what I asked', 'answer my questions first'); before producing or revising any figure, table, outline, paper section, slide deck, README, plan or dataset selection; before changing an established design or decision; before executing a plan the user has open questions about; and after any correction, to find every file, doc and memory row that still states the old version. Prevents repeated instructions, unrequested scope changes, skipped questions and stale claims."
---

# Standing instructions

The user's most frequent frustration is repeating themselves. Frustrated messages often trace back to instruction
adherence ("I have said many times...", "You made that up"). Three
mechanisms cause it: a stated rule is never written down, a design is changed without asking, and a correction is
applied to one file but not to the others. This skill removes each of them with a ledger and four gates.

The ledger is the single place where the user's rules live. Conversation memory is not enough: it is lost at
compaction and at session end.

## Files and helpers

`CONSTRAINTS.md` at the repo root (also saved as an artifact, `version_of` the previous one). One entry per rule:

```
## SI-03 · active · data
- rule: Report foundation models on human datasets only
- source: "I have said many times, exclude non-human from foundation models" (user, 2026-09-14)   # or an NB-… id
- added: 2026-10-02
- keywords: foundation            # optional; "*" = always applies
- files: *.md, *.csv              # optional globs the check applies to
- check: {"forbid_literal": ["dataset_A"]}   # optional, machine-checkable part of the rule
- notebook: NB-20261002-03        # linked lab-notebook DECISION
```

Scopes: `data`, `method`, `format`, `writing`, `collaboration`, `process`. Status: `active` or `retired`
(retired entries stay in the file).

All helpers take `root` (the repo root; default is the git toplevel of the cwd) and raise a specific `SI…Error` with an
actionable message. The result dict is attached as `exc.result`. Details and recipes are in `references/`.

| helper | job |
|---|---|
| `si_init(root)` | create `CONSTRAINTS.md` (never overwrites) |
| `si_add(rule, scope, source, root, check=, keywords=, files=)` | record a rule; refuses a missing/unquoted source and near-duplicates |
| `si_retire(id, reason, root)` | retire a rule when the user lifts or replaces it |
| `si_link(id, nb_id, root)` | attach a lab-notebook entry id (verified to exist) |
| `si_load(root)` | parsed ledger; raises if missing or malformed |
| `si_applicable(task_text, scopes=None, root=)` | rules that bear on a task, with the reason each matched |
| `si_check_output(text_or_paths, rules)` | run the rules' patterns on a deliverable; rules without a pattern come back as `manual` |
| `si_open_questions(user_messages, answered)` | question sentences in user messages not yet answered |
| `si_propagate(old_phrase, roots, extra_texts=)` | every remaining statement of a corrected claim |
| `si_scope_check(allowed, root, base=)` | files changed outside what the user asked for |
| `si_sync_notebook(root)` | ledger vs notebook DECISIONs: unmirrored, dangling, later corrected |

## Protocol

1. **Session start.** `si_load(root)` (`si_init` if the project has none) and read the active rules. If the repo has
   a `notebook/`, run `si_sync_notebook(root, strict=False)`: each `unmirrored` user DECISION is either a standing rule
   (add it) or a one-off (leave it, it is in the notebook). A `dangling` or `corrected` result means the ledger is
   out of date; fix it before working.

2. **Capture the moment a rule is stated or corrected.** Do this in the same turn, before continuing the task.
   A statement is a rule when it has any of: a negation or prohibition ("don't use itemize", "no narrative I did
   not dictate"), a format or tool choice ("PNG not PDF", "filenames not artifact IDs"), an inclusion/exclusion ("exclude
   non-human", "keep TopoLa out"), a choice among options you offered ("accept and b"), a project boundary ("OI and
   genetics are separate"), a correction of how you worked, or any repetition marker ("again", "many times", "stop").
   If you are unsure whether it is general or one-off, record it; retiring a rule costs one line, a repeated instruction
   costs the user's patience.
   - `si_add(rule, scope, source, root, check=…)`. `rule` is the user's own words, not your paraphrase. `source` is the
     quoted message and its date. Add a `check` whenever part of the rule can be matched in text (`references/rule-patterns.md`).
   - A rule that contradicts an active one: `si_retire` the old rule with the user's words as the reason, then `si_add` the new one.
   - Mirror to the lab notebook: call `nb_entry(root, **result["nb_entry_suggestion"])`, then `si_link`. A DECISION the
     user made that sets a recurring constraint goes the other way: `si_add(..., source="NB-…")`.
   - Save `CONSTRAINTS.md` as an artifact (`version_of`). Commit it. Say "Recorded SI-NN: <rule>" in the reply.
   - Rules that hold in every project go to profile memory (`write_memory`) as well. The ledger stays canonical for the
     project; memory keeps a one-line pointer.

3. **Before every deliverable, check it against the ledger.** A deliverable is any figure, table, outline, section,
   deck, README, plan, dataset selection or summary.
   1. `a = si_applicable("<what you are about to produce>", root=…)`. Read every matched rule. Skim `a["unmatched"]` ids
      too; matching is deliberately generous but not exhaustive.
   2. Produce the deliverable so that it satisfies the rules. Do not produce first and patch later.
   3. `si_check_output([paths of the saved files], a)` (strict by default; fix and re-run until it passes).
   4. For each rule in `manual`, check by hand against the saved output and say how ("SI-06: outline contains only
      the 4 elements you dictated").
   5. Put one line in the reply: `Standing rules: SI-02 ✓ checked, SI-05 ✓ read the output, SI-09 n/a`. A rule
      without a verdict is not checked.

4. **Never change an established design or decision unilaterally.** A decision is established if the user stated or
   confirmed it, or it is in the ledger or a notebook DECISION. Changes that need a proposal first: dropping or adding
   an arm, dataset, metric or section; swapping a metric definition for a "fairer" one; implementing an option other than
   the one the user picked; collapsing a class of items to a representative; curating a subset of data the user asked
   for in full; reframing the research question; fixing a label instead of the data behind it; any durable action nobody
   asked for (publishing a skill, pushing, deleting).
   - Propose, then wait: "Proposed change: <old> → <new>. Why: <reason you can show>. Affects: <files/results>.
     Approve?" Do not invent a rationale; if you cannot show one, say so.
   - When the user picks among options, write the choice down with its label (`si_add` with the option text) before
     implementing, then re-read it against your result.
   - Scope: note `base = git rev-parse HEAD` and the paths the request covers when you start. Before reporting, run
     `si_scope_check(allowed, root, base=base)`. List every substantive edit you made, including small ones (a changed
     word in a model description is a change).
   - Silently leaving something out is also a change. State what you did not cover and why (a model family not run, a
     dataset filtered out, a data point left out of a summary).

5. **Answer the user's questions before executing a plan.** `si_open_questions(user_messages, answered)` over the
   user's last messages. Answer each question in the reply, in order, quoting it, and only then continue. If the user's
   message says "answer my questions in the previous message", pass the previous message as well. A commitment you
   make in chat ("I'll fold that into the plan") is not done until the file is edited: edit it in the same turn and show
   the diff.

6. **Propagate every correction.** When the user corrects you, or you retract or correct a claim, number or design:
   1. Write down the old phrase(s) exactly as they appear (and the number, if a count changed).
   2. `si_propagate(old, [repo root, other doc dirs], extra_texts={…})`. Put memory rows
      (`search_memory(old)`) and artifact bodies (`host.artifacts(content=old)`, read the hits) into `extra_texts`.
   3. Fix every hit. Re-run until `clean`. Update memory rows (`write_memory` replace) and write a notebook CORRECTION.
   4. Check the text you are about to send (summary, final message) against the correction too; stale claims reappear there.
   5. Save new artifact versions. List the files you changed in the reply.
   6. Retract at the moment you know, with all its dependents. Do not wait for the user to find the slide that still says it.

## Rules for the rules

- The ledger records what the user said. Do not add rules the user did not state, and do not generalize one example into
  a rule wider than the user's words.
- A check passing means the pattern is absent or present, nothing more. State which rules you checked by pattern and
  which by reading.
- When the user pushes back on your framing ("you made that up"), the correction is a rule: the central question, the
  scope of the outline, or the claim they allow. Record it and re-read your deliverable for the same fault.
- Be brief in the reply: the `Standing rules:` line, the `Recorded SI-NN` lines, and the propagation list. No narration.

## Provenance

The checks in this skill come from a review of real failures in AI-assisted research sessions. `tests/` in the repository holds synthetic fixtures that reproduce each failure pattern: known-bad inputs must fail, known-good inputs must pass.

## References

- `references/rule-patterns.md`: scope guide and a cookbook of `check` patterns for the rule types seen so far.
- `references/protocol-details.md`: templates for the change proposal, question answers and correction sweep.
