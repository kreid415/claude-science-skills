"""Perturb-seq workflow schematic (demo / acceptance test for the bio-diagram skill).
Reads only local assets (fig_assets/icons/*.svg + provenance sidecars); no network."""
import sys, json
sys.path.insert(0, "src")  # vendored copy, see vendor_biodiagram("src")
import biodiagram as bd

A = lambda n: bd.load_asset(f"fig_assets/icons/{n}.svg")
d = bd.Diagram(width_mm=180, height_mm=57, title="Pooled CRISPR screen with single-cell RNA-seq readout",
               desc="Schematic workflow; not a data panel.")
P = bd.PALETTE
W = d.W
steps = [("lib", "bioicons_lentivirus_library", "sgRNA lentiviral\nlibrary"),
         ("cells", "bioicons_cell_clumps", "Cas9-expressing cells\n(low MOI)"),
         ("drop", "bioicons_singlecell_droplet_overloading", "Droplet capture\n(cells + barcoded beads)"),
         ("seq", "bioicons_illumina_miseq", "Short-read\nsequencing"),
         ("umap", "bioicons_singlecell_clustering_datareduction_umap", "Guide assignment +\nexpression clustering")]
n = len(steps)
margin, gapx = 8, 14
bw = (W - 2 * margin - (n - 1) * gapx) / n
top, bh = 22, 120
d.text("title", margin, 14, "Perturb-seq workflow", size=d.st["title_pt"], weight="bold", anchor="start")
for i, (key, asset, lab) in enumerate(steps):
    x = margin + i * (bw + gapx)
    d.box(f"step{i+1}", x, top, bw, bh, fill=P["panel"], rx=5)
    d.text(f"num{i+1}", x + 6, top + 11, str(i + 1), size=8, weight="bold", anchor="start", color=P["blue"])
    d.icon(key, A(asset), x + bw / 2, top + 50, w=bw - 22, h=52)
    d.text(f"{key}-label", x + bw / 2, top + 96, lab, size=7)
for i in range(n - 1):
    a, b = steps[i][0], steps[i + 1][0]
    x0 = margin + (i + 1) * bw + i * gapx
    y = top + 50
    d.arrow((x0 + 1.5, y), (x0 + gapx - 1.5, y), id=f"arrow{i+1}", color=P["blue"])
d.text("note", W - margin, top + bh + 12, "Icons: Bioicons (see attribution file). Schematic; not to scale.",
       size=6, anchor="end", color="#555555")

if __name__ == "__main__":
    issues = d.check()
    d.preview("preview.png", dpi=150)
    d.preview("preview_debug.png", dpi=150, debug=True)
    if not [x for x in issues if x["severity"] == "error"]:
        print(json.dumps(d.export("perturbseq_schematic", outdir="figures"), default=str))
        print(d.write_attribution("figures", "perturbseq_schematic"))
