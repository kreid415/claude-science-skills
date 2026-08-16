# Handoff template

Copy the skeleton, fill it, delete what does not apply. Empty sections are
worse than absent ones — they read as "nothing to say here" when they usually
mean "not checked".

Target one page. The next agent reads this cold; every sentence that does not
change what it does next is noise competing with the sentences that do.

## Why each section exists

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

```markdown
# Handoff: <topic>

**Session:** <date> · <n> messages · <n> folds
**Status:** <one sentence: what is done, what is in flight>

## Objective
<What the user is trying to accomplish, in their terms. 2-3 sentences.>

## State
- <Done and verified: ...>
- <Done but unverified: ...>
- <In progress: ... — next action is ...>
- <Blocked: ... waiting on ...>

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
```

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
