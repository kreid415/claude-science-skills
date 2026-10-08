---
name: session-handoff
description: Run automatically when a working session has grown too long to sustain performance and token cost (300 messages, 280k tokens of latest context, or the first fold), and produce the handoff artifact plus a ready-to-paste starter message for the next chat. Also use when the user asks "is this session too long", "should I start a new session", "can you hand this off", "write a handoff", "summarize where we are so I can continue tomorrow", or worries about slowness, context limits, or losing work on restart. The user's profile tells the agent to run the size check every turn and to fire this skill on the trigger without asking, at the next safe point (nothing running remotely or in a sub-agent). Opening the new chat stays the user's action. Not for the daemon-level restart, memory or database symptoms of the app process itself (that is science-daemon-ops), and not for compacting a single oversized artifact.
---

# Session handoff

A session ends well when the next one starts without re-deriving anything. That
is the whole job: decide when to rotate, write down what a fresh agent cannot
recover on its own, and give the user the message that starts the next chat.

Two things make this worth doing deliberately. Continuity is cheaper than it
looks — artifacts, durable memory, conda environments and credentials all
carry over, and the archived transcript stays searchable — so rotation is not
a loss. But the **live kernel does not carry over**, and that loss is silent:
in-memory dataframes, fitted models, session-scoped `pip install`s, and
anything held only in a variable are gone the moment the session ends. A good
handoff is mostly an inventory of that gap.

## When it runs

**Size fires it automatically, without asking.** The user's profile tells you to
run the size check below on every user turn and after each deliverable. Any one
of these is enough:

| signal (this chat's own record) | trigger |
|---|---|
| messages | 300 or more |
| latest context | 280k tokens or more |
| folds so far | 1 or more |

After a handoff the trigger re-arms only after another 150 messages or a new
fold, so a user who keeps working here is not asked every turn. At 2000
messages or more it is urgent (see Safe point).

The numbers come from the user's own data rather than convention (`references/thresholds.md`, from 324
frames across the user's projects; descriptive, and fold timing varies widely): the share of frames that had folded is 2% below 120 messages, 6.5% at 120-199, 19% at 200-399, 75% at 400-799 and 100% from 800 (root chats alone: 0 of 37, 0 of 5, 3 of 9, 5 of 7 and 9 of 9); the median latest context of frames that had not folded is 280k tokens
at 201-400 messages (3.6x the 78k at 25 messages or fewer); and the next step
costs 1.8-3.3x the first by step 100 (fits to frames that never folded; the range depends on the cache prices assumed). Three hundred messages is the middle of
the 200-399 band, early enough to write the handoff before most chats have
folded. Recorded credits per step show no clear trend with length in root chats
(about +8% per doubling, interval -3% to +20%), so the case for rotating is not
a measured per-step saving: it is that folds become likely (each costs a
summarising call plus a cache re-write), that detail from before a fold reaches
you as summary, and that resuming a large context after a gap of 2 hours or
more re-writes it to the cache.

**Task boundary is the second signal, and it only offers.** At a clear boundary —
a new topic or dataset, an approach abandoned for another, a shift from
exploration to writing up — offer a handoff in one sentence whatever the size.
Do not interrupt iteration on the same objects.

**Safe point.** Fire at the next safe point: the current deliverable is finished
and no remote job or sub-agent is still running, because their completion
notices land in *this* chat, not the next one. If something is in flight, finish
it first, then run the skill. At 2000 messages or more do not wait: hand off now,
and list each job id with the command that polls it, since its notice will not
reach the new chat.

**Sub-agents never run this skill.** Only the user-facing chat does.

Folds are not damage: a fold keeps user messages verbatim and compresses the
rest, and everything archived stays searchable. But each one means earlier
detail now reaches you as summary rather than transcript, so precision about
identifiers, numbers and quoted text degrades — check the archive rather than
trusting recall once folds are non-zero.

Very large chats may also cost responsiveness outside your context, but this is
not established. In one daemon log the user pasted on 2026-08-08, four
transcript reads of about 6,200 rows took 334-791 ms, three of them followed by
a database-lock hold of similar length; a separate 4,142 ms hold came from an
insert into the execution log; and a stall watchdog was armed to respawn the
daemon, with no firing logged. Paged reads of 50 messages took 5-13 ms in chats
of up to 2,240 messages. If the user is *also* reporting app slowness or
restarts, see `science-daemon-ops`.

## The size check

Run this in the `repl` tool on every user turn and after each deliverable. Say
nothing to the user unless it fires. Substitute this chat's frame id (it is in
your context as **Frame ID**). `LAST` is the `(messages, folds)` you recorded when
you last wrote a handoff in this chat; keep it in frame memory
(`write_memory(entity="frame")`) so it survives compaction.

