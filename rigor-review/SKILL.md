---
name: rigor-review
description: "Project-long scientific rigor review of each research output (figure, table, analysis script, results file, methods or results draft). Flags issues likely to draw reviewer pushback (weak or untuned baselines, leakage, pseudoreplication, double dipping, missing variance, multiplicity, invalid tests, metric-claim mismatch, overclaiming), recommends more rigorous approaches grounded in retrieved current literature, and runs a tiered prior-work search to catch methods that are already published. Keeps a running rigor ledger artifact so open issues are re-checked and searches are reused across the project. Use whenever the user asks to review, sanity-check, stress-test, or critique an output or method for rigor, novelty, or reviewer readiness, asks whether something has been done before, or finishes a deliverable in an ongoing project, even if they do not say rigor. For a formal referee report on a finished manuscript use peer-review instead."
---

# Rigor review

Review each output as a skeptical methods reviewer would, early enough that problems can still be fixed. Three jobs:

1. **Flag pushback.** Concrete issues in this output that a reviewer would raise, each tied to evidence in the output or the code that produced it.
2. **Recommend the rigorous alternative,** grounded in literature you actually retrieved, preferring current guidance.
3. **Check prior work,** so the project does not rebuild something that already exists.

Findings accumulate in a **rigor ledger** (`rigor_ledger.json` plus a rendered `rigor_ledger.md`, saved as project artifacts).
Each review starts from the ledger. Open issues are re-checked, prior searches are reused, and the ledger becomes the
pre-submission checklist.

This skill reviews; it does not fix. Do not edit the user's code, results, or prose, and do not re-derive results. Recommend
changes and let the user decide. When something cannot be determined from what you have, log it as a `question`, not an issue.

## Helpers (loaded with this skill, in the `python` kernel)

| Function | Purpose |
|---|---|
| `rigor_ledger_load()` → `(ledger, artifact_id)` | newest `rigor_ledger.json` in the project, or `(None, None)` |
| `rigor_ledger_new(project)` | empty ledger |
| `rigor_log_review(L, output, version_id, scope)` → `V-###` | record that a review happened |
| `rigor_add_finding(L, review_id, category, severity, issue, evidence, pushback, recommendation, literature, confidence)` → `R-###` | add a finding |
| `rigor_set_status(L, fid, status, note, review_id)` | open → resolved / disputed / wontfix, with history |
| `rigor_upsert_method(L, name, card, search)` | method card plus prior-work search record |
| `rigor_save(L)` | writes `rigor_ledger.json` + `rigor_ledger.md` |
| `novelty_search(queries, since_year, per_query)` | OpenAlex + arXiv + Europe PMC (PubMed, bioRxiv/medRxiv); `df.attrs["coverage"]` |
| `rate_overlap(card, hits, top_n)` | LLM triage of abstracts against the method card |
| `citation_neighbors(openalex_id)` | backward and forward citations for escalation |

Constants: `SEVERITIES = (fatal, major, minor)`, `CATEGORIES = (design, baselines, leakage, statistics, metrics, data,
claims, reporting, reproducibility, novelty)`. OpenAlex needs the stored OpenAlex credential declared on the cell
(`credentials=["OpenAlex"]`). Without it OpenAlex is skipped and the coverage record says so; never call it keyless.

Reference files are read on demand from the `repl` tool with `host.skills.read("rigor-review", "references/<file>")`:
- `ml-benchmarking.md`: baselines, tuning parity, seeds and variance, comparison tests, leakage, metric validity, disentanglement.
- `compbio.md`: pseudoreplication, double dipping, batch confounding, simulation realism, embedding over-interpretation, benchmarking.
- `statistics.md`: multiplicity, forking paths, effect sizes, power, test choice, causal language.
- `novelty-check.md`: the tiered prior-work protocol and verdict definitions. Read it before the first prior-work check in a session.

Each catalog lists seed sources that were checked against Crossref/arXiv/JMLR. Treat them as starting points, not a
substitute for searching for newer guidance.

## Workflow

### 1. Load context
- `L, ledger_aid = rigor_ledger_load()`. If `None`, ask once whether to start a ledger for this project, then `rigor_ledger_new(...)`.
- Identify the output: artifact filename and version id. Read it. For figures, tables, and results, also read the code that
  produced it (`host.lineage[vid]["code"]`, or the committed script). Leakage, seed handling, split logic, and test choice live
  in code, not in the figure.
- Write down the claims this output supports, or will be used to support, in one line each. Review against those claims.
  A figure is only "wrong" relative to what it is meant to show. If the intended claim is unclear, ask.
