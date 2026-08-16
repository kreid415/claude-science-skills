---
name: deslop
description: Strip AI writing tells from scientific prose. Load whenever drafting, editing, or reviewing a manuscript, abstract, title, cover letter, grant narrative or specific aims, introduction or discussion section, reviewer response, rebuttal letter, figure caption, poster or slide text, talk script, methods write-up, release notes, or repository README. Also load on any request to "deslop", "de-AI", "remove AI patterns", "remove AI tropes", "cut the slop", "make it sound human", or to audit prose for AI tells. Ships a linter (deslop_scan, deslop_report, deslop_score) flagging banned phrases, em dashes, tricolons, bold-first bullets, hedge stacks, vague attribution, passive-voice candidates, and rhythm monotony, while protecting numbers, claim strength, citations, and terms of art. Secondary calibration for general prose such as blog posts, memos, and newsletters.
---

# Deslop: Remove AI Writing Patterns from Scientific Prose

Language models write in a recognizable dialect: hedged, symmetrical, adverb-heavy, padded with transitions that carry no information. Reviewers and editors now recognize it on sight. This skill removes that dialect while leaving the science intact.

Two failure modes matter equally. Prose that reads as machine-generated is the obvious one. Over-correction is the other: stripping hedges an honest claim requires, swapping precise terminology for plain words that mean something slightly different, or flattening the passive voice in a Methods section where the reagent is genuinely the subject of interest. Section 1 is the guard against that.

## Workflow

Apply in order. Do not skip step 4.

1. Draft or receive the prose. If drafting, follow the rules below from the first sentence rather than writing slop and cleaning it afterward.
2. Scan mechanically with `deslop_scan(text_or_path)`, which catches what regex catches reliably: em dashes, banned phrases, arrows, bold-first bullets, tricolons, rhythm monotony. Do not eyeball for these.
3. Revise by hand for what regex cannot catch: false agency, invented concept labels, stakes inflation, fractal summaries, diluted argument.
4. Rescan and score with `deslop_score(deslop_scan(revised))`. Below 35/50, revise again.
5. Report what changed and why, so the author can accept or reject each edit. Never hand back a silent rewrite.

## 1. Preserve the science before touching the style

Hard constraints. A stylistic edit that violates one of these is a defect, not an improvement.

- Never change a number, unit, effect size, p-value, confidence interval, sample size, accession, or identifier.
- Never change the strength of a claim. "Suggests" does not become "shows". "Associated with" does not become "causes". If the original overclaims, flag it separately instead of fixing it under the banner of style.
- Never drop or reattribute a citation. If a sentence carrying a citation gets split, both halves keep what they need.
- Never delete a stated limitation, caveat, or negative result to tighten prose.
- Never rename a defined term, gene, protein, strain, method, or metric to something that reads better. WIS, AUROC, Kd, dN/dS, and delta Ct are the correct words.
- Preserve required boilerplate: ethics, funding, data availability, conflict of interest, consent. Journals check for exact phrasing.
- When unsure whether a phrase is slop or a term of art, leave it and flag it.

## 2. Core rules

### 2.1 Cut filler

Delete throat-clearing openers ("Here's the thing:", "It is important to note that"), emphasis crutches ("Let that sink in.", "And that matters."), business jargon ("navigate the landscape", "leverage", "ecosystem", "unlock", "harness"), and meta-commentary ("In this section we will explore...", and "The rest of this paper is organized as follows" outside venues that require it).

A sentence that survives its own deletion without information loss was filler. Test every transition that way.

See `references/phrases.md`.

### 2.2 Break formulaic structure

Avoid binary contrasts ("Not X. Y.", "This isn't a limitation, it's a design choice"), negative listings ("Not a bug. Not a feature. A tradeoff."), dramatic fragmentation ("Speed. That's it."), self-posed rhetorical questions ("The result? Substantial."), anaphora, and tricolon abuse. State the claim once, in one sentence, in ordinary syntax.

Two items beat three. When a third parallel item appears, ask whether the argument needed it or the cadence wanted it.

See `references/structures.md`.

### 2.3 Eliminate the trope vocabulary

