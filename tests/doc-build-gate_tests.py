# Tests for the doc-build-gate sidecar (kernel.py). Run:  python doc-build-gate_tests.py [path/to/kernel.py]
# Every test has a known-bad fixture (must raise the specific Dg*Error) and/or a known-good fixture (must pass).
# Each test reproduces a known failure pattern.
import os, sys, re, json, shutil, tempfile, traceback, warnings
warnings.filterwarnings("ignore")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

KERNEL = sys.argv[1] if len(sys.argv) > 1 else "kernel.py"
G = {}
exec(open(KERNEL).read(), G)
TMP = tempfile.mkdtemp(prefix="dg_tests_")
RESULTS = []


def test(name, incidents=()):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, incidents, True, ""))
        except Exception as ex:
            RESULTS.append((name, incidents, False, "%s: %s" % (type(ex).__name__, str(ex)[:300])))
            traceback.print_exc()
        return fn
    return deco


def raises(kind, fn, contains=None):
    cls = G["dg_exc"](kind)
    try:
        fn()
    except cls as ex:
        assert contains is None or contains in str(ex), "wrong message: %s" % str(ex)[:300]
        assert type(ex).__name__.startswith("Dg")
        return ex
    raise AssertionError("expected %s, nothing raised" % cls.__name__)


def png(path, seed=0, kind="scatter", size=(4, 3)):
    fig, ax = plt.subplots(figsize=size, dpi=100)
    rng = np.random.default_rng(seed)
    if kind == "scatter":
        ax.scatter(rng.normal(size=40), rng.normal(size=40))
    elif kind == "bars":
        ax.bar(range(6), rng.uniform(1, 5, 6), color="C1")
    elif kind == "blank":
        ax.axis("off")
    fig.savefig(path)
    plt.close(fig)
    return path


def mk(name):
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    return d


def w(d, fn, text):
    p = os.path.join(d, fn)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)
    return p


GOOD_TEX = r"""\documentclass{article}
\usepackage{graphicx}
\begin{document}
See Figure~\ref{fig:a}.
\begin{figure}\includegraphics[width=0.5\linewidth]{figures/a.png}\caption{A}\label{fig:a}\end{figure}
\end{document}
"""

# ------------------------------------------------------------- source gate
@test("src: artifact marker in \\includegraphics fails", ())
def _():
    d = mk("s1"); png(os.path.join(d, "figures", "a.png")) if os.makedirs(os.path.join(d, "figures"), exist_ok=True) is None else 0
    p = w(d, "main.tex", GOOD_TEX.replace("figures/a.png", "{{artifact:art_0123abcd-1234-4abc-8abc-0123456789ab}}"))
    raises("source", lambda: G["dg_check_sources"](p), "artifact_marker")


@test("src: clean tex with relative real png passes", ())
def _():
    d = mk("s2"); os.makedirs(os.path.join(d, "figures"), exist_ok=True); png(os.path.join(d, "figures", "a.png"))
    p = w(d, "main.tex", GOOD_TEX)
    r = G["dg_check_sources"](p)
    assert r["ok"] and r["n_images"] == 1


@test("src: artifact-id style filename without extension / nonexistent fails", ())
def _():
    d = mk("s3")
    p = w(d, "main.tex", GOOD_TEX.replace("figures/a.png", "f94706ee9ab3"))
    ex = raises("source", lambda: G["dg_check_sources"](p), "missing_image")


@test("src: absolute sandbox path in includegraphics fails even if file exists", ())
def _():
    d = mk("s4"); os.makedirs(os.path.join(d, "figures"), exist_ok=True); fp = png(os.path.join(d, "figures", "a.png"))
    p = w(d, "main.tex", GOOD_TEX.replace("figures/a.png", fp))
    ex = raises("source", lambda: G["dg_check_sources"](p))
    kinds = {x["kind"] for x in ex.result["problems"]}
    assert "absolute_image_path" in kinds and "absolute_path" in kinds, kinds


@test("src: markdown ![](artifact marker) and missing file fail; relative ok", ())
def _():
    d = mk("s5"); png(os.path.join(d, "a.png"))
    bad = w(d, "bad.md", "# T\n![x]({{artifact:PLACEHOLDER}})\n![y](nothere.png)\n")
    ex = raises("source", lambda: G["dg_check_sources"](bad))
    assert {"artifact_marker", "missing_image"} <= {x["kind"] for x in ex.result["problems"]}
    good = w(d, "good.md", "# T\n![x](a.png)\n")
    assert G["dg_check_sources"](good)["n_images"] == 1


