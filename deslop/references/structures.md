# Structural patterns and their repairs

Each pattern below is a shape, not a phrase. The linter catches some of them; the rest need a read.

## Binary contrast

The shape: negate a plausible reading, then assert the intended one.

> This isn't a limitation, it's a design choice.
> The problem is not the model. It's the data.

Why it fails: the negated half is filler, and the construction implies a strawman nobody proposed. It also flattens genuine uncertainty into false clarity.

Repair: assert the second half and support it.

> We fixed the window length at 26 weeks to keep the training set disjoint from the evaluation period.
> Held-out error tracked label noise (r = 0.71) rather than model capacity (r = 0.08).

## Negative listing

> Not a bug. Not a feature. A tradeoff.

Repair: name the thing once. "The truncation is a tradeoff: we lose 3% of reads to gain a uniform read length."

## Dramatic fragmentation

> Speed. That's it. That's the tradeoff.

Repair: one sentence with a subject and a verb. "The only advantage is speed."

Exception: slides, posters, and talk scripts, where fragments are a legitimate register.

## Self-posed rhetorical question

> What did we find? Instability.
> So why does this matter? Because rankings drive weighting.

Repair: fold the question into the statement.

> Model rankings were unstable across seasons.
> Ranking instability matters because it propagates into the ensemble weights.

Genuine questions are fine when you leave them open (a stated open problem, a hypothesis you did not test). The tell is a question you answer in the next three words.

## Fractal summary

The shape: announce the argument, make it, then restate it, at every level of nesting.

> In this section we show that A implies B. [paragraph showing A implies B] Thus, as shown, A implies B.

Repair: delete the announcement and the restatement. Abstracts and Conclusions summarize; paragraphs do not summarize themselves.

## Listicle disguised as prose

> The first obstacle is coverage. ... The second obstacle is depth. ... The third obstacle is annotation.

Repair: either use a real list (in a README or slide, where lists belong) or write connected prose where each paragraph's claim follows from the last. In a manuscript, prefer the prose.

## Anaphora and tricolon

> We measured. We modelled. We validated.
> The assay is fast, cheap, and reproducible.

Repair: two items, or one with a number. "The assay costs $4 per sample and takes 90 minutes."

Exception: a Methods procedural list is a legitimate tricolon ("fixed, permeabilized, and stained"). The linter flags these as soft; leave them.

## False agency

An inanimate subject performing a human action.

| Slop | Repair |
|---|---|
| Our analysis reveals that X | We found X |
| This work argues that X | We argue X |
| The data tells us X | X (cite the figure) |
| The results invite further study | (name the study) |
| The complaint becomes a fix | The team fixed it |
| This paper seeks to X | We X |

## Passive voice

Convert in the Introduction and Discussion; leave in Methods.

| Section | Example | Verdict |
|---|---|---|
| Methods | Libraries were sequenced on a NovaSeq 6000. | Keep. The instrument is the point. |
| Methods | Samples were randomized by an independent statistician. | Keep, and name the role. |
| Discussion | The effect is believed to arise from crowding. | Fix: "We attribute the effect to crowding" or "Author et al. (year) attribute it to crowding". |
| Intro | It has been shown that X. | Fix: "Author et al. (year) showed X." |

The test: if you cannot name the agent, the sentence is hiding whose claim it is.

## Stacked historical analogy

> Apple didn't build Uber. Facebook didn't build Spotify. Platforms don't build applications.

Repair: cut it. Analogies borrow authority they have not earned. One analogy, if genuinely clarifying, is the maximum.

## Metaphor overrun

Introducing a metaphor is fine. Extending it for three paragraphs, or reviving it in the Conclusion, is not. Use it once, then use the technical vocabulary.

## Invented concept label

> This is the supervision paradox.
> We call this the reproducibility trap.

Coining a term is legitimate when you define it, use it consistently, and it names something new. It is slop when it dresses an ordinary observation as a discovery. If the label appears once and never returns, delete it.

## Rhythm problems

- Three consecutive sentences within about two words of each other reads as metronomic. Break one.
- Every paragraph ending on a short declarative is a strong AI tell. Let some paragraphs end mid-thought, on a qualification, or on a citation.
- Alternating long-short-long-short is its own pattern. Vary the variation.
- Sentence-initial What/When/Where/Which/Who/Why/How as a crutch ("What makes this surprising is that...") usually inverts to a cleaner declarative ("This is surprising because...").

## Em dash

Remove every one. Replacements, in order of preference: a comma for a light aside, parentheses for a genuine aside, a colon for an expansion, a period for two thoughts. The en dash stays only in numeric ranges, and in most journal styles a hyphen or "to" is safer.

## Bold-first bullets

> - **Accuracy**: improved.
> - **Speed**: improved.

Repair in prose: write the sentences. Repair in a list that must stay a list: drop the bold and lead with the content, or keep a bolded lead only where the list is a genuine glossary or parameter table.