The reliable tells: delve, tapestry, realm, myriad, plethora, pivotal, testament to, underscores, highlights the importance of, sheds light on, serves as, plays a crucial role in, paves the way, opens the door, at the forefront, ever-evolving, growing body of literature, "quietly" and its sibling magic adverbs, and participial after-thoughts that analyze nothing (", highlighting its significance").

Also: invented concept labels ("the supervision paradox") presented as established terms; stakes inflation ("this fundamentally reshapes how we understand X"); patronizing analogies ("think of it as a filing cabinet for genes"); and false vulnerability ("we will be honest, this surprised us").

See `references/tropes.md`.

### 2.4 Active voice with real actors, with a Methods exception

Prefer named actors: we, the specific author, the specific instrument. "The complaint becomes a fix" is false agency; "the team fixed it" is a sentence. "Our analysis reveals" is weaker than "we found".

The exception: in Methods, passive voice is standard and often correct, because the object is what matters. "Libraries were sequenced on a NovaSeq 6000" needs no agent. Do not convert Methods passives to satisfy a rule. Do convert passives in the Introduction and Discussion, where a hidden agent usually hides a hidden claim.

### 2.5 Be specific

No vague declaratives ("The implications are significant", "The reasons are structural"). Name the implication. No lazy extremes (every, always, never) doing vague work. No vague attribution: "researchers have shown", "studies suggest", and "it has long been recognized" all mean the citation was never looked up. Name author and year, or drop the claim.

Domain terminology is not jargon. "Weighted interval score" is the precise name of a thing. The problem is business vocabulary and AI vocabulary leaking into technical prose.

### 2.6 Hedge exactly once

AI prose stacks hedges: "may potentially suggest", "could possibly indicate", "appears to be somewhat associated with". Scientific prose needs one hedge, chosen deliberately, matched to the evidence. Pick the weakest accurate verb and stop: suggests, is consistent with, we observed. Then let it stand without a second qualifier.

Cutting hedges to zero is the opposite error. "Causes" where you measured a correlation is worse than "may potentially be associated with", because it is wrong rather than merely flabby.

### 2.7 Vary rhythm

Mix sentence lengths. If three consecutive sentences land within a couple of words of each other, break one. End paragraphs differently, so that not every one closes on a short declarative. Do not stack fragments for manufactured emphasis. Do not write a listicle disguised as prose ("The first obstacle... The second obstacle...").

No em dashes. Use a comma, a period, a colon, or parentheses.

### 2.8 Trust the reader

The reader is a domain expert. State facts directly. No "Let's break this down", no "Think of it as", no pedagogical voice. No fractal summaries, meaning no announcing what the section will argue, arguing it, then restating what was argued. An abstract summarizes the paper; a paragraph does not need to summarize itself.

### 2.9 Watch formatting tells

No bold-first bullets, meaning every item opening with a bolded keyword. No unicode arrows in prose. No em dashes. No "In conclusion" or "In summary" opening a paragraph. No "Despite these challenges" formula. In a manuscript body prefer prose to bullets entirely: reviewers read paragraphs, and a bulleted Discussion signals a draft that was assembled rather than written.

### 2.10 Do not dilute

One point per paragraph, one argument per section. Do not restate a claim in three registers. Do not extend a metaphor past its second appearance. Do not stack historical analogies for borrowed authority.

## 3. Register by document type

| Document | Register, and which rules bend |
|---|---|
| Manuscript body | Formal. First-person plural for your own work, past tense for what you did, present tense for what is true. Claims carry citations. |
| Abstract | Every sentence load-bearing. No motivation sentence that could preface any paper in the field. Numbers in the results sentence. |
| Methods | Passive voice is correct and stays (2.4). Procedural tricolons stay (2.2). |
| Cover letter, reviewer response | Direct, courteous, specific. Answer the question asked. No flattery of the editor, no defensive throat-clearing, no "we thank the reviewer for this insightful comment" on all twelve points. |
| Grant narrative, specific aims | Consequence stated plainly, once. Significance argued from the gap, not asserted with intensifiers. |
| Slides, posters, talk scripts | Fragments are legitimate, so 2.2's ban on fragmentation relaxes. Trope vocabulary, false agency, and stakes inflation stay banned. |
| Figure captions | What the panel shows, what the elements mean, the n and the test. No interpretation verbs (demonstrating, revealing) unless the journal's style expects them. |
| README, release notes | Second person and imperative are fine. Trope vocabulary is not. |
| General prose (blog, memo, newsletter) | Secondary calibration. Put the reader in the room, "you" over "one" or "people", concrete over abstract. All of section 2 applies; section 1 applies wherever a factual claim appears. |