@test("src: markdown image path with space (pandoc silently drops) fails", ())
def _():
    d = mk("s5b"); png(os.path.join(d, "my fig.png"))
    p = w(d, "a.md", "![x](my fig.png)\n")
    raises("source", lambda: G["dg_check_sources"](p), "image_path_space")


@test("src: outline with zero embedded images fails when figures expected", ())
def _():
    d = mk("s6"); p = w(d, "outline.md", "# Outline\nText only.\n")
    raises("source", lambda: G["dg_check_sources"](p, min_images=3), "too_few_images")


@test("src: static \\ref to nonexistent label and duplicate label fail", ())
def _():
    d = mk("s7"); os.makedirs(os.path.join(d, "figures"), exist_ok=True); png(os.path.join(d, "figures", "a.png"))
    t = GOOD_TEX.replace("See Figure", "Mechanism in Sec.~\\ref{sec:mechanisms}. See Figure").replace("\\end{document}", "\\label{fig:a}\n\\end{document}")
    p = w(d, "main.tex", t)
    ex = raises("source", lambda: G["dg_check_sources"](p))
    assert {"undefined_ref_static", "duplicate_label"} <= {x["kind"] for x in ex.result["problems"]}


@test("src: cite key missing from .bib fails; present passes", ())
def _():
    d = mk("s8"); w(d, "refs.bib", "@article{good2020,\n title={x}}\n")
    base = "\\documentclass{article}\\begin{document}\\cite{%s}\\bibliography{refs}\\bibliographystyle{plain}\\end{document}\n"
    raises("source", lambda: G["dg_check_sources"](w(d, "bad.tex", base % "bad2020"), check_labels=True), "undefined_cite_static")
    assert G["dg_check_sources"](w(d, "ok.tex", base % "good2020"))["ok"]


@test("src: \\input chain scanned; commented-out marker ignored; allow regex works", ())
def _():
    d = mk("s9"); os.makedirs(os.path.join(d, "figures"), exist_ok=True); png(os.path.join(d, "figures", "a.png"))
    w(d, "sec/one.tex", "Text {{artifact:art_0123abcd-1234-4abc-8abc-0123456789ab}}\n")
    p = w(d, "main.tex", "\\documentclass{article}\\begin{document}\n% {{artifact:art_x}} old\n\\input{sec/one}\n\\end{document}\n")
    ex = raises("source", lambda: G["dg_check_sources"](p), "artifact_marker")
    assert all("one.tex" in x["file"] for x in ex.result["problems"])
    assert G["dg_check_sources"](p, allow=[r"artifact"])["ok"]


@test("src: image format restriction (PDFs delivered when PNG requested)", ())
def _():
    d = mk("s10"); png(os.path.join(d, "a.png"))
    fig, ax = plt.subplots(); ax.plot([1, 2]); fig.savefig(os.path.join(d, "b.pdf")); plt.close(fig)
    p = w(d, "a.md", "![a](a.png)\n![b](b.pdf)\n")
    raises("source", lambda: G["dg_check_sources"](p, allowed_image_ext=[".png"]), "image_format")
    assert G["dg_check_sources"](w(d, "ok.md", "![a](a.png)\n"), allowed_image_ext=[".png"])["ok"]


@test("src: 0-byte / corrupt image fails", ())
def _():
    d = mk("s11"); open(os.path.join(d, "z.png"), "w").close(); open(os.path.join(d, "c.png"), "wb").write(b"notapng" * 20)
    raises("source", lambda: G["dg_check_sources"](w(d, "a.md", "![z](z.png)\n")), "empty_image")
    raises("source", lambda: G["dg_check_sources"](w(d, "b.md", "![c](c.png)\n")), "corrupt_image")


