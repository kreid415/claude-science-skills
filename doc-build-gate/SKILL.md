---
name: doc-build-gate
description: "Gate every LaTeX, markdown/pandoc, HTML or slide deliverable before it is called built, compiled, rendered, verified or done. Load when writing or editing a .tex/.md/.qmd/.html paper, outline, review, report or deck; when embedding figures; when the user says compile, build, render, Overleaf, pandoc, PDF, 'figures missing', blank figure, overlapping legend or p-values, text cut off, duplicate figures, undefined reference/citation, or asks 'did it build / did you check'. Builds the exact saved file and fails on chat artifact markers or sandbox paths in sources, any TeX warning class, dropped images, blank/duplicate/low-res figures, text/legend overlaps, saved-vs-built mismatch, and unresolvable artifact ids; emits a build receipt."
---

# doc-build-gate

A document is "built" only when the **exact saved file** compiled with a real engine, every warning class was classified, every promised figure is embedded, and the receipt says so. Past failures were all one pattern: the check ran on something other than the deliverable (a test copy, page 1 of 3, a regex that could not match, a marker that only resolves in chat). Helpers load with this skill (prefix `dg_`); each raises a `Dg*Error` (carrying `.result`) on failure. Do not catch-and-continue; fix the cause or pass an explicit `allow=` and say so in the report.

## Protocol (run in order; stop at the first failure)

1. **Write build sources with relative paths only.** Figures sit in `figures/` next to the document and are referenced as `figures/name.png`. Chat artifact markers (the double-brace `artifact:` syntax), local sandbox paths and artifact-id file names belong in chat prose, never in `.tex/.md/.html/.qmd`.
2. **Check figures before embedding.** On the live Figure: `dg_overlap_check(fig)` before `fig.savefig(...)`. On the saved PNGs: `dg_figure_qa(paths)`. If a defect is fixed, re-run both; do not claim a fix without the passing result.
3. **Gate the source:** `dg_check_sources("paper/main.tex", min_images=N)` (N = figures you generated for this document).
4. **Build the exact file** (not a copy): `dg_build_tex(path, expected_figures=N)` or `dg_build_md(path, to="pdf"|"html", expected_images=N)`. These re-run the source gate, build, classify the log/pandoc warnings, count embedded images, hash the source before and after, and write `.dg_build/<stem>.build_receipt.json`.
5. **Independent output checks:** `dg_page_ink_check(pdf)` (catches overflow and blank pages that TeX stays silent about: minted/verbatim/tables), then `dg_render_pages(pdf)` and **view every page PNG** (`read_file` each one; the build result lists `pages_to_view`). "Page 1 of 3 looks fine" is not a verification.
6. **Save, then prove what was saved is what was built:** after `save_artifacts`, `dg_verify_saved(built_path, host.artifact_path(version_id))` for each file. A bundle for Overleaf/handoff is the `.tex` + `figures/` + `.bib` zipped; extract the saved zip into an empty directory and run `dg_build_tex` there.
7. **Report from the receipt, verbatim:** engine, pages, figures embedded vs expected, failing warning classes (none), source sha256 prefix, pages viewed. Before sending the final message run `dg_verify_artifact_refs(final_text)` so every artifact link is a real id whose label names its file.

If a helper cannot run because a tool is missing (`DgToolMissing`), install it with the printed `manage_packages` command or say plainly that the check did not run. Check for a real engine *before* building workarounds for a weaker one.

## Helpers

