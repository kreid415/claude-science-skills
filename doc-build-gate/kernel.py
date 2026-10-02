import os
import re
import sys
import json
import math
import glob
import shutil
import hashlib
import platform
import subprocess
import datetime

DG_EXC = {}
DG_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".pdf", ".eps", ".svg")
DG_RASTER_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff")
DG_TEX_IMG_TRY = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".PDF", ".PNG", ".JPG", ".JPEG")
DG_MARKER_RE = r"\{\{\s*!?\s*artifact\s*:|artifact://|\bart_[0-9a-f]{8}-[0-9a-f]{4}-"
DG_ABS_RE = r"(?<![\w.:/-])(?:/home/|/tmp/|/root/|/mnt/|/workspace\b|/Users/|/var/folders/|/private/tmp|/sandbox)[^\s}\])\"']*|file://\S+|\.claude-science"
DG_FAIL_CLASSES = ("error", "undefined_citation", "undefined_reference", "multiply_defined", "missing_file", "missing_character", "font_substitution", "rerun_needed", "overfull", "underfull")


# ----------------------------------------------------------------- basics
def dg_exc(kind="error"):
    # Exception classes are created lazily (sidecars may not define classes at top level).
    names = {"error": "DgError", "source": "DgSourceError", "build": "DgBuildError", "tool": "DgToolMissing",
             "count": "DgFigureCountError", "qa": "DgQAError", "overlap": "DgOverlapError", "mismatch": "DgMismatchError"}
    if kind not in names:
        raise KeyError("unknown dg exception kind %r; choose from %s" % (kind, sorted(names)))
    if "error" not in DG_EXC:
        def init(self, message="", result=None):
            RuntimeError.__init__(self, message)
            self.result = result
        DG_EXC["error"] = type("DgError", (RuntimeError,), {"__init__": init})
    if kind not in DG_EXC:
        DG_EXC[kind] = type(names[kind], (DG_EXC["error"],), {})
    return DG_EXC[kind]


def dg_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dg_which(name):
    return shutil.which(name) or shutil.which(name, path=os.path.join(sys.prefix, "bin"))


def dg_run(cmd, cwd=None, env=None, timeout=900):
    try:
        return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise dg_exc("build")("timed out after %ss: %s" % (timeout, " ".join(cmd)))


def dg_strip_tex_comments(text):
    return "\n".join(re.sub(r"(?<!\\)%.*$", "", ln) for ln in text.split("\n"))


def dg_lineno(text, pos):
    return text.count("\n", 0, pos) + 1


def dg_summarize(items, n=8):
    out = []
    for it in items[:n]:
        out.append("  - %s:%s [%s] %s" % (os.path.basename(str(it.get("file", ""))), it.get("line", "?"), it.get("kind"), it.get("message")))
    if len(items) > n:
        out.append("  ... and %d more" % (len(items) - n))
    return "\n".join(out)


# ------------------------------------------------------- source checking
def dg_blank_md_code(text):
    out, fence = [], None
    for ln in text.split("\n"):
        m = re.match(r"\s*(`{3,}|~{3,})", ln)
        if fence is None and m:
            fence = m.group(1)[0]
            out.append("")
        elif fence is not None:
            if m and m.group(1)[0] == fence:
                fence = None
            out.append("")
        else:
            out.append(re.sub(r"`[^`\n]*`", lambda k: " " * len(k.group(0)), ln))
    return "\n".join(out)


