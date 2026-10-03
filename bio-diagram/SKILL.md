---
name: bio-diagram
description: "Build a publication-quality biological schematic (workflow, mechanism, pathway, experimental design, graphical abstract) as an editable SVG from open icon libraries: search and download icons from Bioicons (pinned GitHub commit), fall back to SciDraw (Zenodo DOIs), assemble with one style (fonts, palette, stroke weights, arrow semantics), render a preview, check overlaps and legibility, revise, then export SVG + PDF + 300-dpi PNG and write a per-asset attribution file (source, author, licence, changes). Use for any schematic or icon-based figure panel; not for panels that plot data."
---

# bio-diagram

Library: `scripts/biodiagram.py`. Run figure work in the `figure-editor` environment and load it
there with `bd = load_biodiagram()`; if the sidecar loaded into a different kernel, use
`sys.path.insert(0, os.path.dirname(<that path>)); import biodiagram as bd`.
Environment packages: (cairosvg, lxml, pillow, pypdf, fontconfig). Fonts: Arial
metrics via Liberation Sans (fc-match). Coordinates are in **points** (viewBox in pt), so
font sizes and stroke widths in code equal their printed size.

## Scope

- Schematics only: workflows, mechanisms, pathways, study designs, graphical abstracts.
- Never draw a schematic where a figure panel should show data. A cartoon UMAP or bar
  icon stands for a step; it must not imply a result. No invented axes or values.
- Hand off data panels to figure-style / figure-composer.

## Workflow

1. **Brief.** Write down the claim the diagram supports, the entities, the relations
   (flow / activation / inhibition / indirect), reading order, and final size: single
   column 85-90 mm, 1.5 column ~120 mm, double column 170-180 mm. Check the project's
   standing-instructions (CONSTRAINTS.md) for journal format rules.
2. **Find assets: Bioicons first.** `idx = bd.bioicons_index()` (pins the current commit
   of duerrsimon/bioicons and caches the index in the project's `fig_assets/_cache/`). Then
   `bd.search_bioicons("t cell", idx, k=10)`. Try synonyms (lymphocyte, T-cell, leukocyte).
   Licence order: CC0 / MIT / BSD, then CC BY. CC BY-SA is excluded by default
   (`allow_share_alike=True` only if the user accepts CC BY-SA for the whole figure).
3. **SciDraw if Bioicons has nothing suitable.** `bd.search_scidraw("mouse")` searches the
   Zenodo deposits that carry SciDraw DOIs (scidraw.io is not on the network allowlist;
   request access only if the Zenodo route fails). Raster-only records are rejected.
   Do not take icons from sources without a clear open licence (BioRender, image search).
4. **Fetch and choose by eye.** `a = bd.fetch_asset(hit)` saves a sanitized SVG to the
   project's `fig_assets/icons/` with a provenance sidecar `<file>.json` (URLs pinned to the commit/DOI,
   author, licence, sha256, retrieval time). `bd.contact_sheet(list_of_assets)` renders
   candidates in a grid; view it and pick. Reject icons with brand logos (unless the
   vendor is the point), embedded raster images, or a drawing style that clashes with the
   other icons. Mixed styles (flat vs shaded, line vs filled) are the most common visual
   inconsistency.
5. **Write the figure script** `figures/<name>/make_<name>.py`, modelled on
   `examples/make_perturbseq_schematic.py`. It reads only local assets
   (`bd.load_asset(path)`), so re-running needs no network. Vendor the library into the
   project with `vendor_biodiagram("src")` and commit both, plus `fig_assets/icons/`.
6. **Assemble** with `d = bd.Diagram(width_mm, height_mm, title=..., desc=...)`:
   `d.box` (compartments, step panels), `d.icon` (fit in w x h pt, aspect kept, whitespace
   cropped), `d.text` / `d.label` (live text), `d.arrow` (`kind="flow"|"inhibition"|"line"`,
   `dashed=True` for indirect/hypothesised, `curve=`, `via=[...]` for elbows,
   `src_side`/`dst_side`), `d.panel_letter`. The SVG uses Inkscape layers
   (Background / Icons / Connectors / Labels); every element has a readable id.
