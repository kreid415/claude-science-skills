# Handoff template

Copy the skeleton, fill it, delete what does not apply. Empty sections are
worse than absent ones — they read as "nothing to say here" when they usually
mean "not checked".

Target one page. The next agent reads this cold; every sentence that does not
change what it does next is noise competing with the sentences that do.

## Why each section exists

**Start message for the next chat** — the text the user pastes to open the next
chat, and the same text the outgoing reply ends with. It names this file and its
artifact id in plain text (a pasted marker or @-mention may not survive copying),
states the objective and the single next action, and points at the previous
chat's archive. About 150 words: it points at this document, it does not replace
it. Build it with `handoff_start_message(...)` so the length cap and the id
checks are enforced, and write it last, once the rest is settled.

**Objective** — the user's goal, in their terms, not a description of what you
did. A new session that knows the destination can choose a different route when
yours turns out to be blocked; one that only knows your route will follow it
off the cliff.

**State** — where things actually stand, including what is half-finished.
Distinguish done from believed-done. If a result is unverified, say so here
rather than letting it be inherited as settled.

**Artifacts** — the deliverables, each with its version id so the link
resolves. Say what each one *is for* in a clause; filenames drift from
contents. Flag which is canonical when several versions exist.

**Kernel state (will be lost)** — the section that justifies the document.
Everything in memory disappears when this session ends. For each item: what it
is, whether rebuilding is cheap or expensive, and the exact reload path. Turn
anything expensive into a checkpoint artifact *before* ending, and note
session-scoped installs by name.

**Decisions** — what was chosen and why, one line each. Prevents the next
session relitigating a settled question, and lets it revisit deliberately when
a premise changes.

**Dead ends** — what was tried that did not work, and the reason. This is the
cheapest section to write and the most expensive to omit: without it the next
session spends its first hour rediscovering your first hour.

**Next steps** — ordered, concrete, executable. Name files, parameters, and
commands.

**Open questions** — decisions that need the user. Keeping them in one place
means the next session can ask them all at once instead of stalling repeatedly.

## Skeleton

````markdown
# Handoff: <topic>

**Session:** <date> · <n> messages · <n> folds · frame <this chat's frame id>
**Status:** <one sentence: what is done, what is in flight>

## Start message for the next chat
```
<output of handoff_start_message(...)>
```

## Objective
<What the user is trying to accomplish, in their terms. 2-3 sentences.>

## State
- <Done and verified: ...>
- <Done but unverified: ...>
- <In progress: ... — next action is ...>
- <Blocked: ... waiting on ...>
- <Live runs: paste `handoff_run_state_rows(...)` (host, job/watcher ids, a
  read-only check command per row). Completion notices and sub-agent results
  land in the old chat, and the new chat cannot attach_job these jobs>

## Artifacts
- [<filename>]({{artifact:<version_id>}}) — <what it is, why it matters>
- [<filename>]({{artifact:<version_id>}}) — <canonical version of ...>

## Kernel state (will be lost)
| in memory | rebuild cost | how to restore |
|---|---|---|
| `df_merged` (1.2M rows) | expensive — 40 min join | load `merged.parquet` (checkpoint above) |
| `model` (fitted GBM) | expensive | load `model.pkl` (checkpoint above) |
| `cfg`, `paths` | trivial | re-declare, see `setup.py` |

Session-scoped installs not in the environment: `<package>`, `<package>`.

## Decisions
- <Chose X over Y because ...>
- <Fixed parameter Z at ... because ...>

## Dead ends
- <Tried X — failed because ...>. Do not retry without <what would change>.

## Next steps
1. <Concrete action naming files and parameters>
2. <...>

## Open questions for the user
- <Question that blocks a real choice>
````

## Worked fragment

The difference between a vague and a usable entry, on the same underlying work:

Vague — reads fine, strands the reader:

```markdown
## Next steps
1. Continue improving the model
2. Look at the outliers
```

Usable:

```markdown
## Next steps
1. Re-fit with `alpha=0.3` in `fit_model.py` (0.1 underfit — see Dead ends);
   compare AIC against the 412.7 in `baseline_fit.csv`.
2. Inspect the 14 samples flagged `qc_fail` in `qc_flags.csv` — they drive the
   residual tail. Decide exclude-vs-winsorize with the user before re-fitting.
```

Same for state. "Analysis mostly done" tells the next session nothing it can
act on; "regression fitted and cross-validated; residual diagnostics written
but not reviewed; the heteroscedasticity question in Open questions is
unresolved" tells it exactly where to pick up.

## Worked start message

```
Continue from HANDOFF-calibration.md (artifact id 0b1c2d3e-0000-4000-8000-00000000a001) in this project; read it before doing anything else.
Objective: finish the dose-response calibration and decide the exclusion rule for the 14 qc_fail samples.
Next action: re-fit with alpha=0.3 in fit_model.py and compare AIC against baseline_fit.csv.
Open first: HANDOFF-calibration.md; baseline_fit.csv; qc_flags.csv; merged.parquet (checkpoint).
Lost with the old kernel: df_merged and model; reload from merged.parquet and model.pkl.
Pending: sub-agent "QC review" still running; open finding on the residual plot.
Do not redo: alpha=0.1 (underfit); the log-link model (diverged).
Before reusing any identifier, number or quote from the previous chat (frame 0b1c2d3e-0000-4000-8000-00000000b002), search its archive.
```