def dg_check_sources(path, allowed_image_ext=None, min_images=0, check_labels=True, allow=None, allow_dynamic=False, strict=True):
    # Static gate on a build source (.tex/.md/.qmd/.html). Run BEFORE any build.
    from urllib.parse import unquote
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        raise dg_exc("source")("source file not found: %s" % p)
    ext = os.path.splitext(p)[1].lower()
    if ext == ".tex":
        kind = "tex"
    elif ext in (".md", ".markdown", ".qmd", ".rmd"):
        kind = "md"
    elif ext in (".html", ".htm"):
        kind = "html"
    else:
        raise dg_exc("source")("unsupported build source type %r (expected .tex/.md/.qmd/.html)" % ext)
    root = os.path.dirname(p)
    allow_res = [re.compile(a) for a in (allow or [])]
    problems, warnings, images, texts = [], [], [], {}

    def add(lst, k, fp, line, msg, snippet=""):
        if any(r.search(snippet or msg) for r in allow_res):
            return
        lst.append({"kind": k, "file": fp, "line": line, "message": msg})

    queue = [p]
    while queue:
        fp = queue.pop(0)
        if fp in texts:
            continue
        raw = open(fp, encoding="utf-8", errors="replace").read()
        texts[fp] = dg_strip_tex_comments(raw) if kind == "tex" else raw
        if kind == "tex":
            for m in re.finditer(r"\\(?:input|include|subfile)\s*\{([^}]*)\}", texts[fp]):
                tgt = m.group(1).strip()
                if "\\" in tgt or "#" in tgt:
                    continue
                cand = os.path.join(root, tgt)
                if not os.path.isfile(cand) and os.path.isfile(cand + ".tex"):
                    cand += ".tex"
                if os.path.isfile(cand):
                    queue.append(os.path.abspath(cand))
                else:
                    add(problems, "missing_input", fp, dg_lineno(texts[fp], m.start()), "\\input/\\include target not found: %s" % tgt)

    # markers and absolute/sandbox paths (every file, every line)
    for fp, text in texts.items():
        for i, ln in enumerate(text.split("\n"), 1):
            m = re.search(DG_MARKER_RE, ln)
            if m:
                add(problems, "artifact_marker", fp, i, "chat artifact-reference marker left in build source: %r" % ln.strip()[:100], ln)
            m = re.search(DG_ABS_RE, ln)
            if m:
                add(problems, "absolute_path", fp, i, "local/sandbox absolute path in build source: %r" % m.group(0)[:100], ln)

    def check_image(fp, line, target, resolved_ok):
        images.append({"file": fp, "line": line, "target": target, "resolved": resolved_ok})

    def verify_file(fp, line, fpath, target):
        e = os.path.splitext(fpath)[1].lower()
        if e not in DG_IMG_EXT:
            add(problems, "image_no_extension", fp, line, "image %r has no recognised image extension (%r); LaTeX/pandoc cannot infer its type" % (target, e))
            return
        if allowed_image_ext is not None and e not in tuple(x.lower() for x in allowed_image_ext):
            add(problems, "image_format", fp, line, "image %r is %s; allowed: %s" % (target, e, ",".join(allowed_image_ext)))
        if os.path.getsize(fpath) == 0:
            add(problems, "empty_image", fp, line, "image file is 0 bytes: %s" % target)
        elif e in DG_RASTER_EXT:
            try:
                from PIL import Image
                with Image.open(fpath) as im:
                    im.verify()
            except Exception as ex:
                add(problems, "corrupt_image", fp, line, "image does not decode (%s): %s" % (type(ex).__name__, target))

    def classify_target(fp, line, target, base_dirs, tex_mode):
        t = target.strip().strip("<>").strip()
        if t.startswith("data:"):
            check_image(fp, line, "data:...", True)
            return
        if re.match(r"(https?:)?//", t):
            warnings.append({"kind": "remote_image", "file": fp, "line": line, "message": "remote image (build not reproducible offline): %s" % t[:80]})
            check_image(fp, line, t, None)
            return
        if re.search(DG_MARKER_RE, t):
            check_image(fp, line, t, False)
            return
        if tex_mode and re.search(r"[\\#{]", t):
            (warnings if allow_dynamic else problems).append({"kind": "dynamic_image_target", "file": fp, "line": line, "message": "image target is a macro/dynamic string, cannot be verified: %s" % t})
            check_image(fp, line, t, None)
            return
        if t.startswith(("/", "~", "file:")) or re.match(r"[A-Za-z]:[\\/]", t):
            add(problems, "absolute_image_path", fp, line, "image target is an absolute path (breaks on any other machine): %s" % t)
            check_image(fp, line, t, False)
            return
        t0 = t if tex_mode else unquote(re.split(r"[?#]", t)[0])
        found = None
        for b in base_dirs:
            base = os.path.join(b, t0)
            cands = [base] + ([base + e for e in DG_TEX_IMG_TRY] if tex_mode else [])
            for c in cands:
                if os.path.isfile(c):
                    found = c
                    break
            if found:
                break
        if not found:
            add(problems, "missing_image", fp, line, "image target does not resolve relative to the document: %s" % t)
            check_image(fp, line, t, False)
            return
        check_image(fp, line, t, True)
        verify_file(fp, line, found, t)

    for fp, text in texts.items():
        if kind == "tex":
            gp = []
            for m in re.finditer(r"\\graphicspath\s*\{((?:\s*\{[^}]*\}\s*)+)\}", text):
                gp += re.findall(r"\{([^}]*)\}", m.group(1))
            bases = [root] + [os.path.join(root, g) for g in gp]
            for m in re.finditer(r"\\includegraphics\*?\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", text):
                classify_target(fp, dg_lineno(text, m.start()), m.group(1), bases, True)
        else:
            scan = dg_blank_md_code(text) if kind == "md" else text
            defs = {k.lower(): v for k, v in re.findall(r"(?m)^\s*\[([^\]]+)\]:\s*(\S+)", scan)}
            if kind == "md":
                for m in re.finditer(r"!\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)", scan):
                    classify_target(fp, dg_lineno(scan, m.start()), m.group(2), [root], False)
                for m in re.finditer(r"!\[[^\]]*\]\(\s*([^<)\s][^)]*?)\s*\)", scan):
                    if re.search(r"\s", m.group(1)) and not re.match(r"^\S+\s+(\"[^\"]*\"|'[^']*')$", m.group(1)):
                        add(problems, "image_path_space", fp, dg_lineno(scan, m.start()), "image path contains a space and is not <wrapped>: pandoc renders it as literal text (image silently lost): %s" % m.group(1)[:80])
                for m in re.finditer(r"!\[([^\]]*)\]\[([^\]]*)\]", scan):
                    key = (m.group(2) or m.group(1)).lower()
                    if key in defs:
                        classify_target(fp, dg_lineno(scan, m.start()), defs[key], [root], False)
                    else:
                        add(problems, "undefined_image_reference", fp, dg_lineno(scan, m.start()), "reference-style image [%s] has no definition" % key)
            for m in re.finditer(r"<(?:img|source)\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", scan, flags=re.I):
                classify_target(fp, dg_lineno(scan, m.start()), m.group(1), [root], False)

    if len(images) < min_images:
        add(problems, "too_few_images", p, 0, "document references %d images but at least %d expected (figures generated but never embedded?)" % (len(images), min_images))

    if kind == "tex" and check_labels:
        alltext = "\n".join(texts.values())
        labels = re.findall(r"\\label\s*\{([^}]*)\}", alltext)
        seen = set()
        for lb in labels:
            if lb in seen:
                add(problems, "duplicate_label", p, 0, "label defined more than once: %s" % lb)
            seen.add(lb)
        for fp, text in texts.items():
            for m in re.finditer(r"\\(?:[cC]?ref|eqref|pageref|autoref|vref|nameref|labelcref|cpageref|Cpageref)\*?\s*\{([^}]*)\}|\\hyperref\s*\[([^\]]*)\]", text):
                for key in re.split(r"\s*,\s*", (m.group(1) or m.group(2) or "").strip()):
                    if key and not re.search(r"[\\#]", key) and key not in seen:
                        add(problems, "undefined_ref_static", fp, dg_lineno(text, m.start()), "\\ref to label that is not defined in any source file: %s" % key)
        cites = []
        for fp, text in texts.items():
            for m in re.finditer(r"\\[A-Za-z]*cite[A-Za-z]*\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]*)\}", text):
                for key in re.split(r"\s*,\s*", m.group(1).strip()):
                    if key and key != "*" and not re.search(r"[\\#]", key):
                        cites.append((fp, dg_lineno(text, m.start()), key))
        bibfiles = []
        for m in re.finditer(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", alltext):
            for b in re.split(r"\s*,\s*", m.group(1).strip()):
                if b:
                    bibfiles.append(b if b.endswith(".bib") else b + ".bib")
        if cites:
            bibkeys = set()
            for b in bibfiles:
                bp = os.path.join(root, b)
                if os.path.isfile(bp):
                    bibkeys |= set(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", open(bp, encoding="utf-8", errors="replace").read()))
                else:
                    add(problems, "missing_bib", p, 0, "bibliography file not found: %s" % b)
            if not bibfiles and "thebibliography" not in alltext:
                add(problems, "no_bibliography", p, 0, "document cites %d keys but declares no \\bibliography/\\addbibresource" % len(cites))
            elif bibkeys:
                for fp, line, key in cites:
                    if key not in bibkeys:
                        add(problems, "undefined_cite_static", fp, line, "cite key not found in any .bib: %s" % key)

    res = {"ok": not problems, "kind": kind, "path": p, "sha256": dg_sha256(p), "files": list(texts), "images": images,
           "n_images": len(images), "problems": problems, "warnings": warnings}
    if problems and strict:
        raise dg_exc("source")("%d source problem(s) in %s:\n%s" % (len(problems), p, dg_summarize(problems)), res)
    return res


# ------------------------------------------------------------ TeX logs
def dg_parse_tex_log(log, overfull_pt=1.0, underfull_badness=10000, allow=None, fail_on_other=False, strict=True):
    # Classify every warning class in a TeX log. `log` = log text or path to a .log file.
    if "\n" not in log and os.path.isfile(log):
        log = open(log, encoding="utf-8", errors="replace").read()
    raw = log.split("\n")
    lines, i = [], 0
    while i < len(raw):          # undo TeX's 79-column line wrapping
        cur = raw[i]
        while len(cur) == 79 and i + 1 < len(raw):
            i += 1
            cur += raw[i]
        lines.append(cur)
        i += 1
    classes = {}

    def put(cls, idx, text, **kw):
        d = {"line_no": idx + 1, "text": text.strip()[:240]}
        d.update(kw)
        classes.setdefault(cls, []).append(d)

    q = r"[`'\"]?"
    rules = [
        ("undefined_citation", r"Citation\s+" + q + r"(.+?)" + q + r"\s+on page\s+\S+\s+undefined", 1),
        ("undefined_citation", r"Warning--I didn't find a database entry for \"(.+?)\"", 1),
        ("undefined_citation", r"could not be found in the database|There were undefined citations", 0),
        ("undefined_citation", r"(?i)\bcitation\b.*\bundefined\b", 0),
        ("undefined_reference", r"(?:Hyper )?[Rr]eference\s+" + q + r"(.+?)" + q + r"\s+on page\s+\S+\s+undefined", 1),
        ("undefined_reference", r"pdfTeX warning \(dest\): name\{(.+?)\} has been referenced but does not exist", 1),
        ("undefined_reference", r"There were undefined references", 0),
        ("undefined_reference", r"(?i)\breference\b.*\bundefined\b", 0),
        ("multiply_defined", r"Label\s+" + q + r"(.+?)" + q + r"\s+multiply defined", 1),
        ("multiply_defined", r"destination with the same identifier \(name\{(.+?)\}\) has been already used", 1),
        ("multiply_defined", r"There were multiply-defined labels", 0),
        ("missing_file", r"File\s+" + q + r"(.+?)" + q + r"\s+not found", 1),
        ("missing_file", r"I can't find file\s+" + q + r"(.+?)" + q, 1),
        ("missing_file", r"Unable to load picture or PDF file\s+" + q + r"(.+?)" + q, 1),
        ("missing_file", r"failed to open input file\s+" + q + r"(.+?)" + q, 1),
        ("missing_file", r"Unknown graphics extension:\s*(\S+)", 1),
        ("missing_file", r"^No file (\S+\.(?:bbl|bcf|ind|gls))\.?$", 1),
        ("missing_character", r"Missing character: There is no (.+?) in font (.+?)!", 0),
        ("font_substitution", r"Font Warning:|Package fontspec Warning:|Font shape .* undefined|Font shape .* not available", 0),
        ("rerun_needed", r"Rerun to get|Label\(s\) may have changed|Please \(re\)run Biber|Please rerun LaTeX|Rerun LaTeX|rerunfilecheck Warning:", 0),
    ]
    compiled = [(c, re.compile(rx), g) for c, rx, g in rules]
    for idx, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        mo = re.match(r"Overfull \\([hv])box \(([\d.]+)pt too (?:wide|high)\)(.*)", s)
        if mo:
            put("overfull", idx, s, pt=float(mo.group(2)), box=mo.group(1))
            continue
        mu = re.match(r"Underfull \\([hv])box \(badness (\d+)\)(.*)", s)
        if mu:
            put("underfull", idx, s, badness=int(mu.group(2)), box=mu.group(1))
            continue
        if s.startswith("!") or re.match(r"(?:error:|Fatal error|Emergency stop|\S+ Error:)", s) or re.match(r"(?:LaTeX|Package \S+|Class \S+) Error:", s):
            ctx = ""
            for nxt in lines[idx + 1: idx + 8]:
                if re.match(r"l\.\d+", nxt):
                    ctx = nxt.strip()
                    break
            kind_miss = re.search(r"not found|can't find file|Unable to load picture|failed to open input|Unknown graphics extension", s)
            put("missing_file" if kind_miss else "error", idx, s + ((" | " + ctx) if ctx else ""))
            continue
        hit = False
        for cls, rx, g in compiled:
            m = rx.search(s)
            if m:
                put(cls, idx, s, key=(m.group(g) if g else None))
                hit = True
                break
        if not hit and re.search(r"(?:LaTeX|Package \S+|Class \S+|pdfTeX|Hyper)\s+[Ww]arning|^warning:", s):
            put("other_warning", idx, s)

    allowed = set(allow or [])
    failing = {}
    for cls in DG_FAIL_CLASSES:
        items = classes.get(cls, [])
        if cls == "overfull":
            items = [x for x in items if x["pt"] > overfull_pt]
        elif cls == "underfull":
            items = [x for x in items if x["badness"] >= underfull_badness]
        if items and cls not in allowed:
            failing[cls] = len(items)
    if fail_on_other and classes.get("other_warning") and "other_warning" not in allowed:
        failing["other_warning"] = len(classes["other_warning"])
    res = {"ok": not failing, "failing": failing, "counts": {k: len(v) for k, v in classes.items()}, "classes": classes,
           "allowed": sorted(allowed), "thresholds": {"overfull_pt": overfull_pt, "underfull_badness": underfull_badness}}
    if failing and strict:
        raise dg_exc("build")("TeX log has failing warning classes: %s\n%s" % (failing, dg_log_brief(res)), res)
    return res


def dg_log_brief(report, n=3):
    out = []
    for cls in report["failing"]:
        items = report["classes"].get(cls, [])
        if cls == "overfull":
            items = [x for x in items if x["pt"] > report["thresholds"]["overfull_pt"]]
        if cls == "underfull":
            items = [x for x in items if x["badness"] >= report["thresholds"]["underfull_badness"]]
        for it in items[:n]:
            out.append("  - %s (log line %s): %s" % (cls, it["line_no"], it["text"][:140]))
        if len(items) > n:
            out.append("  - %s: ... %d more" % (cls, len(items) - n))
    return "\n".join(out)


# ------------------------------------------------------------ pdf utils
def dg_pdf_pages(pdf):
    try:
        import pypdfium2 as pdfium
        d = pdfium.PdfDocument(pdf)
        n = len(d)
        d.close()
        return n
    except ImportError:
        pass
    try:
        from pypdf import PdfReader
        return len(PdfReader(pdf).pages)
    except ImportError:
        return None


def dg_render_pages(pdf, outdir=None, dpi=100, pages=None):
    # Render PDF pages to PNG so EVERY page can be viewed (verification of 'page 1 of 3' is not verification).
    import pypdfium2 as pdfium
    if not os.path.isfile(pdf):
        raise dg_exc("build")("pdf not found: %s" % pdf)
    outdir = outdir or os.path.join(os.path.dirname(os.path.abspath(pdf)), ".dg_pages")
    os.makedirs(outdir, exist_ok=True)
    d = pdfium.PdfDocument(pdf)
    idx = list(range(len(d))) if pages is None else [p - 1 for p in pages]
    out = []
    for i in idx:
        im = d[i].render(scale=dpi / 72.0).to_pil()
        fp = os.path.join(outdir, "%s_p%02d.png" % (os.path.splitext(os.path.basename(pdf))[0], i + 1))
        im.save(fp)
        out.append(fp)
    d.close()
    if not out:
        raise dg_exc("build")("no pages rendered from %s" % pdf)
    return out


def dg_page_ink_check(pdf, min_margin_pt=18.0, allow_blank_pages=None, strict=True):
    # Independent of TeX warnings (which are silent for minted/verbatim/tables): find ink touching the page edge or blank pages.
    import numpy as np
    import pypdfium2 as pdfium
    d = pdfium.PdfDocument(pdf)
    problems, pages = [], []
    ok_blank = set(allow_blank_pages or [])
    for i in range(len(d)):
        im = np.asarray(d[i].render(scale=1.0).to_pil().convert("L"))
        h, w = im.shape
        ys, xs = np.where(im < 245)
        if len(xs) == 0:
            pages.append({"page": i + 1, "blank": True})
            if (i + 1) not in ok_blank:
                problems.append({"kind": "blank_page", "file": pdf, "line": i + 1, "message": "page %d has no ink" % (i + 1)})
            continue
        m = {"left": float(xs.min()), "right": float(w - 1 - xs.max()), "top": float(ys.min()), "bottom": float(h - 1 - ys.max())}
        pages.append({"page": i + 1, "margins_pt": m})
        for side, v in m.items():
            if v < min_margin_pt:
                problems.append({"kind": "ink_near_edge", "file": pdf, "line": i + 1, "message": "page %d: ink within %.0fpt of %s edge (min %.0fpt): text/figure overflow" % (i + 1, v, side, min_margin_pt)})
    d.close()
    res = {"ok": not problems, "pages": pages, "problems": problems}
    if problems and strict:
        raise dg_exc("build")("page ink check failed:\n%s" % dg_summarize(problems), res)
    return res


# ------------------------------------------------------- figure counting
def dg_count_figures(path, expected=None, min_px=32, exact=True, expected_captions=None, strict=True):
    # Count images actually embedded in a built PDF / HTML / DOCX / PPTX and compare with what was promised.
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        raise dg_exc("count")("file not found: %s" % p)
    ext = os.path.splitext(p)[1].lower()
    res = {"path": p, "kind": ext.lstrip("."), "problems": []}
    if ext == ".pdf":
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
        d = pdfium.PdfDocument(p)
        per, tiny, caps = [], 0, 0
        for i in range(len(d)):
            n = 0
            for o in d[i].get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=15):
                w, h = o.get_px_size()
                if min(w, h) < min_px:
                    tiny += 1
                else:
                    n += 1
            per.append(n)
            txt = d[i].get_textpage().get_text_range()
            caps += len(re.findall(r"(?m)^\s*(?:Figure|Fig\.)\s*\d+\s*[.:|]", txt))
        d.close()
        res.update(count=sum(per), per_page=per, tiny_ignored=tiny, captions=caps, pages=len(per))
    elif ext in (".html", ".htm"):
        import base64
        html = open(p, encoding="utf-8", errors="replace").read()
        emb = loc = rem = bad = 0
        for m in re.finditer(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", html, flags=re.I):
            s = m.group(1)
            if s.startswith("data:"):
                try:
                    payload = s.split(",", 1)[1]
                    ok = len(base64.b64decode(payload + "=" * (-len(payload) % 4))) > 0 if ";base64" in s[:60] else len(payload) > 0
                except Exception:
                    ok = False
                emb += 1 if ok else 0
                bad += 0 if ok else 1
            elif re.match(r"(https?:)?//", s):
                rem += 1
            elif os.path.isfile(os.path.join(os.path.dirname(p), s.split("?")[0])):
                loc += 1
            else:
                bad += 1
                res["problems"].append({"kind": "broken_img", "file": p, "line": 0, "message": "<img src> does not resolve: %s" % s[:80]})
        svg = len(re.findall(r"<svg\b", html, flags=re.I))
        res.update(count=emb + loc + svg, embedded=emb, local=loc, remote=rem, inline_svg=svg, broken=bad,
                   captions=len(re.findall(r"<figcaption", html, flags=re.I)))
        if bad:
            res["problems"].append({"kind": "broken_img", "file": p, "line": 0, "message": "%d <img> tags are empty/undecodable/unresolved" % bad})
    elif ext in (".docx", ".pptx", ".odt"):
        import zipfile
        z = zipfile.ZipFile(p)
        names = z.namelist()
        if ext == ".docx":
            xml = z.read("word/document.xml").decode("utf-8", "replace")
            per = [len(re.findall(r"<pic:pic\b", xml))]
        elif ext == ".pptx":
            slides = sorted([n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)], key=lambda s: int(re.findall(r"\d+", s)[0]))
            per = [len(re.findall(r"<p:pic\b", z.read(n).decode("utf-8", "replace"))) for n in slides]
        else:
            xml = z.read("content.xml").decode("utf-8", "replace")
            per = [len(re.findall(r"<draw:image\b", xml))]
        res.update(count=sum(per), per_unit=per)
    else:
        raise dg_exc("count")("unsupported file type %r (pdf/html/docx/pptx/odt)" % ext)
    c = res["count"]
    if expected is not None and ((exact and c != expected) or (not exact and c < expected)):
        res["problems"].append({"kind": "image_count", "file": p, "line": 0, "message": "found %d embedded images, expected %s%d (figures dropped or replaced by alt text?)" % (c, "" if exact else ">=", expected)})
    if expected_captions is not None and res.get("captions") is not None and res["captions"] != expected_captions:
        res["problems"].append({"kind": "caption_count", "file": p, "line": 0, "message": "found %s figure captions, expected %d" % (res["captions"], expected_captions)})
    res["expected"] = expected
    res["ok"] = not res["problems"]
    if not res["ok"] and strict:
        raise dg_exc("count")("figure count check failed for %s:\n%s" % (p, dg_summarize(res["problems"])), res)
    return res


# ------------------------------------------------------------- TeX build
def dg_tex_engine(engine):
    have = {k: dg_which(k) for k in ("latexmk", "pdflatex", "xelatex", "lualatex", "tectonic")}
    if engine == "auto":
        if have["latexmk"] and (have["pdflatex"] or have["xelatex"] or have["lualatex"]):
            engine = "latexmk"
        elif have["pdflatex"]:
            engine = "pdflatex"
        elif have["xelatex"]:
            engine = "xelatex"
        elif have["lualatex"]:
            engine = "lualatex"
        elif have["tectonic"]:
            engine = "tectonic"
        else:
            raise dg_exc("tool")("no TeX engine found (looked for latexmk, pdflatex, xelatex, lualatex, tectonic). Install one: "
                                 "manage_packages(mode='install', environment='python', packages=['tectonic']) "
                                 "(self-contained, downloads packages on demand) or packages=['texlive-core','latexmk'] from conda-forge. "
                                 "Do not substitute a non-TeX renderer for the document.")
    base = engine.split("-")[0]
    if not have.get(base):
        raise dg_exc("tool")("requested engine %r not installed; install via manage_packages(mode='install', environment='python', packages=[%r])" % (engine, base))
    return engine, have


def dg_build_tex(path, engine="auto", outdir=None, max_runs=5, timeout=900, overfull_pt=1.0, underfull_badness=10000,
                 allow=None, expected_figures=None, check_sources=True, strict=True):
    # Build the EXACT saved .tex (no test copy). Source gate -> compile -> classify ALL log warnings -> receipt.
    p = os.path.abspath(path)
    src = dg_check_sources(p, strict=True) if check_sources else {"files": [p], "sha256": dg_sha256(p), "n_images": None}
    root, stem = os.path.dirname(p), os.path.splitext(os.path.basename(p))[0]
    eng, have = dg_tex_engine(engine)
    outdir = os.path.abspath(outdir or os.path.join(root, ".dg_build"))
    os.makedirs(outdir, exist_ok=True)
    for f in src["files"]:
        os.makedirs(os.path.join(outdir, os.path.dirname(os.path.relpath(f, root))), exist_ok=True)
    pre = {f: dg_sha256(f) for f in src["files"]}
    env = dict(os.environ)
    env["TEXINPUTS"] = root + os.pathsep + env.get("TEXINPUTS", "")
    env["BIBINPUTS"] = root + os.pathsep + env.get("BIBINPUTS", "")
    env["BSTINPUTS"] = root + os.pathsep + env.get("BSTINPUTS", "")
    import time
    t0 = time.time()
    pdf, logp, runs, outputs = os.path.join(outdir, stem + ".pdf"), os.path.join(outdir, stem + ".log"), 0, []

    def auxhash():
        h = hashlib.md5()
        for ext in (".aux", ".toc", ".out", ".lof", ".lot", ".bbl"):
            fp = os.path.join(outdir, stem + ext)
            if os.path.isfile(fp):
                h.update(open(fp, "rb").read())
        return h.hexdigest()

    def readlog():
        t = open(logp, encoding="utf-8", errors="replace").read() if os.path.isfile(logp) else ""
        blg = os.path.join(outdir, stem + ".blg")
        if os.path.isfile(blg):
            t += "\n" + open(blg, encoding="utf-8", errors="replace").read()
        return t

    rc_bad = None
    if eng.startswith("latexmk"):
        flag = {"latexmk": "-pdf", "latexmk-xelatex": "-xelatex", "latexmk-lualatex": "-lualatex"}.get(eng, "-pdf")
        cp = dg_run([have["latexmk"], flag, "-interaction=nonstopmode", "-output-directory=" + outdir, os.path.basename(p)], root, env, timeout)
        runs, outputs = 1, [cp.stdout[-400:], cp.stderr[-400:]]
        rc_bad = cp.returncode if cp.returncode else None
    elif eng == "tectonic":
        cp = dg_run([have["tectonic"], "--keep-logs", "--keep-intermediates", "--outdir", outdir, os.path.basename(p)], root, env, timeout)
        runs, outputs = 1, [cp.stdout[-400:], cp.stderr[-600:]]
        rc_bad = cp.returncode if cp.returncode else None
    else:
        prev, bib_done = None, False
        for runs in range(1, max_runs + 1):
            cp = dg_run([have[eng], "-interaction=nonstopmode", "-output-directory", outdir, os.path.basename(p)], root, env, timeout)
            outputs = [cp.stdout[-400:], cp.stderr[-400:]]
            rc_bad = cp.returncode if cp.returncode else None
            if rc_bad:
                break
            aux = os.path.join(outdir, stem + ".aux")
            auxtxt = open(aux, encoding="utf-8", errors="replace").read() if os.path.isfile(aux) else ""
            if not bib_done and "\\bibdata" in auxtxt:
                if os.path.isfile(os.path.join(outdir, stem + ".bcf")):
                    if not dg_which("biber"):
                        raise dg_exc("tool")("document uses biblatex/biber but 'biber' is not installed; manage_packages(mode='install', environment='python', packages=['biber'])")
                    bcp = dg_run([dg_which("biber"), "--input-directory", outdir, "--output-directory", outdir, stem], root, env, timeout)
                else:
                    if not dg_which("bibtex"):
                        raise dg_exc("tool")("document needs bibtex but it is not installed; install texlive-core via manage_packages or use engine='tectonic'")
                    bcp = dg_run([dg_which("bibtex"), stem], outdir, env, timeout)
                bib_done = True
                if bcp.returncode:
                    raise dg_exc("build")("bibliography tool failed (rc=%s): %s" % (bcp.returncode, (bcp.stdout + bcp.stderr)[-500:]))
                prev = None
                continue
            h = auxhash()
            rep = dg_parse_tex_log(readlog(), overfull_pt, underfull_badness, allow, strict=False)
            if h == prev and not rep["classes"].get("rerun_needed"):
                break
            prev = h
    log = readlog()
    if not log:
        raise dg_exc("build")("engine %s produced no log at %s; output:\n%s" % (eng, logp, "\n".join(outputs)))
    report = dg_parse_tex_log(log, overfull_pt, underfull_badness, allow, strict=False)
    problems = []
    if rc_bad:
        problems.append("engine exit code %s" % rc_bad)
    if not os.path.isfile(pdf) or os.path.getsize(pdf) == 0 or os.path.getmtime(pdf) < t0 - 2:
        problems.append("no fresh non-empty PDF at %s (stale PDF from an earlier build does not count)" % pdf)
    post = {f: dg_sha256(f) for f in src["files"]}
    if post != pre:
        problems.append("source file(s) changed during the build: %s" % [os.path.basename(f) for f in pre if pre[f] != post.get(f)])
    pages = dg_pdf_pages(pdf) if os.path.isfile(pdf) else None
    figs = None
    if expected_figures is not None and os.path.isfile(pdf):
        figs = dg_count_figures(pdf, expected_figures, strict=False)
        if not figs["ok"]:
            problems.append(figs["problems"][0]["message"])
    res = {"ok": not problems and report["ok"], "engine": eng, "runs": runs, "pdf": pdf, "log": logp, "pages": pages,
           "pages_to_view": list(range(1, (pages or 0) + 1)), "sha256_source": src["sha256"], "n_images_in_source": src["n_images"],
           "log_report": report, "figures": figs, "problems": problems}
    res["receipt"] = dg_receipt(p, outputs=[pdf] if os.path.isfile(pdf) else [], checks={"source": src, "tex_log": report, "figures": figs} if figs else {"source": src, "tex_log": report},
                                out_path=os.path.join(outdir, stem + ".build_receipt.json"), strict=False, extra={"engine": eng, "runs": runs, "pages": pages, "build_problems": problems})
    if not res["ok"] and strict:
        raise dg_exc("build")("build of %s FAILED (engine=%s, runs=%d):\n%s%s" % (p, eng, runs, "".join("  - %s\n" % x for x in problems), dg_log_brief(report)), res)
    return res


# ------------------------------------------------------------ markdown build
def dg_build_md(path, to="html", pdf_engine="auto", outdir=None, extra_args=None, allow=None, expected_images=None,
                timeout=600, strict=True):
    # Build the EXACT saved markdown with pandoc; fail if any image is dropped / replaced by alt text.
    p = os.path.abspath(path)
    src = dg_check_sources(p, strict=True)
    if src["kind"] == "html":
        raise dg_exc("source")("dg_build_md expects markdown (.md/.qmd), got html")
    pandoc = dg_which("pandoc")
    if not pandoc:
        raise dg_exc("tool")("pandoc not installed; manage_packages(mode='install', environment='python', packages=['pandoc'])")
    root, stem = os.path.dirname(p), os.path.splitext(os.path.basename(p))[0]
    outdir = os.path.abspath(outdir or os.path.join(root, ".dg_build"))
    os.makedirs(outdir, exist_ok=True)
    allowed = set(allow or [])
    vtxt = dg_run([pandoc, "--version"], root, None, 30).stdout.split("\n")[0]
    vm = re.search(r"(\d+)\.(\d+)", vtxt)
    ver = (int(vm.group(1)), int(vm.group(2))) if vm else (0, 0)
    embed = "--embed-resources" if ver >= (2, 19) else "--self-contained"
    common = ["--resource-path", root] + list(extra_args or [])
    pre = dg_sha256(p)
    problems, warns = [], []

    def classify(stderr, label):
        for ln in stderr.split("\n"):
            if not re.match(r"\[(WARNING|ERROR)\]", ln):
                continue
            if re.search(r"Could not fetch resource|Could not find image|not found in resource path", ln):
                cls = "missing_image"
            elif re.search(r"Missing character", ln):
                cls = "missing_character"
            else:
                cls = "other_warning"
            if cls not in allowed:
                problems.append("[%s/%s] %s" % (label, cls, ln.strip()[:200]))

    probe = os.path.join(outdir, stem + (".html" if to == "html" else ".probe.html"))
    cp = dg_run([pandoc, p, "-t", "html5", "--standalone", embed, "--metadata", "pagetitle=" + stem, "-o", probe] + common, root, None, timeout)
    if cp.returncode:
        raise dg_exc("build")("pandoc (html probe) failed rc=%s: %s" % (cp.returncode, cp.stderr[-600:]))
    classify(cp.stderr, "html")
    n_src = len([i for i in src["images"] if i["resolved"] is True])
    n_expect = n_src if expected_images is None else expected_images
    hc = dg_count_figures(probe, strict=False)
    if hc.get("local") or hc.get("broken"):
        problems.append("html probe: %s <img> not embedded / unresolved (images replaced by alt text or left as paths)" % (hc.get("local", 0) + hc.get("broken", 0)))
    if hc["embedded"] != n_expect and "count_mismatch" not in allowed:
        problems.append("html probe embeds %d images but source references %d (silent image loss)" % (hc["embedded"], n_expect))
    out, figs = probe, hc
    if to != "html":
        ext = {"latex": "tex", "typst": "typ"}.get(to, to)
        out = os.path.join(outdir, stem + "." + ext)
        args = [pandoc, p, "-o", out] + common
        if to == "pdf":
            eng = pdf_engine
            if eng == "auto":
                eng = next((e for e in ("xelatex", "pdflatex", "lualatex", "tectonic", "typst", "weasyprint") if dg_which(e)), None)
                if eng is None:
                    raise dg_exc("tool")("no PDF engine for pandoc (xelatex/pdflatex/lualatex/tectonic/typst/weasyprint). Install: manage_packages(mode='install', environment='python', packages=['tectonic'])")
            elif not dg_which(eng):
                raise dg_exc("tool")("pdf engine %r not installed" % eng)
            if eng in ("typst", "weasyprint"):
                warns.append("non-LaTeX PDF backend %s: LaTeX column widths/landscape/longtable pagination are silently dropped; verify tables visually" % eng)
            args.append("--pdf-engine=" + eng)
        t0 = __import__("time").time()
        env = dict(os.environ)
        env["PATH"] = os.path.join(sys.prefix, "bin") + os.pathsep + env.get("PATH", "")
        cp2 = dg_run(args, root, env, timeout)
        if cp2.returncode:
            raise dg_exc("build")("pandoc -> %s failed rc=%s: %s" % (to, cp2.returncode, cp2.stderr[-800:]))
        classify(cp2.stderr, to)
        if not os.path.isfile(out) or os.path.getsize(out) == 0 or os.path.getmtime(out) < t0 - 2:
            problems.append("no fresh non-empty %s output at %s" % (to, out))
        elif to in ("pdf", "docx", "pptx", "odt"):
            n_raster = len([i for i in src["images"] if i["resolved"] is True and os.path.splitext(i["target"])[1].lower() in DG_RASTER_EXT])
            figs = dg_count_figures(out, expected=(n_raster if to == "pdf" else n_expect), exact=False, strict=False)
            if not figs["ok"]:
                problems.append(figs["problems"][0]["message"])
    if dg_sha256(p) != pre:
        problems.append("source changed during build")
    pages = dg_pdf_pages(out) if to == "pdf" and os.path.isfile(out) else None
    res = {"ok": not problems, "output": out, "to": to, "pages": pages, "pages_to_view": list(range(1, (pages or 0) + 1)),
           "sha256_source": src["sha256"], "n_images_in_source": n_src, "figures": figs, "warnings": warns, "problems": problems, "pandoc": vtxt}
    res["receipt"] = dg_receipt(p, outputs=[out], checks={"source": src, "figures": figs}, out_path=os.path.join(outdir, stem + ".build_receipt.json"),
                                strict=False, extra={"to": to, "pandoc": vtxt, "pages": pages, "build_problems": problems, "warnings": warns})
    if problems and strict:
        raise dg_exc("build")("markdown build of %s FAILED:\n%s" % (p, "".join("  - %s\n" % x for x in problems)), res)
    return res


# ------------------------------------------------------------ receipts
def dg_brief(d):
    if not isinstance(d, dict):
        return d
    keys = ("ok", "kind", "failing", "counts", "n_images", "count", "expected", "pages", "engine")
    out = {k: d[k] for k in keys if k in d}
    if d.get("problems"):
        out["problems"] = [x if isinstance(x, str) else x.get("message") for x in d["problems"][:20]]
    return out


def dg_receipt(source, outputs=None, checks=None, out_path=None, extra=None, strict=True):
    # Build receipt: hashes of the exact source + outputs, per-check verdicts, tool versions, git state. Raises if any check failed.
    sp = os.path.abspath(source)
    if not os.path.isfile(sp):
        raise dg_exc("error")("receipt: source not found %s" % sp)
    rec = {"created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "source": {"path": sp, "sha256": dg_sha256(sp), "bytes": os.path.getsize(sp)},
           "outputs": [], "checks": {}, "python": sys.version.split()[0], "platform": platform.platform(), "tools": {}}
    for o in (outputs or []):
        rec["outputs"].append({"path": os.path.abspath(o), "sha256": dg_sha256(o), "bytes": os.path.getsize(o)})
    for k, v in (checks or {}).items():
        rec["checks"][k] = dg_brief(v)
    for t in ("tectonic", "latexmk", "pdflatex", "xelatex", "pandoc"):
        w = dg_which(t)
        if w:
            try:
                rec["tools"][t] = dg_run([w, "--version"], None, None, 15).stdout.split("\n")[0][:100]
            except Exception:
                rec["tools"][t] = "version-unavailable"
    try:
        d = os.path.dirname(sp)
        head = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if head.returncode == 0:
            st = subprocess.run(["git", "-C", d, "status", "--porcelain", "--", sp], capture_output=True, text=True, timeout=10)
            rec["git"] = {"commit": head.stdout.strip(), "source_dirty": bool(st.stdout.strip())}
        else:
            rec["git"] = None
    except Exception:
        rec["git"] = None
    rec.update(extra or {})
    bad = [k for k, v in (checks or {}).items() if isinstance(v, dict) and not v.get("ok", False)]
    rec["all_checks_ok"] = not bad
    rec["failed_checks"] = bad
    if out_path:
        with open(out_path, "w") as f:
            json.dump(rec, f, indent=2, default=str)
        rec["receipt_path"] = out_path
    if bad and strict:
        raise dg_exc("error")("receipt: failed checks %s" % bad, rec)
    return rec


def dg_verify_saved(built, saved, strict=True):
    # The file you BUILT must be byte-identical to the file you SAVED/delivered (artifact copy, upload, Overleaf zip member).
    for f in (built, saved):
        if not os.path.isfile(f):
            raise dg_exc("mismatch")("file not found: %s" % f)
    a, b = dg_sha256(built), dg_sha256(saved)
    res = {"ok": a == b, "built": built, "saved": saved, "sha256_built": a, "sha256_saved": b, "first_diff": None}
    if a != b:
        try:
            import difflib
            la = open(built, encoding="utf-8").read().split("\n")
            lb = open(saved, encoding="utf-8").read().split("\n")
            d = list(difflib.unified_diff(la, lb, "built", "saved", lineterm="", n=0))
            res["first_diff"] = "\n".join(d[:8])
        except Exception:
            res["first_diff"] = "(binary or non-utf8 files differ)"
        if strict:
            raise dg_exc("mismatch")("saved file differs from the built file (built sha %s.. vs saved %s..). The verification covered different content.\n%s" % (a[:12], b[:12], res["first_diff"]), res)
    return res


# ------------------------------------------------- artifact id verification
def dg_verify_artifact_refs(text, host_obj=None, strict=True):
    # For CHAT prose / report text (where markers belong): every artifact reference must be a real id that
    # resolves in the artifact store, and a link label must name the file it points to. Never reconstruct ids.
    h = host_obj if host_obj is not None else globals().get("host")
    if h is None:
        raise dg_exc("source")("no host object available; call from the python kernel or pass host_obj=host")
    uuid_re = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    problems, resolved = [], []
    for m in re.finditer(r"(?:(!?)\[([^\]]*)\]\(\s*)?\{\{artifact:([^}\s]+)\}\}", text):
        bang, label, raw = m.group(1), m.group(2), m.group(3)
        core = raw[4:] if raw.startswith("art_") else raw
        line = dg_lineno(text, m.start())
        if not re.fullmatch(uuid_re, core):
            problems.append({"kind": "not_uuid", "file": "text", "line": line, "message": "%r is not a full UUID (hash prefix of a storage path, truncated or invented id)" % raw})
            continue
        fname = None
        try:
            hits = h.artifacts(version_id=core).get("artifacts") or []
            if hits:
                fname = hits[0].get("filename")
        except Exception:
            hits = []
        if not hits:
            try:
                h.artifact_path(core)
                fname = ""
            except Exception:
                problems.append({"kind": "unresolved_id", "file": "text", "line": line, "message": "artifact id %s does not resolve in the artifact store (never saved, or fabricated)" % raw})
                continue
        resolved.append({"id": raw, "filename": fname})
        if label is not None and not bang and fname and (fname not in label and label.strip("`* ") not in fname):
            problems.append({"kind": "label_mismatch", "file": "text", "line": line, "message": "link label %r points at artifact whose filename is %r" % (label[:60], fname)})
    res = {"ok": not problems, "checked": len(resolved) + len(problems), "resolved": resolved, "problems": problems}
    if problems and strict:
        raise dg_exc("source")("artifact reference check failed:\n%s" % dg_summarize(problems), res)
    return res


# ------------------------------------------------------------ image QA
def dg_load_rgb(fp):
    from PIL import Image
    im = Image.open(fp)
    im.load()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    return im.convert("RGB")


def dg_hashes(im):
    import numpy as np
    from PIL import Image
    rs = getattr(Image, "Resampling", Image).LANCZOS
    g = im.convert("L")
    a = np.asarray(g.resize((8, 8), rs), dtype=float)
    d = np.asarray(g.resize((9, 8), rs), dtype=float)
    thumb = np.asarray(g.resize((64, 64), rs), dtype=float) / 255.0
    thumb = np.abs(thumb - np.median(thumb))   # ink map relative to this image's own background
    return (a > a.mean()).flatten(), (d[:, 1:] > d[:, :-1]).flatten(), thumb


def dg_figure_qa(png_paths, min_side=300, min_ink_frac=0.001, dup_hamming=6, dup_rel=0.15, allow_duplicates=None, strict=True):
    # Blank / near-uniform, very low resolution, identical and near-duplicate (aHash+dHash+relative ink-map difference) images.
    import numpy as np
    if isinstance(png_paths, str):
        png_paths = sorted(glob.glob(png_paths)) if any(c in png_paths for c in "*?[") else [png_paths]
    paths = list(png_paths)
    if not paths:
        raise dg_exc("qa")("figure QA received zero images: the figure-producing step silently produced nothing")
    allow_pairs = {frozenset(x) for x in (allow_duplicates or [])}
    rows, problems = [], []
    for fp in paths:
        row = {"path": fp}
        if not os.path.isfile(fp):
            problems.append({"kind": "missing", "file": fp, "line": 0, "message": "image file does not exist"})
            continue
        try:
            im = dg_load_rgb(fp)
        except Exception as ex:
            problems.append({"kind": "unreadable", "file": fp, "line": 0, "message": "cannot decode image: %s: %s" % (type(ex).__name__, ex)})
            continue
        arr = np.asarray(im)
        sub = arr[:: max(1, int(math.sqrt(arr.shape[0] * arr.shape[1] / 4e6))), :: max(1, int(math.sqrt(arr.shape[0] * arr.shape[1] / 4e6)))]
        pk = sub[..., 0].astype(np.int64) * 65536 + sub[..., 1].astype(np.int64) * 256 + sub[..., 2]
        vals, cnt = np.unique(pk, return_counts=True)
        modal = int(vals[cnt.argmax()])
        mrgb = np.array([(modal >> 16) & 255, (modal >> 8) & 255, modal & 255])
        ink = float((np.abs(sub.astype(int) - mrgb).max(axis=2) > 8).mean())
        w, h = im.size
        row.update(width=w, height=h, ink_frac=round(ink, 5), std=round(float(sub.std()), 3), sha256=dg_sha256(fp))
        if ink < min_ink_frac or row["std"] < 1.5:
            problems.append({"kind": "blank", "file": fp, "line": 0, "message": "blank / near-uniform image (non-background pixels %.4f%%)" % (ink * 100)})
        elif ink < 0.01:
            row["warning"] = "very sparse content (%.2f%% non-background): check panels are not empty" % (ink * 100)
        if min(w, h) < min_side:
            problems.append({"kind": "low_resolution", "file": fp, "line": 0, "message": "%dx%d px: shorter side below %d" % (w, h, min_side)})
        row["ah"], row["dh"], row["thumb"] = dg_hashes(im)
        rows.append(row)
    dups = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            nm = frozenset((os.path.basename(a["path"]), os.path.basename(b["path"])))
            if nm in allow_pairs:
                continue
            if a["sha256"] == b["sha256"]:
                dups.append({"a": a["path"], "b": b["path"], "type": "identical"})
                continue
            if abs(a["width"] / a["height"] - b["width"] / b["height"]) / (a["width"] / a["height"]) > 0.05:
                continue
            da, dh = int((a["ah"] != b["ah"]).sum()), int((a["dh"] != b["dh"]).sum())
            rel = float(np.abs(a["thumb"] - b["thumb"]).sum() / max(a["thumb"].sum(), b["thumb"].sum(), 1e-9))
            if da <= dup_hamming and dh <= dup_hamming and rel <= dup_rel:
                dups.append({"a": a["path"], "b": b["path"], "type": "near_duplicate", "ahash_dist": da, "dhash_dist": dh, "ink_rel_diff": round(rel, 4)})
    for d in dups:
        problems.append({"kind": d["type"], "file": d["a"], "line": 0, "message": "%s: %s ~ %s %s" % (d["type"], os.path.basename(d["a"]), os.path.basename(d["b"]), {k: v for k, v in d.items() if k.endswith(("dist", "diff"))})})
    for r_ in rows:
        for k in ("ah", "dh", "thumb"):
            r_.pop(k, None)
    res = {"ok": not problems, "n_images": len(paths), "rows": rows, "duplicates": dups, "problems": problems}
    if problems and strict:
        raise dg_exc("qa")("figure QA failed (%d problem(s)):\n%s" % (len(problems), dg_summarize(problems, 12)), res)
    return res


# --------------------------------------------------- matplotlib overlaps
def dg_poly_area(pts):
    a, n = 0.0, len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def dg_poly_clip(subject, clipper):
    # Sutherland-Hodgman; both polygons convex and counter-clockwise.
    out = subject
    n = len(clipper)
    for i in range(n):
        a, b = clipper[i], clipper[(i + 1) % n]
        inp, out = out, []
        if not inp:
            break

        def inside(pt):
            return (b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0]) >= 0

        def inter(p1, p2):
            x1, y1 = p1
            x2, y2 = p2
            x3, y3 = a
            x4, y4 = b
            den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
            if den == 0:
                return p2
            t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
            return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))

        s = inp[-1]
        for e in inp:
            if inside(e):
                if not inside(s):
                    out.append(inter(s, e))
                out.append(e)
            elif inside(s):
                out.append(inter(s, e))
            s = e
    return out