| helper | does | fails (raises) on |
|---|---|---|
| `dg_check_sources(path, allowed_image_ext=None, min_images=0, check_labels=True, allow=None, allow_dynamic=False)` | static gate over `.tex` (follows `\input/\include`), `.md/.qmd`, `.html` | artifact markers; `/home/`, `/tmp/`, `/workspace`, `file://` paths; absolute or unresolvable image targets (extension search as LaTeX does, `\graphicspath`); 0-byte/corrupt images; markdown image paths with spaces (pandoc silently drops them); `\ref` to undefined or duplicate labels; cite keys absent from the `.bib`; too few images; disallowed image format |
| `dg_build_tex(path, engine="auto", outdir=None, expected_figures=None, allow=None, overfull_pt=1.0, underfull_badness=10000)` | latexmk, pdflatex/xelatex/lualatex (+bibtex/biber, rerun loop) or tectonic, whichever is installed | non-zero exit; stale/no PDF; source changed mid-build; any failing log class; figure-count mismatch |
| `dg_parse_tex_log(log_or_path, ...)` | classify warnings: `undefined_citation`, `undefined_reference`, `multiply_defined`, `missing_file`, `missing_character`, `font_substitution`, `rerun_needed`, `overfull`/`underfull` (thresholds), `error`, plus `other_warning` (reported, not failing unless `fail_on_other=True`) | any failing class (rejoins 79-column wrapped lines) |
| `dg_build_md(path, to="html", pdf_engine="auto", expected_images=None, allow=None)` | pandoc build; always also an embedded-resources HTML probe | any pandoc `[WARNING]/[ERROR]`; images not embedded or fewer than referenced; missing/stale output; PDF image count below raster refs. Prefers LaTeX-family PDF engines; warns when falling back to typst/weasyprint (they drop column widths/longtable pagination) |
| `dg_count_figures(pdf_or_html_or_docx_or_pptx, expected, exact=True, expected_captions=None)` | images embedded per page/slide; HTML data-URI validity; caption count | count differs from expected |
| `dg_figure_qa(png_paths, min_side=300, dup_hamming=6, dup_rel=0.15, allow_duplicates=None)` | blank/near-uniform (opaque and transparent), low resolution, identical files, near-duplicates (aHash + dHash + relative ink-map difference) | any of these, and zero input images |
| `dg_overlap_check(fig, min_frac=0.1, expect_labels=None, ignore=None)` | draws the Figure; exact rotated text rectangles | text/text overlap (titles, ticks, annotations, legend entries); legend over data, bars, text or another axes; text or legend outside the canvas; axes with no finite data; expected labels missing |
| `dg_page_ink_check(pdf, min_margin_pt=18.0, allow_blank_pages=None)` | rasterises pages, finds ink bbox | ink near a page edge; blank pages |
| `dg_render_pages(pdf, outdir=None, dpi=100)` | one PNG per page for viewing | no pages |
| `dg_receipt(source, outputs, checks, out_path)` | hashes, check verdicts, tool versions, git commit/dirty | any check with `ok=False` |
| `dg_verify_saved(built, saved)` | byte-compare built file with the saved/delivered copy | any difference (shows first differing lines) |
| `dg_verify_artifact_refs(text)` | every artifact reference in chat text is a full UUID, resolves in the store, and link label matches filename | hash prefixes, invented ids, mislabeled links |

All take `strict=True` by default (where applicable); `strict=False` returns the structured dict (`ok`, `problems`/`errors`, details) without raising, for aggregation into a receipt.

## Minimal session

```python
N = 6                                    # figures generated for this paper
for name, fig in figs.items():           # live matplotlib Figures
    dg_overlap_check(fig, expect_labels=MODEL_NAMES)
    fig.savefig(f"paper/figures/{name}.png", dpi=200)
dg_figure_qa(sorted(glob.glob("paper/figures/*.png")))
r = dg_build_tex("paper/main.tex", expected_figures=N)
dg_page_ink_check(r["pdf"])
pages = dg_render_pages(r["pdf"])        # then read_file every path in `pages`
# save_artifacts(...); then:
dg_verify_saved("paper/main.tex", host.artifact_path(main_tex_vid))
print(r["receipt"]["all_checks_ok"], r["pages"], r["figures"]["count"])
```

Markdown: `dg_build_md("review/outline.md", to="pdf", expected_images=N)`; slides built elsewhere (pptx/Beamer): `dg_count_figures("deck.pptx", expected=N)` gives per-slide image counts, and the overlap/QA helpers apply to the figures that go on them.

## Judgement calls

- A failing `underfull`/`overfull` on a deliberately loose paragraph may be allowed with `allow=["underfull"]`; record the allowance in the report. Never raise a threshold or add an `allow` just to get a green build.
- `dg_overlap_check` flags legend-over-reference-line (axhline/axvline) as a warning only. Image/heatmap cells are not treated as data for legend collisions: look at those figures.
- Near-duplicate flags between deliberately paired figures: pass `allow_duplicates=[("a.png","b.png")]`.
- Positive control for any new checker you write: feed it a known-bad input and confirm it fails (see `references/checklist.md`). A check that has never failed has not been shown to work.

## Limits (state them in the report when relevant)

- Vector-only figures (TikZ, pgfplots, vector PDFs without raster content) are not image objects: use `expected_captions` or inspect pages. The converter that drops TikZ is the failure to watch.
- `dg_count_figures` on PDFs ignores images under `min_px=32` (logos, bullets).
- Overfull/underfull warnings do not exist for `minted`/verbatim content; `dg_page_ink_check` is the substitute signal.
- latexmk/pdflatex/biber paths are implemented but exercised only where those binaries exist; tectonic and pandoc paths are covered by `doc-build-gate_tests.py`.
- Figure-title claims versus plotted values, caption numbers versus rendered order (pandoc auto-numbers), and visual judgement of every page remain manual: `references/checklist.md`.

## Provenance

The checks in this skill come from a review of real failures in AI-assisted research sessions. `tests/` in the repository holds synthetic fixtures that reproduce each failure pattern: known-bad inputs must fail, known-good inputs must pass.

## References

- `references/checklist.md`: manual checks that cannot be automated (page-by-page viewing, titles versus data, numbering), slide defects, positive-control recipe.
- `references/log-classes.md`: each warning class, real log line, what it means, fix.
- `references/engines.md`: engine selection, install commands, bibliography handling, output layout.
