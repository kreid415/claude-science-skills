---
name: paper-outline
description: "Maintain a living, citation-backed outline of the paper a project is building toward, kept in the project's git repo and updated as the work changes. The outline is laid out like an academic paper: main text (Abstract, Introduction with research questions, Methods, Results with experiments, Discussion), then References generated from verified BibTeX, then Supplementary material holding low-level detail (method math, dataset specifics, experiment settings) that the main text points to as [S-ID]. The supplement may only cite works already cited in the main text. Unverified leads are flagged (@?key), an evidence log records what each source shows and where it was checked, and a separate CHANGELOG.md records every change and why. Use at project start to create the outline, and whenever an experiment changes status, a method or parameter changes, a dataset or reference is adopted, or a result confirms or contradicts a hypothesis. Also use when asked for the paper outline or methodology record."
---

# Paper outline

One living outline per project, laid out like the paper it will become. It records **what the paper will argue
and why each step is justified**. It is not the paper: one claim per line, grouped cite keys, no narration of what
a source says.

## Files (in the project repo, under `paper/`)

| file | holds |
|---|---|
| `outline.md` | the outline document: main text, then References, then Supplementary material |
| `CHANGELOG.md` | dated entries, newest first: what changed in the outline and why |
| `refs.bib` | every cited key; each entry stamped `verified = {YYYY-MM-DD via doi\|pmid\|arxiv\|url}` |
| `evidence.md` | one line per key: `- @key — what it shows — checked: <DOI + section/page/figure>` |

`refs.bib` is the same file the `reproducible` scaffold's `paper/main.tex` uses. Experiment IDs `E01…` match
`experiments/01_name/`.

## Document structure of `outline.md` (fixed order; helpers parse it)

```
# <working title>
- target venue / thesis / last updated
## Abstract                problem · approach · main result · implication (one line each)
## 1 Introduction          background claims, gaps (one per line, cited); ### RQ blocks
## 2 Methods               ### M blocks
## 3 Results               ### E blocks
## 4 Discussion            interpretation, limitations, threats to validity, open issues
## References              GENERATED from main-text cites by outline_render_refs()
## Supplementary material  ### S-M / S-D / S-E blocks
```

- **Main text = the main outline.** It holds the points a reader needs to follow the argument. Details needed to
  reproduce the work or check the math go in the supplement, and the main text points to them:
  - `S-M*`: equations, derivations, assumptions;
  - `S-D*`: dataset source/version/size/preprocessing/caveats;
  - `S-E*`: hyperparameters, seeds, hardware, exact metric definitions.

  Supplement blocks may use LaTeX math (`$…$`).
- Blocks: `### RQ1 — question`, `### M1 — method`, `### E01 — name`, `### S-M1 — title`. Fields are `- key: value`
  lines directly under the heading.
  - RQ: `hypothesis`, `status` (open | supported | refuted | revised).
  - M: `used in`, `params`, `rationale` (with cites), `details: [S-M1]`.
  - E: `addresses` (RQ IDs), `status` (planned | running | done | dropped), `design`, `data: <name> [S-D1]`,
    `methods` (M IDs), `result` (repo-relative path or `artifact:VERSION_ID`), `finding`, `details: [S-E01]`.
- **Citations:** `(@key1; @key2)` must be in `refs.bib` and `evidence.md`. `(@?key)` marks an unverified lead that
  must never reach the manuscript.
- **References** lists the main-text citations only, so don't edit it by hand. Unverified leads appear under it in
  a separate subsection.
- **Supplement citations must also appear in the main text** (paper convention: one reference list). If a
  supplement detail needs a source the main text doesn't cite, either cite it at the main-text point that points
  to that block, or drop it.
- Every `[S-ID]` pointer needs a matching supplement block, and every supplement block must be pointed to from the
  main text.
- Dropped experiments and refuted hypotheses stay in the outline with their status. The reason goes in
  `CHANGELOG.md`.

## Helpers (`kernel.py`, auto-loaded)

- `outline_init(root, title, venue, thesis)`: creates the four files (never overwrites).
- `outline_check(root)`: returns the consistency report.
- `outline_render_refs(root)`: rebuilds the References section.
- `outline_log(root, msg, commit=True)`: adds a CHANGELOG entry, bumps `last updated`, and git-commits the four files.
- `outline_fetch_bibtex(doi, key)`: fetches BibTeX from Crossref and stamps it verified.
- `outline_add_bib(root, bibtex)`: appends an entry to `refs.bib`.
- `outline_parse(root)`: programmatic read.

## Workflow

1. **Locate the repo root.** Use the project's git repo: a granted host path, or the repo built by the
   `reproducible` skill. Check project memory for it. If there is no repo, scaffold one with `reproducible` first.
   If `paper/outline.md` exists, read it and the top of `CHANGELOG.md` before changing anything.
2. **Create** (project start): `outline_init(...)`, then fill the Abstract lines, RQs, planned experiments and
   methods from what the user has stated and what the project's artifacts show. Put details in the supplement.
3. **Update on triggers.** Apply edits directly (don't ask first), then report the diff in the reply. Triggers:
   - experiment status change: set `status`, `result`, `finding`; update its `S-E` block; revisit the Abstract's
     main-result line;
   - method or parameter change: update `M`, its `S-M` block, and the `rationale` cites;
   - new dataset: add an `S-D` block and point to it from `data:`;
   - result vs hypothesis: update RQ `status`, then add to Discussion if something is unexplained;
   - reference adopted: verify it first (step 4).
4. **Verify citations against the primary source** before writing `@key` (not `@?key`): resolve the DOI/PMID
   (`outline_fetch_bibtex`, or the `paper-lookup` / `citation-management` skills) and read the relevant passage
   (`fetch_article_fulltext`). Then add the `evidence.md` line saying what was checked. A key whose claim you could
   not confirm stays `@?key`. Never cite from memory.
5. **Close every update**, in this order:
   1. `outline_render_refs(root)`.
   2. `outline_check(root)`: fix everything in `structure`, `missing_from_bib`, `bib_unverified` and
      `missing_from_evidence`; report `unverified_leads` and `bib_uncited`.
   3. `outline_log(root, "<what changed and why>")`.
   4. Copy `outline.md`, `CHANGELOG.md`, `refs.bib`, `evidence.md` to the workspace as `paper_outline.md`,
      `paper_changelog.md`, `paper_refs.bib`, `paper_evidence.md` and save them as artifacts. Find prior versions
      with `host.artifacts(filename=..., exact=True)` and pass `version_of` so each file keeps one history.
6. **Report**: the changelog line, the commit hash, and the `outline_check` summary (open leads, any issues left
   on purpose).

## Rules

- Values in the outline (n, effect sizes, accessions, parameters) are read from the result artifact or code. Never
  retype them from prose.
- Changelog entries say what changed **and why**, and cite the `lab-notebook` entry that prompted the change
  when one exists (e.g. "E02 dropped: batch confound in S-D1; replaced by E02b (NB-20261002-03)").
- If the outline and `EXPERIMENTS.md`/the code disagree, fix the outline to match the evidence and log it, or flag
  the conflict to the user. Don't silently pick one.
- Drafting prose from the outline is a different task: use `scientific-writing` / `academic-paper`, which take the
  outline as input.