7. **Check, look, revise.** Run `d.check()`, then `d.preview("preview.png")` and
   `d.preview("preview_debug.png", debug=True)` (red = text boxes, blue = icon boxes,
   green = arrow extents) and view both images. Fix every `error`; resolve or justify every
   `warn`. Then look for what the geometry cannot see: icon styles that do not match,
   unequal visual weight, reading order, crowding, a label that names the wrong thing.
   Repeat until clean. Record each round (what was found, what changed) in the lab
   notebook or the figure's README.
8. **Export.** `rep = d.export("<name>", outdir="figures/<name>")` writes SVG, PDF and a
   300-dpi PNG (pHYs set) and verifies PNG pixels and the PDF page size against the
   requested mm. Assert `rep["png_ok"] and rep["pdf_ok"]`.
9. **Attribution.** `d.write_attribution("figures/<name>", "<name>")` writes
   `<name>_attribution.md` (paste-ready credit lines plus licence obligations), `.csv` and
   `.json`, listing every asset with source URL/DOI, author, licence, retrieval time,
   sha256 and the changes made (scaled, cropped, recoloured, mirrored). The SVG also
   carries the list in `<metadata id="attribution">`. Put the credit lines in the caption
   or acknowledgements; CC BY requires them.
10. **QA gate and delivery.** Run doc-build-gate's figure QA on the exported files, the
    standing-instructions check, and claim-gate before calling the figure done. Save
    SVG/PDF/PNG plus the attribution files as artifacts and commit the script, assets and
    outputs.

## House style (defaults in `bd.STYLE`, `bd.PALETTE`)

- Font: Arial/Helvetica (Liberation Sans for metrics), live text; never outline text in
  the SVG. Title 9 pt bold, labels 7 pt, notes 6 pt, panel letters 10 pt bold.
  Floor: 6 pt at final size (`check` enforces it, including text inside icons).
- Colour: Okabe-Ito palette; one accent colour per meaning, used consistently. Colour
  never carries meaning alone (pair with shape, line style or a label). Text contrast
  >= 4.5:1 against its panel.
- Lines: arrows 1 pt, panel outlines 0.75 pt or none. Filled triangular head = flow /
  activation; T-bar = inhibition; dashed = indirect or hypothesised. Say so in the legend
  or caption when more than one kind is used.
- Layout: one reading direction (left to right or top to bottom), aligned centres,
  equal gaps, similar icon heights. Label every icon or none, all on the same side.
- `recolor=` only on monochrome or line icons; on shaded icons it flattens the drawing
  into a silhouette. Recolouring counts as a change and is recorded.
- No added drop shadows, gradients or 3D effects.

## Checks performed by `d.check()`

| kind | severity |
|---|---|
| text-overlap, text-on-icon, arrow-through-text, arrow-through-icon | error |
| font-too-small, icon-text-illegible (text baked into an icon prints below the floor) | error |
| outside-canvas (margin 2 pt) | error |
| icon-overlap, arrows-cross, arrow-short, low-contrast, unused-canvas | warn |

Text boxes are estimated from font metrics. A different font on the reader's machine can
shift widths by a few percent, so keep at least 2 pt clearance.

## Pitfalls

- Bioicons file paths do not always match `icons.json` (author directory case and
  spelling). The index is built from the git tree, so the URLs resolve.
- Many Servier and DBCLS icons are CC BY 3.0/4.0: attribution is required.
- Some SciDraw files have no extension; `fetch_asset` reads the content to decide.
- Illustrator SVGs use DTD entities and `<style>` classes. The sanitizer resolves
  internal entities, drops external ones and scripts, inlines CSS and prefixes ids, so one
  icon cannot restyle another.
- After editing in Inkscape or Illustrator, re-export from the edited SVG and note the
  manual edits in the figure README; the script no longer reproduces the figure exactly.