# ----------------------------------------------------------------- tex log
LOG_LINES = {
    "undefined_citation": ["LaTeX Warning: Citation `smith2020' on page 1 undefined on input line 12.",
                           "Package natbib Warning: Citation `jones' on page 2 undefined on input line 33.",
                           "Warning--I didn't find a database entry for \"lee2019\""],
    "undefined_reference": ["LaTeX Warning: Reference `sec:mechanisms' on page 3 undefined on input line 42.",
                            "LaTeX Warning: Hyper reference `fig:x' on page 4 undefined on input line 5.",
                            "pdfTeX warning (dest): name{figure.7} has been referenced but does not exist, replaced by a fixed one"],
    "multiply_defined": ["LaTeX Warning: Label `fig:a' multiply defined."],
    "missing_file": ["! LaTeX Error: File `missing.png' not found.",
                     "! Package pdftex.def Error: File `x.png' not found: using draft setting."],
    "overfull": ["Overfull \\hbox (12.30pt too wide) in paragraph at lines 10--12"],
    "underfull": ["Underfull \\hbox (badness 10000) in paragraph at lines 5--6"],
    "font_substitution": ["LaTeX Font Warning: Font shape `OT1/cmr/bx/sc' undefined",
                          "LaTeX Font Warning: Some font shapes were not available, defaults substituted."],
    "missing_character": ["Missing character: There is no \u00e9 in font cmr10!"],
    "rerun_needed": ["LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right."],
    "error": ["! Undefined control sequence.", "! Missing $ inserted."],
}


@test("log: every warning class is detected from real log lines", ())
def _():
    for cls, lines in LOG_LINES.items():
        for ln in lines:
            rep = G["dg_parse_tex_log"]("This is pdfTeX\n(./main.tex\n" + ln + "\n) Output written\n", strict=False)
            assert not rep["ok"] and cls in rep["failing"], (cls, ln, rep["failing"])
            raises("build", lambda: G["dg_parse_tex_log"]("x\n" + ln + "\ny\n"))


@test("log: old adjacency regex was blind to \\ref warnings; new parser is not", ())
def _():
    old = re.compile(r"' undefined")
    ref = "LaTeX Warning: Reference `sec:x' on page 3 undefined on input line 12."
    assert not old.search(ref)
    assert "undefined_reference" in G["dg_parse_tex_log"](ref, strict=False)["failing"]


@test("log: clean log passes; thresholds respected (0.5pt overfull, badness 5000 not flagged)", ())
def _():
    clean = "This is pdfTeX\nOverfull \\hbox (0.50pt too wide) in paragraph at lines 1--2\nUnderfull \\hbox (badness 5000) in paragraph at lines 3--4\nOutput written on a.pdf (1 page).\n"
    assert G["dg_parse_tex_log"](clean)["ok"]
    assert not G["dg_parse_tex_log"](clean, overfull_pt=0.1, strict=False)["ok"]
    assert G["dg_parse_tex_log"]("Underfull \\hbox (badness 10000) in x", allow=["underfull"])["ok"]


@test("log: 79-column wrapped warning is rejoined (long label)", ())
def _():
    full = "LaTeX Warning: Reference `sec:a_really_long_label_name_that_wraps_the_log_line' on page 3 undefined on input line 12."
    wrapped = full[:79] + "\n" + full[79:]
    rep = G["dg_parse_tex_log"](wrapped, strict=False)
    assert rep["counts"]["undefined_reference"] == 1 and rep["classes"]["undefined_reference"][0]["key"].startswith("sec:a_really")


# --------------------------------------------------------------- tex build
@test("build_tex: good doc builds with real engine, receipt hashes match, 1 figure embedded", ())
def _():
    d = mk("b1"); os.makedirs(os.path.join(d, "figures"), exist_ok=True); png(os.path.join(d, "figures", "a.png"))
    p = w(d, "main.tex", GOOD_TEX)
    r = G["dg_build_tex"](p, expected_figures=1)
    assert r["ok"] and r["pages"] == 1 and r["figures"]["count"] == 1
    rec = json.load(open(r["receipt"]["receipt_path"]))
    assert rec["source"]["sha256"] == G["dg_sha256"](p) and rec["all_checks_ok"]
    assert G["dg_verify_saved"](p, p)["ok"]
    pngs = G["dg_render_pages"](r["pdf"], os.path.join(d, "pages"))
    assert len(pngs) == r["pages"] and all(os.path.getsize(x) > 0 for x in pngs)


@test("build_tex: expected 2 figures but 1 embedded fails", ())
def _():
    d = mk("b1b"); os.makedirs(os.path.join(d, "figures"), exist_ok=True); png(os.path.join(d, "figures", "a.png"))
    raises("build", lambda: G["dg_build_tex"](w(d, "main.tex", GOOD_TEX), expected_figures=2), "found 1 embedded")