- `review_id = rigor_log_review(L, output, vid, scope)`.

### 2. Re-check open findings
For each open finding whose topic this output touches: resolved (cite the evidence, e.g. "5 seeds now, fig3 v4 error bars"),
still open, or changed severity. Use `rigor_set_status`. Do not mark anything resolved without evidence in the current output or code.

### 3. Rigor pass
Read the relevant catalog(s) for the domain. Go through the output against them and against the claims from step 1.
For each issue record:
- **issue**: what is wrong, specifically.
- **evidence**: where it is (file, line or cell, panel, table row). An issue with no location is generic advice; leave it out.
- **pushback**: the reviewer's likely objection in one sentence, and the type of reviewer who would raise it (methods, statistics, domain).
- **severity**: `fatal` if it invalidates a main claim; `major` if a reviewer would require it for acceptance; `minor` if it is
  worth fixing but would not block acceptance.
- **recommendation**: the concrete, more rigorous alternative, with enough detail to act on.
- **confidence**: `verified` if seen in the output or code; `question` if the output does not show enough (e.g. "split logic not visible").

Prioritize. Three well-evidenced major issues are worth more than fifteen minor ones. Do not pad with generic best-practice reminders.
Note what the output already does well when it closes a likely objection. That tells the user what not to change.

### 4. Ground recommendations in literature
For each fatal or major finding, support the recommendation with retrieved sources:
- Start from the catalog seeds, then search for newer guidance (`novelty_search([...], since_year=<current year − 3>)` works
  for this too, or the `paper-lookup` skill). Prefer field guidelines, neutral benchmarks, and methodological papers over
  single applications.
- Cite only what you retrieved. Record `{"doi", "title", "year", "checked": "metadata" | "abstract" | "fulltext"}`. If a
  recommendation rests on a specific claim inside a paper, read that passage (`fetch_article_fulltext`) and mark `fulltext`.
- If current literature disagrees, say so and give both positions. Do not present one side as settled.

### 5. Prior-work check (tiered; protocol in `references/novelty-check.md`)
Run when the output introduces or changes a method, metric, analysis design, or claimed contribution, or contains "first"/"novel" language.
- Build or update the method card (`rigor_upsert_method`). Reuse the ledger's earlier searches; re-run only if the card changed or the last search is stale.
- **Quick tier:** 3–5 queries across vocabularies → `novelty_search` → `rate_overlap` → read the flagged abstracts yourself.
- **Escalate** (full text of the closest 3–5 works, citation-graph expansion) when a hit looks like `partial`/`substantial`
  overlap after your reading, when the card is new, or when the output claims novelty.
- Record the search with queries, sources, number screened, closest works, verdict, and the coverage dict. Overlap findings
  go in as `category="novelty"`.
- Report verdicts as scoped statements, e.g. "no match in OpenAlex/arXiv/Europe PMC for 4 queries, 120 screened, 5 read in
  full". Never write "novel" or "not published" without the scope.

### 6. Save and report
- Write a review note `rigor_review_<output-stem>_<V-id>.md`: claims reviewed, new findings (severity-ordered), status
  changes, prior-work verdict with scope, and what was checked (files, code, sources, full texts).
- `rigor_save(L)`, then save `rigor_ledger.json` and `rigor_ledger.md` with `version_of` set to the existing artifact ids
  (from `ledger_aid` and the matching `.md` artifact) so the ledger stays one artifact with versions. Save the review note as a new artifact.
- In chat, keep it short: a severity-ordered table of new or changed findings (ID, severity, issue, recommendation), the
  prior-work verdict with scope, and the count of items still open. Link the note and the ledger.

## Modes
- **Single output** (default): steps 1–6 for one artifact.
- **Method/plan review:** before an experiment runs, review the plan or method description. Steps 3–5 apply; there is no code yet, so most findings are design findings. This is when the prior-work check is most valuable.
- **Pre-submission sweep:** load the ledger, re-check every open item against the current outputs, re-run the quick tier for every method card restricted to work published since its last search, and produce a checklist ordered by severity. Read `statistics.md` §2 against the ledger history (analysis changes across reviews).
- **Manuscript-level referee report:** use `peer-review` (or `academic-paper-reviewer`) and add this ledger as input.

## Related skills
`scientific-critical-thinking` (bias and evidence-grading frameworks), `paper-lookup` (more databases), `literature-review`
(synthesis and citation grounding), `reproducible` (trace and re-run audit; rigor-review does not duplicate it, but flag
untraceable numbers under `reproducibility`).