```python
def handoff_trigger(msgs, ctx_used, folds, last_msgs=0, last_folds=0):
    """Automatic size trigger: any of 300 messages, 280k tokens of latest context, 1 fold.

    Returns {'fire': bool, 'reasons': [...], 'urgent': bool}. After a handoff, pass the
    (messages, folds) recorded then; the trigger re-arms only after 150 more messages
    or a new fold. 'urgent' means 2000+ messages: hand off now, even with jobs in flight.
    """
    msgs = msgs or 0
    ctx_used = ctx_used or 0
    folds = folds or 0
    reasons = []
    if msgs >= 300:
        reasons.append("{} messages (trigger 300)".format(msgs))
    if ctx_used >= 280000:
        reasons.append("{}k tokens of latest context (trigger 280k)".format(ctx_used // 1000))
    if folds >= 1:
        reasons.append("{} fold(s) so far".format(folds))
    armed = (not last_msgs) or (msgs - last_msgs >= 150) or (folds > last_folds)
    return {"fire": bool(reasons) and armed, "reasons": reasons, "urgent": msgs >= 2000}


FID = "<this chat's frame id>"
LAST = (0, 0)
q = host.query("""
  SELECT json_extract(context_data,'$._message_count'),
         json_extract(context_data,'$._context_used'),
         COALESCE(json_extract(context_data,'$._compaction_count'), 0),
         (SELECT COUNT(*) FROM execution_log e WHERE e.frame_id = f.id),
         (SELECT COUNT(*) FROM artifacts a WHERE a.root_frame_id = f.id
            AND a.is_ephemeral = 0)
  FROM frames f WHERE f.id = ?
""", [FID])
msgs, ctx, folds, cells, arts = q["rows"][0]
print(dict(msgs=msgs, ctx=ctx, folds=folds, cells=cells, artifacts=arts),
      handoff_trigger(msgs, ctx, folds, *LAST))
```

`fire` false: carry on, silently. `fire` true: run the steps below. `cells` and
`artifacts` size the handoff you are about to write, not the decision.
`ctx` is a snapshot of a sawtooth that each fold resets, which is why folds are
a trigger of their own and why context alone is the noisier signal. The same
`handoff_trigger` loads in the python kernel with the skill, for tests; a test
keeps the two copies identical. If the repl kernel is busy with a background
cell, run the check in a fresh repl cell; it takes about 10 ms.

## When it fires

1. **Confirm the safe point.** The current deliverable is finished and nothing
   is running remotely or in a sub-agent (or the chat is past 2000 messages,
   in which case list each job id and its polling command in State).
2. **Checkpoint expensive kernel state.** `save_artifacts(...,
   checkpoints=[...])` for anything costly to rebuild, with the reload line
   written next to it; note session-scoped `pip install`s by name.
3. **Draft the handoff** from `references/handoff-template.md`, using
   `handoff_artifact_lines(frame_id=...)` and
   `handoff_kernel_inventory(namespace=globals())`. Fill Objective, State,
   Decisions, Dead ends, Next steps and Open questions.
4. **Write the start message last, in two passes.** It needs the handoff's own
   artifact id, which exists only after the first save: save the handoff once,
   take the artifact id from the result, build the message with
   `handoff_start_message(...)`, paste it under "Start message for the next
   chat" in a fenced block, run `handoff_check(text)` until it returns an empty
   list, and save again as a new version of the same artifact.
5. **Record.** Write durable decisions and constraints to memory, and a frame
   memory note `handoff written at msgs=N folds=K` so the re-arm survives
   compaction.