@test("build_tex: undefined ref + undefined cite in real log fail the build", ())
def _():
    d = mk("b2")
    t = "\\documentclass{article}\\begin{document}\nSee \\ref{sec:nope} and \\cite{ghost}.\n\\begin{thebibliography}{9}\\bibitem{real} R.\\end{thebibliography}\n\\end{document}\n"
    p = w(d, "main.tex", t)
    ex = raises("build", lambda: G["dg_build_tex"](p, check_sources=False))
    f = ex.result["log_report"]["failing"]
    assert "undefined_reference" in f and "undefined_citation" in f, f


@test("build_tex: LaTeX compile error is a failure and names the error", ())
def _():
    d = mk("b3")
    p = w(d, "main.tex", "\\documentclass{article}\\begin{document}\n\\badmacro{x}\n\\end{document}\n")
    ex = raises("build", lambda: G["dg_build_tex"](p))
    assert "Undefined control sequence" in str(ex) or "exit code" in str(ex)


@test("build_tex: source gate runs first - marker blocks the build", ())
def _():
    d = mk("b4"); os.makedirs(os.path.join(d, "figures"), exist_ok=True)
    p = w(d, "main.tex", GOOD_TEX.replace("figures/a.png", "{{artifact:art_0123abcd-1234-4abc-8abc-0123456789ab}}"))
    raises("source", lambda: G["dg_build_tex"](p))


@test("build_tex: no TeX engine raises DgToolMissing with install instructions", ())
def _():
    old = G["dg_which"]
    G["dg_which"] = lambda n: None
    try:
        d = mk("b5"); p = w(d, "main.tex", "\\documentclass{article}\\begin{document}x\\end{document}\n")
        ex = raises("tool", lambda: G["dg_build_tex"](p), "manage_packages")
    finally:
        G["dg_which"] = old


@test("build_tex: bibtex bibliography resolves through a real build", ())
def _():
    d = mk("b6"); w(d, "refs.bib", "@article{good2020,\n author={A. Author}, title={T}, journal={J}, year={2020}}\n")
    p = w(d, "main.tex", "\\documentclass{article}\\begin{document}Cited \\cite{good2020}.\\bibliographystyle{plain}\\bibliography{refs}\\end{document}\n")
    r = G["dg_build_tex"](p)
    assert r["ok"] and not r["log_report"]["failing"], r["log_report"]["failing"]


@test("build_tex: engine=tectonic explicit; missing engine name raises tool error", ())
def _():
    d = mk("b7"); p = w(d, "main.tex", "\\documentclass{article}\\begin{document}x\\end{document}\n")
    assert G["dg_build_tex"](p, engine="tectonic")["engine"] == "tectonic"
    old = G["dg_which"]
    G["dg_which"] = lambda n: None if n == "pdflatex" else old(n)
    try:
        raises("tool", lambda: G["dg_build_tex"](p, engine="pdflatex"), "not installed")
    finally:
        G["dg_which"] = old


# ---------------------------------------------------------------- md build
@test("build_md: html with 2 images embeds both; receipt written", ())
def _():
    d = mk("m1"); png(os.path.join(d, "a.png")); png(os.path.join(d, "b.png"), seed=3, kind="bars")
    p = w(d, "doc.md", "# T\n\n![A](a.png)\n\ntext\n\n![B](b.png)\n")
    r = G["dg_build_md"](p, to="html")
    assert r["ok"] and r["figures"]["embedded"] == 2 and os.path.exists(r["receipt"]["receipt_path"])


@test("build_md: expected_images larger than embedded fails (silent image loss)", ())
def _():
    d = mk("m2"); png(os.path.join(d, "a.png"))
    p = w(d, "doc.md", "# T\n\n![A](a.png)\n")
    raises("build", lambda: G["dg_build_md"](p, to="html", expected_images=3), "silent image loss")


@test("build_md: unreachable remote image -> pandoc warning is a failure", ())
def _():
    d = mk("m3")
    p = w(d, "doc.md", "# T\n\n![A](http://127.0.0.1:9/a.png)\n")
    raises("build", lambda: G["dg_build_md"](p, to="html"))


