# Protocol details

## Capture: what to say

Reply line, one per rule: `Recorded SI-07 (format): No itemize lists. Source: your message of 2026-09-20.`
If the new rule replaces an old one: `Retired SI-03, recorded SI-09.`

Statements that sound like one-off requests but are rules:

| the user says | rule |
|---|---|
| "don't use itemize, explain it in prose" | prose for explanations (format) |
| "I did not dictate any [narrative]" | the document contains only dictated content (writing) |
| "exclude non-human from foundation models" | data filter, applies to every table and figure that reports them |
| "accept and b" | option b is the design; record the option text |
| "the OI and genetics work are separate" | project boundary; do not extend one into the other |
| "I want to use the standard published scores" | method definition; no substitutes |
| "all the data" | include everything; curation needs a proposal |

## Change proposal (step 4)

```
Proposed change to <decision / SI-id / NB-id>:
- Now: <what the user confirmed>
- Proposed: <the change>
- Why: <evidence you can show: file, number, source>
- Affects: <files, results, figures>
Approve, or keep as is?
```

Send it, stop, and do not edit. If the user's earlier choice cannot be implemented as stated (a dataset lacks the
column, a tool is missing), say that, show the evidence, and offer options; do not substitute silently.

## Question answers (step 5)

```
Your questions from the last message:
1. "<question as asked>": <answer, with the file or number it rests on>
2. "<question>": <answer>
Plan unchanged / changed because <…>. Proceeding with <…>.
```

Pass the text of each answer you wrote (or the question text you answered) to `si_open_questions(..., answered=[…])`; it
raises while any question lacks coverage. A question that is "why did X happen" needs evidence from the output, not a
restatement of the earlier summary.

## Correction sweep (step 6)

1. Old phrases: list the sentence fragments the old claim uses (`nearly independent directions`, `99 PDFs`, `best
   reproduction: method 3`). Run `si_propagate` once per distinct wording; numbers are often written two ways.
2. Roots: the repo, the paper directory, slide and outline directories, `README.md`, protocol files, scripts (docstrings and
   comments state results too).
3. `extra_texts`: `search_memory(old)` hits (memory row text, keyed by `mem_id`), artifact bodies from `host.artifacts(content=old)`,
   and the text of your draft final message.
4. Fix all hits. Edit memory rows with `write_memory` replace, not by appending a contradiction.
5. `si_propagate` again until `clean`. Notebook pages are append-only and skipped by design; record the correction with a
   CORRECTION entry instead.
6. Reply: `Corrected <claim>: updated <n> places (<paths>); memory rows <ids>; new artifact versions <names>.`

Counts that depend on each other (a table row count and the sentence that cites it, a total and its parts) are corrections
too: when one changes, search for the other.

## When the ledger is wrong

If the user says a recorded rule is no longer wanted, `si_retire` it with their words as the reason. If a rule was recorded
too broadly, retire it and add the narrower one; do not edit entries by hand. If `si_load` raises on a hand-edited file,
fix the named line; the helpers never skip a malformed entry.