def dg_poly_overlap(p, q):
    p = p if dg_poly_area(p) >= 0 else p[::-1]
    q = q if dg_poly_area(q) >= 0 else q[::-1]
    c = dg_poly_clip(p, q)
    return abs(dg_poly_area(c)) if len(c) >= 3 else 0.0


def dg_text_poly(t, r):
    # Exact (rotated) rectangle of a Text: centre of the rotated bbox == centre of the rotated rectangle.
    from matplotlib.text import Text
    bb = Text.get_window_extent(t, r)
    if not (bb.width > 0 and bb.height > 0):
        return None
    ang = float(t.get_rotation()) % 360.0
    if min(ang % 90.0, 90.0 - ang % 90.0) < 1e-6:
        return [(bb.x0, bb.y0), (bb.x1, bb.y0), (bb.x1, bb.y1), (bb.x0, bb.y1)]
    old = t.get_rotation()
    t.set_rotation(0)
    b0 = Text.get_window_extent(t, r)
    t.set_rotation(old)
    cx, cy = (bb.x0 + bb.x1) / 2.0, (bb.y0 + bb.y1) / 2.0
    th = math.radians(ang)
    c, s = math.cos(th), math.sin(th)
    w, h = b0.width / 2.0, b0.height / 2.0
    return [(cx + dx * c - dy * s, cy + dx * s + dy * c) for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h))]


