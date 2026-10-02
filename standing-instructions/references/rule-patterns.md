# Rule patterns

## Scopes

| scope | holds | examples |
|---|---|---|
| data | which data, genes, samples, papers are in or out | exclude non-human genes from foundation models; keep named papers out of the review |
| method | metric definitions, designs, protocols the user fixed | standard published scIB scores; three perturbation pairs; final run recreated from the baseline, no cherry-picking |
| format | file types, markup, layout, figure rules | PNG not PDF; no `itemize`; figures referenced by filename in `figures/`; one metric family per panel |
| writing | what a document may contain, tone | outline has no narrative the user did not dictate; plain language; explicit Introduction section |
| collaboration | project boundaries, who is involved | two projects are separate papers; no collaboration with a named group |
| process | how work is done | answer questions before executing; verify against the primary source; ask before publishing |

## Writing the `check`

`check` is a dict of lists, applied to the text of the deliverable (all `MULTILINE`):

| key | meaning | note |
|---|---|---|
| `forbid` | regex that must not match | use `ignore_case: True` for case-insensitive |
| `require` | regex that must match | |
| `forbid_literal` / `require_literal` | plain strings, no escaping needed | prefer these for names and LaTeX commands |
| `files` (on the rule) | globs; the check is skipped for other file names | skipped files are reported under `skipped`, never as passed |

A check can only see text. It proves a string is present or absent, not that the rule was followed. Rules that need
reading (narrative, plain language, "explain in prose") have no check and are returned as `manual`.

### Cookbook

| rule | check |
|---|---|
| No `itemize` | `{"forbid_literal": ["\\begin{itemize}", "\\begin{enumerate}"]}`, files `*.tex` |
| Markdown prose, no bullets | `{"forbid": ["^\\s*[-*] "]}`, files `*.md` |
| PNG not PDF | `{"forbid": ["\\.pdf\\b"], "ignore_case": True}` |
| Figures by filename | `{"forbid": ["artifact:"], "require": ["\\\\includegraphics(\\[[^\\]]*\\])?\\{figures/[^}]+\\}"]}` |
| Markdown outline embeds figures | `{"require": ["!\\[[^\\]]*\\]\\([^)]+\\.png\\)"]}` |
| Dataset or paper excluded | `{"forbid_literal": ["<name>", "<name>"]}` |
| Design keeps all confirmed arms | `{"require_literal": ["IFN", "LPS", "T-cell"]}` on the design doc |
| Published metric includes component X | `{"require_literal": ["kBET"]}` |
| Introduction section exists | `{"require": ["^\\\\section\\{Introduction\\}"]}` |
| No unresolved placeholders | `{"forbid": ["\\{\\{[a-z_]+:", "\\bTODO\\b"]}` |

Write the pattern from the user's wording, then test it once on a known-bad and a known-good snippet before relying on it
(`si_check_output(bad, [rule], strict=False)` must report a violation). A pattern that never fires is worse than none.

### Manual rules

For a rule with no check, open the saved output and verify it by reading. Report the evidence in the `Standing rules:` line
(file, section, what you saw). Examples: "outline lists only the four dictated elements"; "no model description changed
beyond the five edits listed".

## Choosing keywords

`si_applicable` also matches on the words of the rule and on scope trigger words in the task text. Add `keywords` for the
nouns the user uses for the deliverable ("outline", "review", "foundation"). Use `["*"]` for a rule that applies to
everything (for example "verify against the primary source before suggesting").
