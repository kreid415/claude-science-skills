# Manual checklist (what the helpers cannot decide)

Tick each item against the built output, not against memory of what the code does. Anything not looked at is reported as "not checked".

## Every page / slide
- [ ] Every page PNG from `dg_render_pages` was opened (count opened = `pages`; re-do after any rebuild, since page count can change: an earlier "2 pages verified" does not cover a 3-page rebuild).
- [ ] No text running into or under an image; captions not split from their figure; no stray math or escape sequences (`\'e`, `$\beta$`) in titles/author names.
- [ ] Table columns readable; long tables paginate (page count plausible for the row count).
- [ ] Slides: legend, p-values and annotations do not overlap data or each other; text boxes do not cut into pictures; no slide shows a number that differs from the table it summarises.

## Figures
- [ ] Panel title/claim matches every plotted value (a blanket title such as "only at lambda >= 0.2" must hold for the whole series; a number in a title came from this panel's data, not a neighbour's).
- [ ] Same model/series set in every panel that should share it (`expect_labels` to `dg_overlap_check`); a filter on a display name must be compared with the names actually stored.
- [ ] Axis labels, units, and legend order correct; legend text describes the visible lines.
- [ ] Numbers in captions recomputed from the data file this session, not retyped.

## Numbering and references
- [ ] Hard-coded "Figure N" in captions agrees with rendered order (pandoc and LaTeX number by appearance; prefer `\ref`/`{#fig:x}` over typed numbers).
- [ ] Text citing a figure/section number or label uses a reference, not a typed number.
- [ ] The final message's counts (figures, pages) equal `dg_count_figures`/receipt values.

## Delivered bundle
- [ ] The files delivered are the files built: `dg_verify_saved` for each.
- [ ] Image format requested by the user is the format delivered (png vs pdf): `allowed_image_ext`.
- [ ] Bundle extracted to an empty directory compiles with `dg_build_tex` (relative paths, bib present).
- [ ] Artifact links in chat pass `dg_verify_artifact_refs`.

## Positive control for any checker (new or old)
A checker that has not been seen to fail has no demonstrated detection power. Before trusting a grep/regex/script that reports "zero problems":
1. Create a copy of the input with one deliberate defect of the class under test (bad `\ref`, bad `\cite`, missing image, overlapping text).
2. Run the checker; it must fail and name the defect.
3. Run it on the real input and report both outcomes.
`dg_parse_tex_log` was validated this way against real log lines for every class (see `doc-build-gate_tests.py`).