def dg_collect_texts(fig, r):
    from matplotlib.legend import Legend
    items, legends = [], []

    def add(t, role, owner=None):
        if t is None or not t.get_visible():
            return
        s = t.get_text()
        if not s or not s.strip():
            return
        poly = dg_text_poly(t, r)
        if poly is None:
            return
        items.append({"role": role, "text": s.strip().replace("\n", " ")[:40], "poly": poly, "owner": owner})

    for t in fig.texts:
        add(t, "fig_text")
    for ax in fig.axes:
        if not ax.get_visible():
            continue
        for t in (ax.title, getattr(ax, "_left_title", None), getattr(ax, "_right_title", None)):
            add(t, "title", ax)
        if ax.axison:
            add(ax.xaxis.label, "xlabel", ax)
            add(ax.yaxis.label, "ylabel", ax)
            add(ax.xaxis.get_offset_text(), "offset_text", ax)
            add(ax.yaxis.get_offset_text(), "offset_text", ax)
            for axis, role in ((ax.xaxis, "xtick"), (ax.yaxis, "ytick")):
                lo, hi = sorted(axis.get_view_interval())
                eps = 1e-9 * max(abs(hi - lo), 1e-12)
                for tk in list(axis.get_major_ticks()) + list(axis.get_minor_ticks()):
                    loc = tk.get_loc()
                    if loc < lo - eps or loc > hi + eps:
                        continue
                    add(tk.label1, role, ax)
                    add(tk.label2, role, ax)
        for t in ax.texts:
            add(t, "annotation", ax)
        for c in ax.get_children():
            if isinstance(c, Legend):
                legends.append((c, ax))
    for lg in fig.legends:
        legends.append((lg, None))
    for lg, _ax in legends:
        for t in lg.get_texts():
            add(t, "legend_text", lg)
        add(lg.get_title(), "legend_title", lg)
    return items, legends