6. **Reply once.** One or two sentences: the numbers that triggered it, the
   handoff in the artifact tray, anything checkpointed. Then end the reply with
   the start message in a fenced block; nothing after it. Never say the chat
   was rotated: the user opens the new one, in this project.

If the user keeps working here, carry on; the trigger re-arms after 150 more
messages or the next fold. Never treat the handoff as finished work in itself.

## The start message for the next chat

The user pastes this as the first message of the next chat, so it has to work
from nothing. Plain text, about 150 words, no artifact markers (a marker or an
@-mention may not survive copy and paste). It carries:

- the instruction to continue from `HANDOFF-<topic>.md`, with the artifact's
  filename and full id, so the new chat finds it by id even without an @-mention;
- the objective and the single next action;
- up to five artifacts to open first, and what the old kernel lost (which
  checkpoints to reload);
- what is pending: jobs or sub-agents with ids, open reviewer findings;
- up to three things not to redo (decisions and dead ends, one line each);
- this chat's frame id, with the instruction to search its archive before
  reusing any identifier, number or quote.

`handoff_start_message(topic, objective, next_action, handoff_artifact_id,
prev_frame_id, open_first=None, lost_state=None, pending=None,
do_not_redo=None)` builds it and raises if it exceeds the word cap, so shorten
the fields rather than the cap: the detail belongs in the handoff file.
`handoff_check_start(text)` flags a missing file name, a missing id, a marker or
local path, and length. A worked example is at the end of
`references/handoff-template.md`.

## Writing the handoff

Save it as `HANDOFF-<topic>.md` and keep it short enough to read in a minute —
a fresh agent needs orientation, not a transcript. Include only what cannot be
recovered by looking at the artifacts, and write for someone competent who has
no memory of the conversation.

Read `references/handoff-template.md` for the section-by-section template and
the reasoning behind each one.

Six helpers load with this skill and do the mechanical parts, so your effort
goes into the judgement the document actually needs:

- `handoff_trigger(msgs, ctx_used, folds, last_msgs, last_folds)` — the size
  trigger, for tests and for the python kernel.
- `handoff_artifact_lines(frame_id=...)` — this session's artifacts as
  ready-to-paste bullets with resolvable ids; you fill in each description.
- `handoff_kernel_inventory(namespace=globals())` — the kernel-state table
  from live variables, with real shapes and sizes; rebuild cost and reload
  path are yours to fill, since nothing can infer them.
- `handoff_start_message(...)` and `handoff_check_start(text)` — the start
  message and its check.
- `handoff_write(topic, body)` — writes `HANDOFF-<topic>.md` to the workspace.
  Saving it as an artifact stays a separate deliberate step.
- `handoff_check(text)` — flags missing sections (the start message is one), a
  start message that would not work from a fresh chat, unresolvable links, and
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
*response* text is fine — it is only code cells that pre-resolve. The start
message avoids the problem by carrying plain ids.

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
tray, and end the reply with the start message. Stay in the same project:
artifacts and memory are project-scoped, so a new chat there finds everything
by itself, while a new chat in a different project has to be pointed at it
explicitly (the start message's artifact id does that).

## Resuming from a handoff

The receiving session inherits more than the document. Artifacts, durable
memory, conda environments and credentials are already there; the archived
transcript of the previous session is searchable. The handoff is orientation —
it tells you which of that inherited material matters and what state was lost.
The first message is usually the start message from the outgoing chat: take its
objective and next action as the brief, and the handoff file as the authority.

Work in this order, because each step can invalidate the next:

1. **Read the handoff first, before touching anything.** The start message
   gives its artifact id; `host.artifact_path(<id>)` resolves it to a file you
   can read. Objective and Next steps tell you what the session is for;
   Decisions and Dead ends stop you re-running work that already failed.
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
   Search the previous chat's archive (its frame id is in the start message)
   before reusing any identifier, number or quote from it.

When the handoff is thin or the objective has moved on since it was written,
ask rather than infer. A handoff is a snapshot of intent at one moment; the
user is the live source. Searching the previous session's archived transcript
is the other recourse — it is retained and searchable, so a detail the handoff
omitted is usually still recoverable rather than lost.
