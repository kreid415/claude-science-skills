---
name: session-handoff
description: Judge whether a working session has grown long enough to hand off, and write the handoff artifact that lets a fresh session continue without losing the thread. Use when the user asks "is this session too long", "should I start a new session", "can you hand this off", "write a handoff", "summarize where we are so I can continue tomorrow", or worries about slowness, context limits, or losing work on restart; also run the cheap size self-check at natural checkpoints in any long session and raise rotation yourself when the numbers cross the thresholds here. Rotation is judged on TASK BOUNDARIES first and size second — a long session on one continuous task is fine, a short one that changed topic is not. Not for the daemon-level restart, memory or database symptoms of the app process itself (that is science-daemon-ops), and not for compacting a single oversized artifact.
---

# Session handoff

A session ends well when the next one starts without re-deriving anything. That
is the whole job: decide when to rotate, then write down what a fresh agent
cannot recover on its own.

Two things make this worth doing deliberately. Continuity is cheaper than it
looks — artifacts, durable memory, conda environments and credentials all
carry over, and the archived transcript stays searchable — so rotation is not
a loss. But the **live kernel does not carry over**, and that loss is silent:
in-memory dataframes, fitted models, session-scoped `pip install`s, and
anything held only in a variable are gone the moment the session ends. A good
handoff is mostly an inventory of that gap.

## When to rotate

**Task boundary is the primary signal, not length.** Rotate when the work
itself changes: a new topic or dataset, an approach abandoned for a different
one, a shift from exploration to writing up. Stay put while iterating on the
same objects, and while remote jobs or sub-agents are still in flight — their
results land in *this* session.

Size is the secondary signal, and it is measurable rather than a matter of
feel. Run the self-check below; the thresholds come from observed fold
behaviour across real sessions, not from guesswork — the fold rate stays at
0% below 120 messages, sits near 10-25% through the 120-400 range, then jumps
to 67% at 400-800 and 100% above 800. The jump, not the absolute number, is
what the table below encodes.

| message count | folds so far | reading |
|---|---|---|
| under ~200 | 0 | healthy — no action |
| 200-400 | 0-1 | fine; rotate if you are at a task boundary anyway |
| over ~400 | 1+ | rotation earns its keep — two thirds of sessions this size have already folded |
| over ~800 | 2+ | rotate at the next boundary; this size reliably folds repeatedly |
| over ~2000 | 5+ | rotate now — this is where sessions start to slow the app itself |

Folds are not damage: a fold keeps user messages verbatim and compresses the
rest, and everything archived stays searchable. But each one means earlier
detail now reaches you as summary rather than transcript, so precision about
identifiers, numbers and quoted text degrades — check the archive rather than
trusting recall once folds are non-zero.

The upper rows have a cost outside your context, too. All of the app's database
access serializes through one lock, and session hydration re-reads every
message row under it; a session in the thousands of messages turns each
hydrate into a multi-hundred-millisecond hold and can end in a watchdog
respawn of the whole daemon. If the user is *also* reporting app slowness or
restarts, these are the same problem seen from both ends — see
`science-daemon-ops`.

## The size self-check

Run this in the `repl` tool. Substitute this session's frame id (it is in your
context as **Frame ID**); the query needs nothing else.

```python
FID = "<this session's frame id>"
q = host.query("""
  SELECT json_extract(context_data,'$._message_count')      AS msgs,
         json_extract(context_data,'$._user_message_count')  AS user_msgs,
         json_extract(context_data,'$._context_used')        AS ctx_used,
         json_extract(context_data,'$._compaction_count')    AS folds,
         (SELECT COUNT(*) FROM execution_log e WHERE e.frame_id = f.id) AS cells,
         (SELECT COUNT(*) FROM artifacts a WHERE a.root_frame_id = f.id
            AND a.is_ephemeral = 0)                          AS artifacts,
         total_cost
  FROM frames f WHERE f.id = ?
""", [FID])
print(dict(zip(q["columns"], q["rows"][0])))
```

Read `msgs` and `folds` against the table above. `ctx_used` is the weaker
signal — sessions run past 300k without folding and some fold below 150k, so
it tells you roughly where you sit in the window, not whether to rotate.
`cells` and `artifacts` size the handoff you are about to write, not the
decision.

Do this at natural checkpoints — after finishing a deliverable, before
starting something new — rather than mid-thought. It costs one cell.

## Raising it with the user

Rotation is the user's call; you are reporting a measurement and an
opportunity, not asking permission to continue. Keep it to two sentences: the
numbers, the boundary you have reached, and the offer. "We're at 480 messages
with one fold, and the calibration work just wrapped — want me to write a
handoff so the analysis starts clean?" Then let it go if they say no; do not
raise it again until the next threshold or the next boundary.

Never rotate silently, and never treat the handoff as finished work in itself:
if they decline, keep working in this session.

