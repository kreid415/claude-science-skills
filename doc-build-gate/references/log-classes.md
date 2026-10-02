# Warning classes recognised by `dg_parse_tex_log`

Logs wrap at 79 columns; the parser rejoins wrapped lines before matching. Fail-by-default classes are marked (F).

| class | real line (abridged) | meaning / fix |
|---|---|---|
| `error` (F) | `! Undefined control sequence.` / `! LaTeX Error: ...` / `! Missing $ inserted.` | compile error; line number follows in `l.NN`. Fix the source; do not rely on the nonstopmode output PDF |
| `undefined_citation` (F) | `LaTeX Warning: Citation 'k' on page 1 undefined` / `Package natbib Warning: Citation ...` / `Warning--I didn't find a database entry for "k"` (.blg) | key not in `.bib`, or bibtex/biber did not run |
| `undefined_reference` (F) | `LaTeX Warning: Reference 'sec:x' on page 3 undefined` / `Hyper reference ...` / `pdfTeX warning (dest): name{figure.7} has been referenced but does not exist` | label missing or typo; also fires on the summary line `There were undefined references.` |
| `multiply_defined` (F) | `LaTeX Warning: Label 'fig:a' multiply defined.` | duplicate `\label`; refs may point to the wrong place |
| `missing_file` (F) | `! LaTeX Error: File 'x.png' not found.` / `Package pdftex.def Error: File ... not found: using draft setting` / `Unable to load picture or PDF file` / `Unknown graphics extension` / `No file main.bbl.` | image path/extension or bibliography missing; draft setting yields an empty frame, not an image |
| `missing_character` (F) | `Missing character: There is no é in font cmr10!` | unicode not supported by font/engine; use xelatex/lualatex/tectonic or proper escapes |
| `font_substitution` (F) | `LaTeX Font Warning: Font shape 'OT1/cmr/bx/sc' undefined` / `Some font shapes were not available, defaults substituted.` / `Package fontspec Warning` | typography silently changed |
| `rerun_needed` (F) | `Label(s) may have changed. Rerun to get cross-references right.` / `Please (re)run Biber` | the final log still asks for another pass; `dg_build_tex` loops up to `max_runs` and fails if still pending |
| `overfull` (F above `overfull_pt`, default 1.0) | `Overfull \hbox (12.30pt too wide) in paragraph at lines 10--12` | text/table sticks into the margin. Not emitted for minted/verbatim: use `dg_page_ink_check` |
| `underfull` (F at `underfull_badness` >= 10000) | `Underfull \hbox (badness 10000) ...` | loose line/column; allow explicitly if intended |
| `other_warning` | any other `... Warning:` | reported in `classes`; failing only with `fail_on_other=True` (e.g. float specifier changes) |

`allow=[...]` names classes to ignore; the report records `allowed`. The earlier pattern that matched only `' undefined` missed reference warnings because their wording is `Reference 'x' on page N undefined`; the parser matches by warning type, with generic fallbacks for citation/reference wording variants.
