# Where the automatic trigger comes from

The trigger in `SKILL.md` (300 messages, 280k tokens of latest context, or the first fold;
re-arm after 150 more messages or another fold; urgent at 2000 messages) was set from the
user's own usage database, not from convention. Source: the chat-length analysis of
2026-10-07, 324 frames with message counts across 9 projects (67 root chats, 257 sub-agent
frames). The numbers below are from the revised report (version 5, written after three independent reviews; per-step statements and idle-gap claims of earlier versions were corrected). Artifacts in this project:
`chat_length_report.md` (artifact id 67b2e962-1444-4d9a-a938-908afaaf3056), `review_response.md`
(artifact id a734d9b3-99cb-4fb9-8c11-8965aa6d108b) and the re-runnable bundle
`chat-length-analysis.tar.gz` (artifact id 9b430665-078b-4847-9f19-f3b460148012; newest version). Re-derive the thresholds
by re-running its pull and analysis scripts on a fresh snapshot; the fold-rate table is
`results/tables/fold_rate.csv`. The supporting figure is `session_handoff_thresholds.png`
(artifact id eb047a06-6d78-4098-be40-fee1d5b585f9): panel a is the share of frames that had folded
by message-count bin, panel b the latest context against message count with the two triggers
drawn in.

## What the data show

| messages | share of frames that had folded | root chats folded | median latest context, never folded |
|---|---|---|---|
| under 120 | 1.8% (3/168) | 0/37 | 78k at 25 or fewer, 96k at 26-50, 158k at 51-100 |
| 120-199 | 6.5% (4/62) | 0/5 | 217k at 101-200 |
| 200-399 | 19.0% (12/63) | 3/9 | 280k at 201-400 |
| 400-799 | 75.0% (12/16) | 5/7 | 354k above 400 (n = 4) |
| 800 or more | 100% (15/15) | 9/9 | not applicable: every chat has folded |

- Each message adds about 0.87k tokens to the context (root chats that never folded, n = 48), on
  top of a fixed prefix of roughly 48-52k tokens re-read at every step. These fits describe
  frames that never folded, so they are lower bounds for frames in general.
- API-equivalent cost: the median price-weighted cost per step at 201-400 messages is 1.53x (all
  models) and 1.19x (Opus-class) that at 25 or fewer at the default weights, and 1.17-1.53x and
  0.74-1.19x across six weight combinations; every Opus-class interval includes 1, so a rise is
  not robust. The next step costs 1.8-3.3x the first by step 100 (about message 200). After
  folding the average sits at 39-44k price-weighted tokens per step.
- Recorded credits per step in root chats that never folded: medians 40, 58, 57, 62 and 58 from
  25 or fewer to 201-400 messages; the 201-400 over 25-or-fewer ratio is 1.46 (95% CI 0.57-3.63),
  and the regression gives +8% per doubling of length (-3% to +20%), +12% (+4% to +20%) after
  adjusting for model, effort and month. Sub-agent frames fall (-10% per doubling, from 9 root
  chats), which produces the pooled decline. Frames that had folded run 1.37x the credits per
  step of never-folded frames at 201-400 messages (1.11-1.71); the cause is not identified.
- A rolling fold costs about 484 credits for the fold call alone ($0.57 API-equivalent). The
  cache re-write that follows is billed in the main loop, so the 4.0% of recorded credits (2.0%
  of cost) in the fold classes is a lower bound; an exploratory regression puts the induced
  re-write at about 275k tokens per fold (95% CI 175-334k), 1.5-3.8 times the call.
- Idle gaps (between consecutive recorded cells): a small re-write after 2-10 minutes (0.03-0.04 of the context, distinguishable from zero) and none distinguishable from zero after 10-60 minutes. After gaps of 2 hours or more about 0.7-1.2 of the average context is re-written (2-6 h 0.84, CI 0.00-1.76; over 6 h 0.69, CI 0.14-1.20, with fold events in the model). A lifetime between 1 h and 2 h cannot be
  resolved (319 gaps in 25 frames). Anthropic's documentation, as quoted by secondary sources,
  describes a 5-minute default lifetime and an optional 1-hour lifetime; the platform's setting
  is not recorded.