## Writing the handoff

Save it as `HANDOFF-<topic>.md` and keep it short enough to read in a minute —
a fresh agent needs orientation, not a transcript. Include only what cannot be
recovered by looking at the artifacts, and write for someone competent who has
no memory of the conversation.

Read `references/handoff-template.md` for the section-by-section template and
the reasoning behind each one.

Four helpers load with this skill and do the mechanical parts, so your effort
goes into the judgement the document actually needs:

- `handoff_artifact_lines(frame_id=...)` — this session's artifacts as
  ready-to-paste bullets with resolvable ids; you fill in each description.
- `handoff_kernel_inventory(namespace=globals())` — the kernel-state table
  from live variables, with real shapes and sizes; rebuild cost and reload
  path are yours to fill, since nothing can infer them.
- `handoff_write(topic, body)` — writes `HANDOFF-<topic>.md` to the workspace.
  Saving it as an artifact stays a separate deliberate step.
- `handoff_check(text)` — flags missing sections, unresolvable links, and
  unfilled placeholders. Run it before saving; it catches the failure modes
  that strand a receiving session.

Four practical rules make the difference between a handoff that works and one
that reads well but strands the next session:

**Reference every artifact by id, not by name.** Take the id from the
`save_artifacts` result or `host.artifacts()`. A bare filename is not
resolvable from a new session, and two artifacts can share a name.

One trap here costs a rewrite if you hit it blind: a literal `{{artifact:...}}`
marker written inside a submitted code cell is rewritten to a local filesystem
path *before* the cell executes, so the string that lands in your file is
already resolved and resolves for nobody else. When generating handoff text in
a cell, build markers with `host.artifact_marker(version_id)`;
`handoff_artifact_lines()` already does, and `handoff_check()` flags a
pre-resolved path if one slips through. Writing the marker by hand in your
*response* text is fine — it is only code cells that pre-resolve.

**Inventory the kernel loss explicitly.** List what is in memory now, whether
it is expensive to rebuild, and where the reload comes from. Anything costly
should be a checkpoint artifact before you end the session, not a note saying
it was lost — `save_artifacts(..., checkpoints=[...])` with a `.parquet` /
`.h5ad` / `.rds`, and the reload line written next to it. Note session-scoped
`pip install`s by name; a fresh kernel will not have them.

**Record the decisions and the dead ends.** These are the two things a new
session cannot reconstruct from files and will otherwise redo. One line each:
what was chosen and why, what was tried and why it failed. This is the highest
value-per-word content in the document.

**Make the next steps ordered and concrete.** "Continue the analysis" is not a
next step. "Re-run `fit_model.py` with `alpha=0.3` (0.1 underfit — see Dead
ends), then compare AIC against `baseline_fit.csv`" is.

Also write the durable facts to memory as you go — decisions, conventions,
constraints — because memory carries over automatically and needs no reading
step. The artifact is for the shape of the work in flight; memory is for the
facts that outlive it. Do not duplicate what is already derivable from
`host.artifacts()` or lineage.

## Ending the outgoing session

Checkpoint expensive state, save the handoff, confirm both are in the artifact
tray, and tell the user in one line how to start the next one. Stay in the same
project: artifacts and memory are project-scoped, so a new session there finds
everything by itself, while a new session in a different project has to be
pointed at it explicitly.

## Resuming from a handoff

The receiving session inherits more than the document. Artifacts, durable
memory, conda environments and credentials are already there; the archived
transcript of the previous session is searchable. The handoff is orientation —
it tells you which of that inherited material matters and what state was lost.

Work in this order, because each step can invalidate the next:

1. **Read the handoff first, before touching anything.** Objective and Next
   steps tell you what the session is for; Decisions and Dead ends stop you
   re-running work that already failed.
2. **Verify the artifacts it names still exist.** `host.artifacts()` — a
   handoff can outlive the files it references, and a version id in the
   document may not be the latest version any more. Check before you build on
   one.
3. **Rebuild only the kernel state the next step actually needs.** The
   inventory lists everything the previous session held; that is not a
   restore list. Load the checkpoint you need for step 1 of Next steps and
   leave the rest until something asks for it.
4. **Re-check anything the handoff marked unverified.** State entries flagged
   as believed-done are exactly the claims most likely to be wrong, and they
   are cheap to confirm now and expensive to discover later.
5. **Say what you inherited and what you skipped**, in a sentence, before
   starting work. The user needs to know the resumption was faithful — and if
   the handoff missed something, that sentence is where they will catch it.

When the handoff is thin or the objective has moved on since it was written,
ask rather than infer. A handoff is a snapshot of intent at one moment; the
user is the live source. Searching the previous session's archived transcript
is the other recourse — it is retained and searchable, so a detail the handoff
omitted is usually still recoverable rather than lost.