## 4. Linter

`kernel.py` loads with this skill. Three functions:

```python
scan = deslop_scan("draft.md")          # path or raw string
print(deslop_report(scan))              # markdown findings table
deslop_score(scan)                      # 5 dimensions, 1-10 each, plus total
```

`deslop_scan` returns `n_words`, `n_sentences`, `findings` (each with `category`, `severity`, `label`, `line`, `excerpt`), `rhythm`, and `counts`. Severity is `hard` (essentially always slop) or `soft` (context-dependent, and common in legitimate scientific prose). Read every `hard` finding. Triage `soft` findings against section 1 before acting.

Arguments: `skip_code=True` (default) drops fenced code blocks, inline code, and LaTeX math; `only="hard"` filters the findings list.

Save the report beside the revision so the author can audit the edits:

```python
scan = deslop_scan("draft.md")
open("deslop_report.md", "w").write(deslop_report(scan, include_score=True))
```

The linter is a net, not a judge. It cannot see invented concept labels, stakes inflation, fractal summary, or dilution, and it will flag terms of art that belong in the paper. A clean scan is not a good draft.

Scanning this skill or its reference files produces dozens of hard findings, because they quote the patterns they ban. That is expected. Do not "fix" the catalogs.

## 5. Quick checks

Run these on any prose before delivering it.

1. Em dash anywhere? Remove it.
2. Any "here's what/this/that" or "it's worth noting"? Cut to the point.
3. Any "not X, it's Y" contrast? State Y.
4. Any self-posed question answered immediately? Fold it into a statement.
5. Three consecutive sentences of the same length? Break one.
6. Every paragraph ending on a punchy one-liner? Vary.
7. Vague declarative ("the implications are significant")? Name the implication.
8. Vague attribution ("studies have shown")? Cite or cut.
9. Two or more hedges in one clause? Keep the accurate one.
10. Inanimate subject performing a human verb? Name the actor.
11. Passive voice outside Methods? Find the agent.
12. Adverb doing the work a verb should do? Replace the verb.
13. Tricolon? Two items or one.
14. Bold-first bullets, arrows, "In conclusion", "Despite these challenges"? Remove.
15. Same metaphor more than twice? Cut the repeats.
16. Sentence opening with What/When/Which/Why/How as a crutch? Restructure.
17. Any number, hedge, or citation changed while editing? Revert it.

## 6. Scoring

Rate 1-10 per dimension. `deslop_score` gives a heuristic estimate; your own read overrides it.

| Dimension | Question |
|-----------|----------|
| Directness | Statements, or announcements of statements? |
| Rhythm | Varied, or metronomic? |
| Trust | Does it respect a specialist reader? |
| Authenticity | Does it read as a specific person's writing? |
| Density | Is anything cuttable without information loss? |

Below 35/50: revise. Below 25/50: rewrite the section rather than editing it.

## 7. Example

Before:

> It's worth noting that these findings have important implications for how we navigate the challenges of forecast ensembling moving forward. Despite these challenges, this work contributes meaningfully to the growing body of literature, highlighting the need for continued evaluation.

After:

> If model rankings are unstable across geography and time, performance-weighted ensembles may not beat equal weighting. We could not test this directly, because our evaluation covers one season.

Removed: filler transition, "navigate the challenges", "moving forward", "despite these challenges", "growing body of literature", and a participial after-thought. Added: the specific implication, and the specific limitation the vague version was hiding.

## 8. References

- `references/phrases.md`: phrases to cut or replace, with scientific-prose substitutes.
- `references/structures.md`: structural patterns and their repairs.
- `references/tropes.md`: the trope catalog, including science-specific tells.
- `references/examples.md`: before and after transformations by document type.