def dg_axes_data(ax, r):
    # display-space sample points (lines densified, scatter offsets, collection vertices), reference lines, bar rects, finite-count
    import numpy as np
    from matplotlib.collections import QuadMesh
    from matplotlib.container import BarContainer
    pts, refs, rects, finite = [], [], [], 0
    for ln in ax.lines:
        if not ln.get_visible():
            continue
        v = ln.get_path().vertices
        if len(v) == 0:
            continue
        xy = ln.get_transform().transform(v)
        xy = xy[np.isfinite(xy).all(axis=1)]
        if len(xy) == 0:
            continue
        finite += len(xy)
        arr = xy
        if len(xy) > 1:
            seg = np.diff(xy, axis=0)
            k = int(min(200, max(1, math.ceil(float(np.hypot(seg[:, 0], seg[:, 1]).max()) / 4.0))))
            k = max(1, min(k, int(2e6 // max(1, len(seg)))))
            fr = (np.arange(1, k + 1) / (k + 1.0))[None, :, None]
            arr = np.vstack([xy, (xy[:-1, None, :] + fr * seg[:, None, :]).reshape(-1, 2)])
        is_ref = len(v) == 2 and (v[0, 0] == v[1, 0] or v[0, 1] == v[1, 1])
        (refs if is_ref else pts).append((ln.get_label(), arr))
    for co in ax.collections:
        if not co.get_visible() or isinstance(co, QuadMesh):
            continue
        if type(co).__name__ == "PathCollection":
            offs = np.ma.filled(co.get_offsets(), np.nan)
            xy = co.get_offset_transform().transform(offs) if len(offs) else np.zeros((0, 2))
        else:
            vs = [pth.vertices for pth in co.get_paths() if len(pth.vertices)]
            xy = co.get_transform().transform(np.vstack(vs)) if vs else np.zeros((0, 2))
        xy = xy[np.isfinite(xy).all(axis=1)] if len(xy) else xy
        finite += len(xy)
        if len(xy):
            pts.append((co.get_label(), xy))
    for cont in ax.containers:
        if isinstance(cont, BarContainer):
            for pa in cont.patches:
                bb = pa.get_window_extent(r)
                if bb.width > 0 and bb.height > 0:
                    rects.append(bb)
                    finite += 1
    finite += len([i for i in ax.images if i.get_visible()]) + len(ax.patches)
    finite += len([c for c in ax.collections if isinstance(c, QuadMesh)])
    return pts, refs, rects, finite


def dg_overlap_check(fig, min_frac=0.1, min_px=4.0, check_outside=True, expect_labels=None, ignore=None, strict=True):
    # Draw the live Figure, then report: text-text overlaps (titles, tick labels, annotations, legend entries),
    # legend over data / other axes / text, text or legend outside the canvas, empty axes, missing expected labels.
    import numpy as np
    fig.canvas.draw()
    try:
        r = fig.canvas.get_renderer()
    except AttributeError:
        r = fig._get_renderer()
    skip = set(ignore or [])
    errors, warns = [], []

    def put(sev, kind, a, b, area=None, frac=None):
        if kind in skip:
            return
        d = {"severity": sev, "kind": kind, "a": a, "b": b, "overlap_px2": None if area is None else round(float(area), 1),
             "frac_of_smaller": None if frac is None else round(float(frac), 3)}
        (errors if sev == "error" else warns).append(d)

    items, legends = dg_collect_texts(fig, r)
    desc = lambda it: "%s:%r" % (it["role"], it["text"])
    n = len(items)
    boxes = np.array([[min(x for x, _ in it["poly"]), min(y for _, y in it["poly"]), max(x for x, _ in it["poly"]), max(y for _, y in it["poly"])] for it in items]) if n else np.zeros((0, 4))
    areas = [abs(dg_poly_area(it["poly"])) for it in items]
    for i in range(n):
        if i + 1 >= n:
            break
        bi = boxes[i]
        cand = np.nonzero((boxes[i + 1:, 0] < bi[2]) & (boxes[i + 1:, 2] > bi[0]) & (boxes[i + 1:, 1] < bi[3]) & (boxes[i + 1:, 3] > bi[1]))[0] + i + 1
        for j in cand:
            ov = dg_poly_overlap(items[i]["poly"], items[int(j)]["poly"])
            sm = min(areas[i], areas[int(j)])
            if ov >= min_px and sm > 0 and ov / sm >= min_frac:
                put("error", "text_overlap", desc(items[i]), desc(items[int(j)]), ov, ov / sm)
    figbb = fig.bbox
    if check_outside:
        for it in items:
            xs, ys = [x for x, _ in it["poly"]], [y for _, y in it["poly"]]
            ex = max(figbb.x0 - min(xs), max(xs) - figbb.x1, figbb.y0 - min(ys), max(ys) - figbb.y1)
            if ex > 1.0:
                put("error", "text_outside_figure", desc(it), "figure edge", ex)
    axdata = {}
    for ax in fig.axes:
        if ax.get_visible():
            axdata[ax] = dg_axes_data(ax, r)
    for lg, own in legends:
        if not lg.get_visible():
            continue
        lb = lg.get_window_extent(r)
        lpoly = [(lb.x0, lb.y0), (lb.x1, lb.y0), (lb.x1, lb.y1), (lb.x0, lb.y1)]
        lname = "legend(%s)" % ", ".join(t.get_text() for t in lg.get_texts())[:50]
        if check_outside:
            ex = max(figbb.x0 - lb.x0, lb.x1 - figbb.x1, figbb.y0 - lb.y0, lb.y1 - figbb.y1)
            if ex > 1.0:
                put("error", "legend_outside_figure", lname, "figure edge", ex)
        for it in items:
            if it["owner"] is lg:
                continue
            ov = dg_poly_overlap(lpoly, it["poly"])
            a_it = abs(dg_poly_area(it["poly"]))
            if ov >= min_px and a_it > 0 and ov / a_it >= min_frac:
                put("error", "legend_over_text", lname, desc(it), ov, ov / a_it)
        own_bb = own.get_window_extent(r) if own is not None else None
        for ax2, (pts, refs, rects, _fin) in axdata.items():
            abb = ax2.get_window_extent(r)
            if own is not None and ax2 is not own:
                sib = (abs(abb.x0 - own_bb.x0) < 2 and abs(abb.x1 - own_bb.x1) < 2 and abs(abb.y0 - own_bb.y0) < 2 and abs(abb.y1 - own_bb.y1) < 2)
                if not sib and (ax2.axison or pts or rects):
                    ov = dg_poly_overlap(lpoly, [(abb.x0, abb.y0), (abb.x1, abb.y0), (abb.x1, abb.y1), (abb.x0, abb.y1)])
                    if ov >= min_px:
                        put("error", "legend_over_axes", lname, "axes %r" % ((ax2.get_title() or tuple(round(v, 2) for v in ax2.get_position().bounds)),), ov)
                continue
            for lab, arr in pts:
                m = (arr[:, 0] >= lb.x0) & (arr[:, 0] <= lb.x1) & (arr[:, 1] >= lb.y0) & (arr[:, 1] <= lb.y1) & (arr[:, 0] >= abb.x0) & (arr[:, 0] <= abb.x1) & (arr[:, 1] >= abb.y0) & (arr[:, 1] <= abb.y1)
                if m.any():
                    put("error", "legend_over_data", lname, "data %r (%d points)" % (lab, int(m.sum())))
            for lab, arr in refs:
                m = (arr[:, 0] >= lb.x0) & (arr[:, 0] <= lb.x1) & (arr[:, 1] >= lb.y0) & (arr[:, 1] <= lb.y1) & (arr[:, 0] >= abb.x0) & (arr[:, 0] <= abb.x1) & (arr[:, 1] >= abb.y0) & (arr[:, 1] <= abb.y1)
                if m.any():
                    put("warning", "legend_over_reference_line", lname, "line %r" % lab)
            for bb in rects:
                ov = dg_poly_overlap(lpoly, [(bb.x0, bb.y0), (bb.x1, bb.y0), (bb.x1, bb.y1), (bb.x0, bb.y1)])
                if ov >= min_px:
                    put("error", "legend_over_bars", lname, "bar", ov)
                    break
    for ax2, (pts, refs, rects, fin) in axdata.items():
        if ax2.axison and fin == 0 and not pts and not refs:
            put("error", "empty_axes", "axes %r" % ((ax2.get_title() or tuple(round(v, 2) for v in ax2.get_position().bounds)),), "no finite data drawn (silent filter/column mismatch?)")
    if expect_labels:
        have = [it["text"] for it in items]
        for ax2 in axdata:
            for lab in ax2.get_legend_handles_labels()[1]:
                have.append(str(lab))
        for lg, _ in legends:
            have += [t.get_text() for t in lg.get_texts()]
        for lab in expect_labels:
            if not any(lab in h for h in have):
                put("error", "expected_label_missing", lab, "not found in any text/legend/artist label")
    res = {"ok": not errors, "errors": errors, "warnings": warns, "n_text": n, "n_legends": len(legends), "n_axes": len(axdata)}
    if errors and strict:
        lines = ["  - %s: %s | %s%s" % (e["kind"], e["a"], e["b"], "" if e["overlap_px2"] is None else " (%.0f px^2)" % e["overlap_px2"]) for e in errors[:12]]
        raise dg_exc("overlap")("%d layout defect(s) in figure:\n%s%s" % (len(errors), "\n".join(lines), "\n  ... and %d more" % (len(errors) - 12) if len(errors) > 12 else ""), res)
    return res
