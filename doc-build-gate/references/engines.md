# Engines, install, layout

## Selection (`engine="auto"` in `dg_build_tex`)
latexmk (needs a pdflatex/xelatex/lualatex) > pdflatex > xelatex > lualatex > tectonic. Explicit values: `latexmk`, `latexmk-xelatex`, `latexmk-lualatex`, `pdflatex`, `xelatex`, `lualatex`, `tectonic`. If none is present the helper raises `DgToolMissing` with the install command; it never substitutes a non-TeX renderer.

Install (Claude Science):
- `manage_packages(mode="install", environment="python", packages=["tectonic"])`: single binary; fetches TeX packages on demand (needs network access to its bundle host).
- `packages=["pandoc"]` for `dg_build_md`.
- Full TeX Live: conda-forge `texlive-core` and `latexmk` (large). Add `biber` only for biblatex documents.

Check for a real LaTeX engine before building table-pagination or layout workarounds on pandoc-to-typst: typst/weasyprint drop LaTeX `p{}` column widths, landscape directives and longtable pagination without an error.

## Bibliography
- Direct engines (`pdflatex` etc.): `dg_build_tex` runs bibtex (or biber when a `.bcf` exists), then reruns until the aux files stop changing and no `rerun_needed` remains (max `max_runs`).
- latexmk and tectonic handle bibliography and reruns themselves; `.blg` is appended to the parsed log so `I didn't find a database entry` is seen.
- `dg_check_sources` already cross-checks `\cite` keys against the `.bib` before any build.

## Output layout
Builds go to `<doc dir>/.dg_build/` (override with `outdir`), leaving the source directory clean: `<stem>.pdf`, `<stem>.log`, aux files, `<stem>.build_receipt.json`. Subdirectories of `\include`d files are created inside `outdir`. `TEXINPUTS`, `BIBINPUTS`, `BSTINPUTS` are set to the document directory, so relative paths behave exactly as in the saved file.

## Receipt fields
`source.sha256`, `outputs[].sha256`, per-check verdicts (`checks`), `all_checks_ok`, `failed_checks`, tool versions, `git.commit` and `git.source_dirty` when in a repo, engine, runs, pages, `build_problems`. Commit the receipt next to the paper if the repo convention tracks build outputs; otherwise attach it to the report.
