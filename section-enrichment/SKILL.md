---
name: section-enrichment
description: Enrich an ALREADY-DRAFTED prose section of a technical book or paper in place, one subsection/subsubsection at a time, discussing proposed changes before applying them. For each unit: run a correctness-review pass that surfaces technical errors (wrong signs, misstated definitions, imprecise claims) AND typos for approval before rewriting; add citations verified by identifier; convert markdown tables to LaTeX; add minimally-viable teaching code snippets (mirrored in a notebook); finish or add real figures (TikZ diagrams, matplotlib data plots) replacing framed placeholders; add algorithm/pseudocode where relevant; convert bullet (itemize) lists into flowing prose in the book's house style; then compile-and-render to verify. Distinct from textbook-chapter-expansion, which builds NEW scaffolds from stubs. Use when asked to improve, polish, or enrich existing written prose rather than flesh out an empty outline. Built for the "AI for Statistics" book (Reid & Caffo) but reusable for any technical manuscript.
---

# Section Enrichment

Take a section that is ALREADY WRITTEN (real prose, equations, maybe stub bullets and `\todo`s)
and raise it to publication quality **in place**, without rewriting the author's voice. This is the
opposite end of the pipeline from `textbook-chapter-expansion`: that skill turns an empty stub into a
fresh scaffold; this one polishes existing prose.

## The two hard rules

1. **One unit at a time.** Work a single `\subsection` or `\subsubsection` per pass. Never edit ahead
   of the unit under discussion.
2. **Propose before applying.** For each unit, present the correctness findings and the proposed
   LaTeX to the user and get approval BEFORE writing to the file. The user drives sequencing.

## Per-unit workflow

### 1. Correctness-review pass (surface, do not silently fix)
Read the unit and enumerate, as a numbered list for approval:
- **Technical errors** — wrong signs, reversed inequalities, misstated definitions, symbol misuse
  (e.g. encoder written as $p_\theta(z\mid x)$ when it is the approximate posterior $q_\phi(z\mid x)$),
  claims that are false as written. These are the high-value finds; state the correct version and why.
- **Imprecise claims** — technically-defensible-but-loose statements to tighten.
- **Typos** — collect them but list them last; they are the least important finding.
**Integrity:** every substantive change you intend to make must appear in the numbered list. If your
proposed replacement text quietly alters a claim, that alteration is a review item — surface it, do
not bury it in the diff. (A fresh-context reviewer WILL diff your proposal against the original.)

### 2. Enrichment (only after approval)
Apply the locked conventions below:
- **Citations** — add `\citep/\citet` hooks for every currently-uncited claim that needs support.
  VERIFY each new key by identifier before writing it (see "References" below). Reuse keys already in
  the project `.bib` where they exist; only add genuinely new ones to the fragment.
- **Markdown -> LaTeX tables** — convert any markdown table to a captioned, labeled `table[t]` float
  using `tabularx` + `booktabs`. Wide concept-map tables (field-A term / field-B analogue / same /
  different) get `>{\hsize=..\hsize}X` columns so text wraps.
- **Minimally-viable code** — add a short `minted{python}` snippet that makes ONE idea concrete
  (a closed-form check, a variance comparison, a toy fit). Keep teaching snippets inline in the `.tex`;
  put the fuller runnable version in the companion `.ipynb`. RUN the snippet first and copy its real
  output into the comment — never write output numbers from memory. Use `[fontsize=\small,breaklines]`
  so long lines wrap inside the margin.
- **Figures** — replace framed `\fbox` placeholders with REAL figures: TikZ for graphical
  models / computation graphs / diagrams, matplotlib (saved `\includegraphics`) for data plots
  (latent spaces, samples, curves). Build the figure standalone, render it, and eyeball it for label
  collisions BEFORE embedding — plate/box labels overlapping borders is the usual defect; add edge
  labels as named nodes to the `\fit` list so the box encloses them.
- **Pseudocode** — add an `algorithm` + `algpseudocode` block for any training loop / iterative
  procedure, cross-referencing the equations it implements (`\eqref`).
- **Bullet lists -> prose** — this book's house style is flowing prose, not bullets. Convert EVERY
  `itemize` block in the unit into connected paragraphs: keep each item's content and citation, and
  add the connective tissue that says how the points relate (contrast, sequence, cause). A leading
  `\textbf{Term}` per item can be kept inline as a bolded lead-in when the list enumerates named
  variants (e.g. DDIM / classifier-free guidance / latent diffusion), but the surrounding structure
  becomes sentences. This applies to `itemize` only — a genuine `enumerate` problem set (Exercises)
  and `algorithm`/`algpseudocode` blocks stay as lists. Do it as part of the enrichment pass unless
  the author says otherwise.

### 3. Compile-and-render verification (every unit, no exceptions)
Wrap the file in the book's preamble (or the shim below) and compile with `tectonic`, then render the
changed pages to PNG and LOOK at them. Fix overfull boxes, label collisions, and broken refs before
saving. The helper `enrich_compile_check(tex_path, ...)` (from this skill's `kernel.py`) does the
wrap+compile+render in one call.

### 4. Save
`save_artifacts` the updated `.tex` with `version_of` the section artifact, and the `.bib` fragment
with `version_of` its artifact. State which units are done and what is next.

## References (reuse, don't reimplement)
Verification uses the helpers from `textbook-chapter-expansion` (`verify_id`, `verify_search`,
`to_bibtex`, `merge_bib_files`). Load that skill to get them:
`skill({skill: "textbook-chapter-expansion"})`. **Verify seminal papers by identifier**
(`verify_id(key, title, arxiv=... | doi=...)`) — Crossref fuzzy search returns spurious reprint DOIs
for famous papers. Watch for **mangled title casing** from Crossref (e.g. "EM" -> "Em"); fix casing and
`{}`-protect acronyms manually, and set the correct entry type (`@incollection` for book chapters).
Keep the fragment's header-comment counts truthful; never fabricate a DOI/arXiv id.

## Compile shim + preamble packages
`tectonic` with `minted` needs `-Z shell-escape`. Standalone-compile shim: `\documentclass{book}` +
`\providecommand` the `\cite/\citep/\citet` family + a no-op `\todo`. Enrichment commonly introduces
packages beyond a minimal preamble — when you use them, CHECK they are in the book's `main.tex` and
FLAG any that are missing to the author:
- `\usepackage{tabularx, booktabs, array}` (tables)
- `\usepackage{algorithm}` + `\usepackage{algpseudocode}` (pseudocode)
- `\usetikzlibrary{arrows.meta, positioning, fit, backgrounds}` (figures)
Prefer figures that avoid extra tikz libraries (e.g. name edge-label nodes instead of the `calc`
`$(a)!0.5!(b)$` midpoint syntax) so the figure needs nothing the book does not already load.

## Notes
- **Artifact-marker compile gotcha.** `save_artifacts` rewrites `\includegraphics{fig.pdf}` in the
  STORED `.tex` into an `{{artifact:art_<id>}}` marker (correct — the rendered artifact tracks the
  figure's latest version). `tectonic` cannot resolve that marker, so a local compile must first map
  markers back to workspace files. `enrich_compile_check(..., artifact_map={"<artifact_id>":
  "fig.pdf"})` does this; unmapped markers fall back to `host.artifact_path(<id>)`.
- Keep the author's first-person voice and notation; enrich, don't rewrite.
- Cache-read tokens dominate cost on long books — after the pattern is locked, a fresh session per
  section is cheaper than one long thread.