- 25 of 67 root chats reached 200 messages and 16 reached 400. Nine root chats with 800 or
  more messages account for 92.8% of API-equivalent cost and 90.7% of credits.
- The previous version of this skill, calibrated on earlier sessions (sample size not
  recorded), described the same shape: 0% folded below 120 messages, about 10-25% from 120
  to 400, 67% at 400-800, 100% above 800.
- The context-size signal is the noisier one: it is a snapshot of a sawtooth that every fold
  resets, and the median never-folded chat of 201-400 messages sits at 280k. In the snapshot,
  all 20 root chats with 300 or more messages had also reached the context or fold trigger,
  so the message trigger acts as a floor rather than the usual first signal. Replayed on the
  snapshot's final states, the trigger would have fired in 23 of 67 root chats (34%): the 20
  above, plus 3 chats under 300 messages that crossed the context or fold trigger earlier.

## Why these numbers

- **300 messages** is the middle of the 200-399 band, where the share of frames that had folded rises from 6.5% (120-199 messages) to 19% (33% of root chats) on the way to 75% at 400-799,
  and where the next step already costs about 2.4-3.3x the first (step 100 is about message
  200, since a step is two messages). It leaves margin to write the handoff before the band
  in which most chats have folded.
- **280k tokens** is the median latest context of the 201-400 message group; it catches chats
  whose tool outputs make the context large before the message count is.
- **First fold** is the marker that earlier transcript now reaches the agent as summary, so
  precision about identifiers, numbers and quotes has started to degrade.
- **The trigger is a design choice, not a derived optimum.** Fold timing does not follow message
  count (the smallest folded frame has 99 messages, the largest never-folded one 483), and
  intervals that resample whole root chats are wide (for example 38-100% folded at 400-799 messages), so the three signals are used together and the message count acts as a floor.
- **150 messages to re-arm** is a design choice, not a measured value: long enough that a user
  who keeps working here is not prompted every turn, short enough to prompt again before the
  next step up in fold rate.
- **2000 messages, urgent**: carried over from the previous version of this skill, which
  already treated 2000 as 'rotate now'; it is a design choice, not a measured threshold. The log
  you pasted on 2026-08-08 shows four transcript reads of about 6,200 rows taking 334-791 ms,
  three followed by a database-lock hold of similar length, a separate 4,142 ms hold from an
  insert into the execution log, and a stall watchdog armed to respawn with no firing logged;
  the chat's explanation (large session, lock stalls, watchdog respawn) is a hypothesis the log
  does not demonstrate. None of this was re-measured on 2026-10-07; a 50-message page read took
  5-13 ms in chats of 22-2,240 messages.

## Caveats

- Cross-sectional: chats of different lengths are different tasks, models and efforts.
- Rotating does not demonstrably save credits per step: root-chat medians are flat and the
  regression slope is +8% to +12% per doubling with wide intervals, and a new chat re-reads a
  fresh prefix of 48-52k tokens at every step. Do not claim a higher credits-per-step cost for short
  Opus-class chats: that group mixes 9 root chats with 6 sub-agent frames. The case for
  rotating is folds (a summarising call, a cache re-write, and detail kept only as summary),
  and resuming a large context after a gap of 2 hours or more.
- Whether the recorded credits are what the plan quota meters is unverified.

## Provenance

- Figure: `session_handoff_thresholds.png` (artifact id eb047a06-6d78-4098-be40-fee1d5b585f9),
  plotted values in `session_handoff_thresholds_table.csv` (artifact id
  ae4ff745-b0c5-40db-abd2-bca166e28691), script `make_thresholds_figure.py` (artifact id
  9c7a0ee5-5582-4243-bce8-76933d42efb4).
- Tests for this skill: `session-handoff_tests.py` (artifact id bd429ffb-638f-4fd5-9164-af870e042842);
  run with `SKILL_DIR=<folder holding SKILL.md> python -m pytest -q session-handoff_tests.py`.
- Previous version of this skill (task boundary first, no automatic trigger), for rollback:
  `session-handoff_skill_before_auto-trigger.tar.gz` (artifact id
  65fc77e4-6e64-4ace-8292-d80155a1ae42).