@test("build_md: pdf output keeps raster images (not alt text)", ())
def _():
    d = mk("m4"); png(os.path.join(d, "a.png")); png(os.path.join(d, "b.png"), seed=3, kind="bars")
    p = w(d, "doc.md", "# T\n\n![A](a.png)\n\n![B](b.png)\n")
    r = G["dg_build_md"](p, to="pdf")
    assert r["ok"] and r["figures"]["count"] >= 2 and r["pages"] >= 1, r["figures"]


# ------------------------------------------------------------ count figures
@test("count_figures: html with zero images vs expected 3 fails", ())
def _():
    d = mk("c1"); p = w(d, "o.html", "<html><body><p>text only</p></body></html>")
    raises("count", lambda: G["dg_count_figures"](p, expected=3), "found 0")
    assert G["dg_count_figures"](p, expected=0)["ok"]


@test("count_figures: broken <img> src in html fails", ())
def _():
    d = mk("c2"); p = w(d, "o.html", "<html><body><img src='nope.png'></body></html>")
    raises("count", lambda: G["dg_count_figures"](p, expected=1))


@test("count_figures: pdf per-page counts and mismatch", ())
def _():
    d = mk("c3"); os.makedirs(os.path.join(d, "figures"), exist_ok=True); png(os.path.join(d, "figures", "a.png"))
    r = G["dg_build_tex"](w(d, "main.tex", GOOD_TEX))
    c = G["dg_count_figures"](r["pdf"], expected=1, expected_captions=1)
    assert c["per_page"] == [1] and c["captions"] == 1
    raises("count", lambda: G["dg_count_figures"](r["pdf"], expected=1, expected_captions=2), "captions")


# ---------------------------------------------------------------- page ink
@test("page_ink: ink at page edge and blank page flagged; centred ink passes", ())
def _():
    from matplotlib.backends.backend_pdf import PdfPages
    d = mk("p1")
    for name, xy, txt in (("edge", (0.0, 0.5), "A very long line of code that runs to the page edge " * 3), ("ok", (0.3, 0.5), "centred"), ("blank", None, "")):
        fig = plt.figure(figsize=(8.27, 11.69))
        if xy:
            fig.text(xy[0], xy[1], txt, fontsize=12)
        fig.savefig(os.path.join(d, name + ".pdf")); plt.close(fig)
    raises("build", lambda: G["dg_page_ink_check"](os.path.join(d, "edge.pdf")), "ink_near_edge")
    raises("build", lambda: G["dg_page_ink_check"](os.path.join(d, "blank.pdf")), "blank_page")
    assert G["dg_page_ink_check"](os.path.join(d, "ok.pdf"))["ok"]
    assert G["dg_page_ink_check"](os.path.join(d, "blank.pdf"), allow_blank_pages=[1])["ok"]


# -------------------------------------------------------------- figure QA
@test("figure_qa: blank PNG (opaque and transparent) fails; sparse real plot passes", ())
def _():
    d = mk("q1")
    from PIL import Image
    Image.new("RGB", (800, 600), (255, 255, 255)).save(os.path.join(d, "white.png"))
    Image.new("RGBA", (800, 600), (0, 0, 0, 0)).save(os.path.join(d, "transp.png"))
    ex = raises("qa", lambda: G["dg_figure_qa"]([os.path.join(d, "white.png"), os.path.join(d, "transp.png")]))
    assert [x["kind"] for x in ex.result["problems"]].count("blank") == 2
    assert G["dg_figure_qa"]([png(os.path.join(d, "ok.png"), 1)])["ok"]


@test("figure_qa: near-duplicate and identical figures flagged, distinct figures pass", ())
def _():
    d = mk("q2")
    a = png(os.path.join(d, "a.png"), 1)
    fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
    rng = np.random.default_rng(1); x, y = rng.normal(size=40), rng.normal(size=40)
    ax.scatter(x, y); ax.scatter([0.1], [0.1], s=8, color="k")      # one extra point
    fig.savefig(os.path.join(d, "a2.png")); plt.close(fig)
    shutil.copy(a, os.path.join(d, "a_copy.png"))
    b = png(os.path.join(d, "b.png"), 7, "bars")
    c = png(os.path.join(d, "c.png"), 9, "scatter")                  # same layout, different data
    ex = raises("qa", lambda: G["dg_figure_qa"]([a, os.path.join(d, "a2.png"), os.path.join(d, "a_copy.png"), b, c]))
    types = sorted(x["type"] for x in ex.result["duplicates"])
    assert "identical" in types and "near_duplicate" in types, types
    pairs = {frozenset((os.path.basename(x["a"]), os.path.basename(x["b"]))) for x in ex.result["duplicates"]}
    assert frozenset(("a.png", "c.png")) not in pairs and frozenset(("a.png", "b.png")) not in pairs, pairs
    assert G["dg_figure_qa"]([a, b, c])["ok"]
    assert G["dg_figure_qa"]([a, os.path.join(d, "a2.png")], allow_duplicates=[("a.png", "a2.png")])["ok"]


