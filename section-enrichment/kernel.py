"""Sidecar helpers for the section-enrichment skill.

enrich_compile_check(tex_path, ...) wraps a LaTeX fragment in a standalone shim,
compiles it with tectonic (-Z shell-escape for minted), and optionally renders
pages to PNG so the agent can eyeball label collisions / overfull boxes before
saving. Stdlib only; shells out to `tectonic` and `pdftoppm`.
"""
import os
import re
import subprocess

# Default shim preamble: book class + the packages enrichment commonly needs,
# plus \providecommand shims so a fragment with \citep/\citet/\cite/\todo builds
# standalone. Escape-safe: fragment supplies its own body.
ENRICH_SHIM_HEAD = r"""\documentclass[11pt]{book}
\usepackage{amsmath,amssymb}
\usepackage{tabularx,booktabs,array}
\usepackage{graphicx}
\usepackage{tikz}
\usetikzlibrary{arrows.meta,positioning,fit,backgrounds}
\usepackage{minted}
\usepackage{algorithm}
\usepackage{algpseudocode}
\usepackage{xcolor}
\providecommand{\citep}[1]{[#1]}
\providecommand{\citet}[1]{[#1]}
\providecommand{\cite}[1]{[#1]}
\newcommand{\todo}[1]{\marginpar{\footnotesize TODO}}
\begin{document}
"""


def resolve_artifact_markers(body, artifact_map):
    """Replace {{artifact:art_<id>}} with a local path tectonic can read."""
    amap = dict(artifact_map or {})
    pat = re.compile(r"\{\{artifact:art_([0-9a-fA-F-]+)\}\}")

    def repl(m):
        aid = m.group(1)
        if aid in amap:
            return amap[aid]
        h = globals().get("host")
        if h is not None:
            try:
                return h.artifact_path(aid)
            except Exception:
                pass
        return m.group(0)

    return pat.sub(repl, body)


def enrich_compile_check(tex_path, outdir="enrichout", render_pages=None,
                         dpi=130, extra_preamble=None, shell_escape=True,
                         artifact_map=None):
    """Wrap a .tex fragment in a standalone shim, compile with tectonic, render.

    Parameters
    ----------
    tex_path : str
        Path to the fragment (a section body, not a full document).
    outdir : str
        Workspace dir for the built PDF and PNGs (created if missing).
    render_pages : list[int] | None
        1-indexed pages to rasterize to PNG. None renders nothing (compile-only).
    dpi : int
        Resolution for pdftoppm.
    extra_preamble : str | None
        Extra lines inserted before \\begin{document} (e.g. an extra
        \\usetikzlibrary the fragment needs).
    shell_escape : bool
        Pass -Z shell-escape (required for minted). Default True.

    Returns
    -------
    dict with keys: ok (bool), pdf (path or None), pngs (list of paths),
        log_tail (last lines of the tectonic log on failure), errors (list).

    Example
    -------
    >>> res = enrich_compile_check("vae.tex", render_pages=[2, 3])
    >>> res["ok"], res["pngs"]
    """
    os.makedirs(outdir, exist_ok=True)
    body = open(tex_path, encoding="utf-8").read()
    body = resolve_artifact_markers(body, artifact_map)
    head = ENRICH_SHIM_HEAD
    if extra_preamble:
        head = head.replace(r"\begin{document}",
                            extra_preamble.rstrip() + "\n" + r"\begin{document}")
    test_tex = os.path.join(outdir, "_enrich_test.tex")
    with open(test_tex, "w", encoding="utf-8") as fh:
        fh.write(head + body + "\n\\end{document}\n")

    cmd = ["tectonic"]
    if shell_escape:
        cmd += ["-Z", "shell-escape"]
    cmd += ["-o", outdir, test_tex]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    pdf = os.path.join(outdir, "_enrich_test.pdf")
    ok = proc.returncode == 0 and os.path.exists(pdf)

    errors = []
    log_tail = ""
    if not ok:
        pdf = None
        log = proc.stderr or proc.stdout or ""
        errors = [ln for ln in log.splitlines()
                  if "error" in ln.lower() or ln.strip().startswith("!")]
        log_tail = "\n".join(log.splitlines()[-25:])

    pngs = []
    if ok and render_pages:
        for p in render_pages:
            stem = os.path.join(outdir, "pg")
            r = subprocess.run(
                ["pdftoppm", "-png", "-r", str(dpi), "-f", str(p), "-l", str(p),
                 pdf, stem],
                capture_output=True, text=True)
            if r.returncode == 0:
                cand = [f"{stem}-{p}.png", f"{stem}-{p:02d}.png",
                        f"{stem}-{p:03d}.png"]
                pngs += [c for c in cand if os.path.exists(c)]

    return {"ok": ok, "pdf": pdf, "pngs": pngs,
            "log_tail": log_tail, "errors": errors}