@test("figure_qa: low resolution and zero-image input fail loudly", ())
def _():
    d = mk("q3")
    p = png(os.path.join(d, "tiny.png"), 1, size=(1, 0.8))
    raises("qa", lambda: G["dg_figure_qa"]([p]), "low_resolution")
    raises("qa", lambda: G["dg_figure_qa"]([]), "zero images")
    raises("qa", lambda: G["dg_figure_qa"](os.path.join(d, "nomatch*.png")), "zero images")
    raises("qa", lambda: G["dg_figure_qa"]([os.path.join(d, "missing.png")]), "does not exist")


# ----------------------------------------------------------- overlap check
@test("overlap: legend over data (legend collision) fails", ())
def _():
    fig, ax = plt.subplots(figsize=(5, 4), dpi=100)
    x = np.linspace(0, 1, 50)
    ax.plot(x, x, label="model A"); ax.plot(x, x ** 2, label="model B")
    ax.legend(loc="center")           # sits on the curves
    ex = raises("overlap", lambda: G["dg_overlap_check"](fig), "legend_over_data")
    plt.close(fig)


@test("overlap: overlapping annotations / title vs ylabel / text outside figure", ())
def _():
    fig, ax = plt.subplots(figsize=(5, 4), dpi=100)
    ax.plot([0, 1], [0, 1])
    ax.text(0.5, 0.5, "p = 0.003 (MWU)", fontsize=12)
    ax.text(0.52, 0.5, "p = 0.04 (t-test)", fontsize=12)
    ax.set_title("A title that is far too long " * 6, fontsize=12)
    ax.annotate("X_hat", xy=(1, 1), xytext=(1.25, 1.0), textcoords="axes fraction")
    ex = raises("overlap", lambda: G["dg_overlap_check"](fig))
    kinds = {e["kind"] for e in ex.result["errors"]}
    assert "text_overlap" in kinds and "text_outside_figure" in kinds, kinds
    plt.close(fig)


@test("overlap: crowded unrotated x tick labels overlap; rotated 45deg labels do not (false-positive guard)", ())
def _():
    labels = ["condition_%d_long_name" % i for i in range(10)]
    fig, ax = plt.subplots(figsize=(4, 3), dpi=100); ax.bar(range(10), range(10)); ax.set_xticks(range(10)); ax.set_xticklabels(labels)
    raises("overlap", lambda: G["dg_overlap_check"](fig), "text_overlap"); plt.close(fig)
    fig, ax = plt.subplots(figsize=(6, 4), dpi=100); ax.bar(range(10), range(10)); ax.set_xticks(range(10))
    ax.set_xticklabels(labels, rotation=45, ha="right"); fig.tight_layout()
    assert G["dg_overlap_check"](fig)["ok"]; plt.close(fig)


@test("overlap: clean figure with legend outside data passes; reference line is only a warning", ())
def _():
    fig, ax = plt.subplots(figsize=(5, 4), dpi=100, layout="constrained")
    x = np.linspace(0, 1, 50)
    ax.plot(x, x, label="A"); ax.plot(x, 0.5 * x, label="B"); ax.axhline(0.9, color="k")
    ax.set_xlabel("x"); ax.set_ylabel("y"); ax.set_title("Clean")
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0))
    r = G["dg_overlap_check"](fig)
    assert r["ok"] and r["n_legends"] == 1, r; plt.close(fig)


@test("overlap: legend over a neighbouring subplot fails", ())
def _():
    fig, axs = plt.subplots(1, 2, figsize=(6, 3), dpi=100)
    axs[0].plot([0, 1], [0, 1], label="long legend label one"); axs[0].plot([0, 1], [1, 0], label="long legend label two")
    axs[1].plot([0, 1], [0, 1])
    axs[0].legend(loc="upper left", bbox_to_anchor=(0.8, 1.0))
    raises("overlap", lambda: G["dg_overlap_check"](fig), "legend_over_axes"); plt.close(fig)


@test("overlap: empty panel from silent column mismatch and missing expected model label", ())
def _():
    fig, axs = plt.subplots(1, 2, figsize=(6, 3), dpi=100)
    axs[0].plot([0, 1], [0, 1], label="scVI (Standard)")
    # axs[1]: filter dropped every row -> nothing plotted
    ex = raises("overlap", lambda: G["dg_overlap_check"](fig, expect_labels=["scVI (Standard)", "scVI (LDVAE)"]))
    kinds = [e["kind"] for e in ex.result["errors"]]
    assert "empty_axes" in kinds and kinds.count("expected_label_missing") == 1, kinds
    plt.close(fig)


@test("overlap: non-strict returns structured result without raising", ())
def _():
    fig, ax = plt.subplots(); ax.text(0.5, 0.5, "aaaa"); ax.text(0.5, 0.5, "bbbb")
    r = G["dg_overlap_check"](fig, strict=False)
    assert (not r["ok"]) and r["errors"][0]["kind"] == "text_overlap"; plt.close(fig)


# ------------------------------------------------------- saved vs built / receipt
@test("verify_saved: marker-vs-filename content mismatch is caught", ())
def _():
    d = mk("v1")
    built = w(d, "built.tex", "\\includegraphics{figures/a.png}\n")
    saved = w(d, "saved.tex", "\\includegraphics{{{artifact:art_0123}}}\n")
    ex = raises("mismatch", lambda: G["dg_verify_saved"](built, saved), "differs")
    assert "artifact" in ex.result["first_diff"]
    shutil.copy(built, os.path.join(d, "copy.tex"))
    assert G["dg_verify_saved"](built, os.path.join(d, "copy.tex"))["ok"]
    raises("mismatch", lambda: G["dg_verify_saved"](built, os.path.join(d, "nope.tex")))


class FakeHost:
    GOOD = "11111111-2222-4333-8444-555555555555"

    def artifacts(self, version_id=None, **kw):
        if version_id == self.GOOD:
            return {"artifacts": [{"filename": "results.csv", "latest_version_id": self.GOOD}]}
        return {"artifacts": []}

    def artifact_path(self, vid):
        raise FileNotFoundError(vid)


@test("artifact refs: fabricated / hash-prefix / mislabeled artifact ids fail", ())
def _():
    fh = FakeHost()
    V = G["dg_verify_artifact_refs"]
    assert V("[results.csv]({{artifact:%s}})" % fh.GOOD, host_obj=fh)["ok"]
    raises("source", lambda: V("[x.csv]({{artifact:f94706ee9ab3}})", host_obj=fh), "not a full UUID")
    raises("source", lambda: V("[x.csv]({{artifact:aaaaaaaa-2222-4333-8444-555555555555}})", host_obj=fh), "does not resolve")
    raises("source", lambda: V("[cost model]({{artifact:%s}})" % fh.GOOD, host_obj=fh), "label")
    assert V("![any caption]({{artifact:%s}})" % fh.GOOD, host_obj=fh)["ok"]


@test("receipt: failed check raises, passing check writes json with hashes", ())
def _():
    d = mk("r1"); p = w(d, "a.tex", "x")
    raises("error", lambda: G["dg_receipt"](p, checks={"figures": {"ok": False, "problems": ["no"]}}), "failed checks")
    rec = G["dg_receipt"](p, checks={"src": {"ok": True}}, out_path=os.path.join(d, "r.json"))
    assert json.load(open(os.path.join(d, "r.json")))["source"]["sha256"] == G["dg_sha256"](p) and rec["all_checks_ok"]


if __name__ == "__main__":
    npass = sum(1 for r in RESULTS if r[2]); nfail = len(RESULTS) - npass
    out = {"passed": npass, "failed": nfail, "tests": [{"name": n, "incidents": list(i), "passed": ok, "error": e} for n, i, ok, e in RESULTS]}
    json.dump(out, open("doc-build-gate_test_results.json", "w"), indent=1)
    for n, i, ok, e in RESULTS:
        print(("PASS " if ok else "FAIL ") + n + ("" if ok else "  <- " + e))
    print("passed=%d failed=%d" % (npass, nfail))
    shutil.rmtree(TMP, ignore_errors=True)
