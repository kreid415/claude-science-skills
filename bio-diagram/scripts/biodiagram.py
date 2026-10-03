"""biodiagram: editable, publication-grade biological diagrams from open icon libraries.

Workflow: search/fetch icons (Bioicons first, SciDraw via Zenodo as fallback), each
fetch writing a provenance sidecar; lay the figure out in point units with Diagram;
check() for overlaps / legibility; preview(); export() to SVG + PDF + 300-dpi PNG;
write_attribution() for every asset used.

Units: the SVG viewBox is in typographic points (1 unit = 1 pt = 1/72 in), so font
sizes and stroke widths in the code equal their printed size.
Dependencies: lxml, cairosvg (brings tinycss2/cssselect2), pillow, pypdf, fontconfig (fc-match).
"""
import os, re, io, json, csv, math, copy, hashlib, datetime, subprocess, urllib.request, urllib.parse

__version__ = "1.0.0"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
INK_NS = "http://www.inkscape.org/namespaces/inkscape"
XML_NS = "http://www.w3.org/XML/1998/namespace"
BIOICONS_REPO = "duerrsimon/bioicons"
UA = {"User-Agent": "biodiagram/1.0 (scientific figure tool)"}

LICENSES = {
    "cc-0": ("CC0 1.0 Public Domain Dedication", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "cc-by-3.0": ("CC BY 3.0 Unported", "https://creativecommons.org/licenses/by/3.0/"),
    "cc-by-4.0": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
    "cc-by-sa-3.0": ("CC BY-SA 3.0 Unported", "https://creativecommons.org/licenses/by-sa/3.0/"),
    "cc-by-sa-4.0": ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
    "mit": ("MIT License", "https://opensource.org/licenses/MIT"),
    "bsd": ("BSD License (variant as stated by the original author)", "https://opensource.org/licenses/BSD-3-Clause"),
}
LICENSE_RANK = {"cc-0": 0, "mit": 1, "bsd": 1, "cc-by-4.0": 2, "cc-by-3.0": 2, "cc-by-sa-4.0": 5, "cc-by-sa-3.0": 5}

# Okabe-Ito colour-blind-safe palette + neutrals
PALETTE = {"black": "#000000", "orange": "#E69F00", "skyblue": "#56B4E9", "green": "#009E73",
           "yellow": "#F0E442", "blue": "#0072B2", "vermillion": "#D55E00", "purple": "#CC79A7",
           "grey": "#7F7F7F", "lightgrey": "#E6E6E6", "panel": "#F4F6F8"}
STYLE = {"font_family": "Arial, Helvetica, 'Liberation Sans', sans-serif", "font_match": "Liberation Sans",
         "title_pt": 9, "label_pt": 7, "small_pt": 6, "letter_pt": 10, "min_pt": 6,
         "text_color": "#1A1A1A", "arrow_color": "#333333", "arrow_pt": 1.0,
         "head_len": 5.0, "head_w": 4.0, "line_spacing": 1.2, "gap": 2.5}


# ----------------------------------------------------------------------------- fetching
def _get(url, binary=False, token=None, timeout=60):
    h = dict(UA)
    if token:
        h["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bioicons_index(cache_dir="fig_assets/_cache", refresh=False):
    """Index of every Bioicons SVG, pinned to the current commit of duerrsimon/bioicons.
    Paths come from the git tree (authoritative); display author from icons.json; URLs from authors.json."""
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, "bioicons_index.json")
    if os.path.exists(cache) and not refresh:
        return json.load(open(cache))
    tok = os.environ.get("GITHUB_TOKEN")
    sha = json.loads(_get(f"https://api.github.com/repos/{BIOICONS_REPO}/commits/main", token=tok))["sha"]
    tree = json.loads(_get(f"https://api.github.com/repos/{BIOICONS_REPO}/git/trees/{sha}?recursive=1", token=tok))
    raw = f"https://raw.githubusercontent.com/{BIOICONS_REPO}/{sha}/static/icons/"
    meta = json.loads(_get(raw + "icons.json"))
    authors = json.loads(_get(raw + "authors.json"))
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    by_key = {(m["license"].lower(), m["category"].lower(), m["name"].lower()): m for m in meta}
    author_url = {norm(k): v for k, v in authors.items()}
    out = []
    for t in tree["tree"]:
        p = t["path"]
        if not (p.startswith("static/icons/") and p.endswith(".svg")):
            continue
        parts = p.split("/")
        if len(parts) != 6:
            continue
        lic, cat, adir, fn = parts[2], parts[3], parts[4], parts[5][:-4]
        m = by_key.get((lic.lower(), cat.lower(), fn.lower()), {})
        author = m.get("author", adir).replace("_", " ")
        out.append({"source": "Bioicons", "name": fn, "category": cat, "license": lic, "author": author,
                    "author_url": author_url.get(norm(m.get("author", adir)), ""), "repo_path": p,
                    "download_url": f"https://raw.githubusercontent.com/{BIOICONS_REPO}/{sha}/{urllib.parse.quote(p)}",
                    "source_url": f"https://github.com/{BIOICONS_REPO}/blob/{sha}/{urllib.parse.quote(p)}",
                    "commit": sha})
    json.dump(out, open(cache, "w"), indent=0)
    return out


def _score(q, *fields):
    toks = [t for t in re.split(r"[\s_\-]+", q.lower()) if t]
    hay = [re.split(r"[\s_\-]+", f.lower()) for f in fields]
    s = 0.0
    for t in toks:
        for w, words in zip((3, 1, 0.5), hay):
            if t in words:
                s += w
            elif len(t) >= 3 and any(t in x for x in words):
                s += w * 0.5
    return s


def search_bioicons(query, index=None, licenses=None, categories=None, k=15, allow_share_alike=False):
    """Rank icons by name/category match; permissive licences first. Share-alike excluded unless allowed."""
    index = index or bioicons_index()
    hits = []
    for e in index:
        if licenses and e["license"] not in licenses:
            continue
        if not allow_share_alike and "-sa-" in e["license"]:
            continue
        if categories and e["category"] not in categories:
            continue
        s = _score(query, e["name"], e["category"], e["author"])
        if s > 0:
            hits.append((s, -LICENSE_RANK.get(e["license"], 9), e))
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    return [h[2] for h in hits[:k]]


def search_scidraw(query, k=10):
    """SciDraw drawings are deposited on Zenodo with DOIs; search there (scidraw.io itself may be blocked)."""
    q = f"description:scidraw AND title:({query})"
    url = "https://zenodo.org/api/records?" + urllib.parse.urlencode(
        {"q": q, "type": "image", "subtype": "drawing", "size": max(k * 2, 20)})
    res = []
    for h in json.loads(_get(url))["hits"]["hits"]:
        m = h["metadata"]
        files = [f for f in h.get("files", []) if not re.search(r"\.(png|jpe?g|tiff?|gif|pptx?|pdf)$", f["key"], re.I)]
        if not files:
            continue
        creators = []
        for c in m.get("creators", []):
            n = c["name"].strip(" ,")
            if "," in n:
                last, first = [x.strip() for x in n.split(",", 1)]
                n = (first + " " + last).strip()
            creators.append(n)
        lic = (m.get("license") or {}).get("id", "unknown").lower()
        doi = h.get("doi", "")
        res.append({"source": "SciDraw (via Zenodo)", "name": m["title"], "category": "", "license": lic,
                    "author": "; ".join(creators) or "SciDraw contributor", "author_url": "", "doi": doi,
                    "source_url": f"https://doi.org/{doi}" if doi else h["links"]["self_html"],
                    "download_url": files[0]["links"]["self"], "file_key": files[0]["key"], "record_id": h["id"]})
        if len(res) >= k:
            break
    return res


def fetch_asset(entry, dest="fig_assets/icons"):
    """Download one search hit, sanitize it, save SVG + provenance sidecar (<file>.json). Returns the asset dict."""
    os.makedirs(dest, exist_ok=True)
    data = _get(entry["download_url"], binary=True)
    txt = data.decode("utf-8", "replace")
    if "<svg" not in txt[:20000]:
        raise ValueError(f"{entry['name']}: not an SVG (raster or other format); choose another asset")
    clean = sanitize_svg_text(txt)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{entry['source'].split()[0]}_{entry['name']}").strip("_").lower()
    if entry.get("record_id"):
        slug += f"_{entry['record_id']}"
    path = os.path.join(dest, slug + ".svg")
    open(path, "w").write(clean)
    lic = entry["license"]
    asset = dict(entry, file=path, sha256_original=hashlib.sha256(data).hexdigest(),
                 sha256_file=hashlib.sha256(clean.encode()).hexdigest(), retrieved=_now(),
                 license_name=LICENSES.get(lic, (lic, ""))[0], license_url=LICENSES.get(lic, ("", ""))[1],
                 embedded_raster="<image" in clean, share_alike="-sa-" in lic)
    json.dump(asset, open(path + ".json", "w"), indent=1)
    return asset


def load_asset(path):
    """Reload an asset dict from its provenance sidecar (used by figure scripts; no network)."""
    return json.load(open(path + ".json"))


# ----------------------------------------------------------------------------- SVG hygiene
def sanitize_svg_text(txt):
    """Resolve internal DTD entities (Illustrator exports), drop DOCTYPE and external entities,
    scripts, event handlers, editor metadata and external references. Returns SVG text."""
    from lxml import etree
    ents = dict(re.findall(r'<!ENTITY\s+([\w.\-]+)\s+"([^"]*)"\s*>', txt))
    txt = re.sub(r"<!DOCTYPE[^\[>]*(\[.*?\])?\s*>", "", txt, flags=re.S)
    for k, v in ents.items():
        txt = txt.replace(f"&{k};", v)
    txt = re.sub(r"<\?xml[^>]*\?>", "", txt).strip()
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_comments=True, remove_pis=True)
    root = etree.fromstring(txt.encode(), parser)
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        q = etree.QName(el)
        if q.namespace not in (SVG_NS, None) or q.localname in ("script", "foreignObject", "metadata"):
            if el.getparent() is not None:
                el.getparent().remove(el)
            continue
        for a in list(el.attrib):
            qa = etree.QName(a)
            if qa.localname.startswith("on") or qa.namespace not in (None, XLINK_NS, XML_NS):
                del el.attrib[a]
            elif qa.localname == "href" and not el.attrib[a].startswith(("#", "data:")):
                del el.attrib[a]
    return etree.tostring(root, encoding="unicode")


def _inline_css(root):
    """Move <style> rules into style attributes so icons cannot restyle each other and
    every editor (Inkscape, Illustrator, cairo) shows the same thing."""
    import tinycss2, cssselect2
    styles = list(root.iter(f"{{{SVG_NS}}}style"))
    css = "".join(s.text or "" for s in styles)
    for s in styles:
        s.getparent().remove(s)
    if not css.strip():
        return
    matcher = cssselect2.Matcher()
    for r in tinycss2.parse_stylesheet(css, skip_whitespace=True, skip_comments=True):
        if r.type != "qualified-rule":
            continue
        try:
            for sel in cssselect2.compile_selector_list(r.prelude):
                matcher.add_selector(sel, tinycss2.serialize(r.content).strip().rstrip(";"))
        except Exception:
            continue
    for w in cssselect2.ElementWrapper.from_xml_root(root).iter_subtree():
        m = matcher.match(w)
        if m:
            m.sort(key=lambda x: (x[0], x[1]))
            old = w.etree_element.get("style", "")
            w.etree_element.set("style", ";".join(x[3] for x in m) + (";" + old if old else ""))


def _prefix_ids(root, pre):
    ids = {el.get("id") for el in root.iter() if isinstance(el.tag, str) and el.get("id")}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.get("id"):
            el.set("id", pre + el.get("id"))
        el.attrib.pop("class", None)
        for a, v in list(el.attrib.items()):
            if "#" not in v:
                continue
            v2 = re.sub(r"url\(\s*['\"]?#([^)'\"]+)['\"]?\s*\)",
                        lambda m: f"url(#{pre}{m.group(1)})" if m.group(1) in ids else m.group(0), v)
            if a.endswith("href") and v.startswith("#") and v[1:] in ids:
                v2 = "#" + pre + v[1:]
            el.set(a, v2)


def _recolor(root, color):
    keep = {"none", "transparent", "#fff", "#ffffff", "white"}

    def fix(v):
        v = v.strip()
        return v if v.lower() in keep or v.startswith("url(") else color
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for a in ("fill", "stroke"):
            if el.get(a):
                el.set(a, fix(el.get(a)))
        st = el.get("style")
        if st:
            el.set("style", re.sub(r"(fill|stroke)\s*:\s*([^;]+)", lambda m: f"{m.group(1)}:{fix(m.group(2))}", st))


def _viewbox(root):
    vb = root.get("viewBox")
    if vb:
        return [float(x) for x in re.split(r"[\s,]+", vb.strip())]
    num = lambda s: float(re.match(r"[\d.]+", s or "100").group(0))
    return [0.0, 0.0, num(root.get("width")), num(root.get("height"))]


def _tight_box(svg_text, vb):
    """Bounding box of painted pixels, in viewBox coordinates (crops whitespace margins)."""
    import cairosvg
    from PIL import Image
    px = 400
    png = cairosvg.svg2png(bytestring=svg_text.encode(), output_width=px, output_height=max(1, int(px * vb[3] / vb[2])))
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    bb = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if not bb:
        return vb
    sx, sy = vb[2] / im.width, vb[3] / im.height
    return [vb[0] + bb[0] * sx, vb[1] + bb[1] * sy, (bb[2] - bb[0]) * sx, (bb[3] - bb[1]) * sy]


# ----------------------------------------------------------------------------- text metrics
_FONTS = {}


def _font(weight="normal", family=None):
    from PIL import ImageFont
    fam = family or STYLE["font_match"]
    key = (fam, weight)
    if key not in _FONTS:
        spec = fam + (":bold" if weight == "bold" else "")
        f = subprocess.run(["fc-match", "-f", "%{file}", spec], capture_output=True, text=True).stdout.strip()
        _FONTS[key] = ImageFont.truetype(f, 100)
    return _FONTS[key]


def text_box(s, x, y, size, anchor="middle", weight="normal"):
    """(x0, y0, x1, y1) of multi-line text whose first baseline is at y (font metrics via fc-match)."""
    f = _font(weight)
    asc, desc = f.getmetrics()
    lines = s.split("\n")
    w = max(f.getlength(l) for l in lines) * size / 100
    lh = size * STYLE["line_spacing"]
    x0 = x - w / 2 if anchor == "middle" else (x - w if anchor == "end" else x)
    return (x0, y - asc * size / 100, x0 + w, y + (len(lines) - 1) * lh + desc * size / 100)


# ----------------------------------------------------------------------------- geometry
def _inter(a, b, pad=0.0):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def _inside(p, b, pad=0.0):
    return b[0] - pad <= p[0] <= b[2] + pad and b[1] - pad <= p[1] <= b[3] + pad


def _center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _edge_point(box, toward):
    cx, cy = _center(box)
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == dy == 0:
        return (cx, cy)
    hw, hh = (box[2] - box[0]) / 2, (box[3] - box[1]) / 2
    t = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
    return (cx + dx * t, cy + dy * t)


def _side_point(box, side):
    cx, cy = _center(box)
    return {"left": (box[0], cy), "right": (box[2], cy), "top": (cx, box[1]), "bottom": (cx, box[3])}[side]


def _segs_cross(p1, p2, p3, p4):
    d = lambda a, b, c: (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (d(p3, p4, p1) * d(p3, p4, p2) < 0) and (d(p1, p2, p3) * d(p1, p2, p4) < 0)


def _contrast(c1, c2):
    def lum(h):
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    a, b = sorted([lum(c1), lum(c2)], reverse=True)
    return (a + 0.05) / (b + 0.05)


def _f(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


# ----------------------------------------------------------------------------- diagram
class Diagram:
    """Point-unit canvas. Elements live in Inkscape layers: Background, Icons, Connectors, Labels."""

    def __init__(self, width_mm=180, height_mm=90, title="", desc="", style=None, background="#FFFFFF"):
        from lxml import etree
        self.E = etree
        self.st = dict(STYLE, **(style or {}))
        self.wmm, self.hmm = width_mm, height_mm
        self.W, self.H = width_mm / 25.4 * 72, height_mm / 25.4 * 72
        self.root = etree.Element(f"{{{SVG_NS}}}svg", nsmap={None: SVG_NS, "xlink": XLINK_NS, "inkscape": INK_NS})
        for k, v in (("width", f"{width_mm}mm"), ("height", f"{height_mm}mm"), ("version", "1.1"),
                     ("viewBox", f"0 0 {_f(self.W)} {_f(self.H)}")):
            self.root.set(k, v)
        etree.SubElement(self.root, f"{{{SVG_NS}}}title").text = title
        etree.SubElement(self.root, f"{{{SVG_NS}}}desc").text = desc
        self.layers = {}
        for name in ("Background", "Icons", "Connectors", "Labels"):
            g = etree.SubElement(self.root, f"{{{SVG_NS}}}g", id="layer-" + name.lower())
            g.set(f"{{{INK_NS}}}groupmode", "layer")
            g.set(f"{{{INK_NS}}}label", name)
            self.layers[name] = g
        if background:
            self._el("Background", "rect", id="canvas-bg", x="0", y="0", width=_f(self.W), height=_f(self.H), fill=background)
        self.items = {}       # id -> dict(kind, bbox, ...)
        self.assets = {}      # asset file -> asset dict + modifications + used_as
        self.boxes = []

    def _el(self, layer, tag, parent=None, **attrs):
        el = self.E.SubElement(parent if parent is not None else self.layers[layer], f"{{{SVG_NS}}}{tag}")
        for k, v in attrs.items():
            el.set(k if k == "id" else k.replace("_", "-"), str(v))
        return el

    def _uid(self, id):
        if id in self.items:
            raise ValueError(f"duplicate id {id!r}")
        return id

    def bbox(self, id):
        return self.items[id]["bbox"]

    # --- shapes
    def box(self, id, x, y, w, h, fill=None, stroke=None, rx=6, stroke_pt=0.75, dash=None, ellipse=False):
        """Compartment / panel background (cell, nucleus, step frame). Not overlap-checked; used for contrast."""
        fill = fill or PALETTE["panel"]
        kw = dict(fill=fill, stroke=stroke or "none", stroke_width=_f(stroke_pt))
        if dash:
            kw["stroke_dasharray"] = dash
        if ellipse:
            self._el("Background", "ellipse", id=self._uid(id), cx=_f(x + w / 2), cy=_f(y + h / 2), rx=_f(w / 2), ry=_f(h / 2), **kw)
        else:
            self._el("Background", "rect", id=self._uid(id), x=_f(x), y=_f(y), width=_f(w), height=_f(h), rx=_f(rx), **kw)
        self.items[id] = {"kind": "box", "bbox": (x, y, x + w, y + h), "fill": fill}
        self.boxes.append(id)
        return id

    # --- icons
    def icon(self, id, asset, x, y, w=None, h=None, anchor="center", recolor=None, crop=True, flip=False):
        """Place an asset (dict from fetch_asset/load_asset, or its SVG path) to fit inside w x h pt,
        aspect preserved. (x, y) = box centre (anchor='center') or top-left (anchor='topleft')."""
        if isinstance(asset, str):
            asset = load_asset(asset)
        txt = open(asset["file"]).read()
        src = self.E.fromstring(txt.encode())
        vb = _viewbox(src)
        mods = {"scaled"}
        if crop:
            tb = _tight_box(txt, vb)
            if abs(tb[2] - vb[2]) > 0.02 * vb[2] or abs(tb[3] - vb[3]) > 0.02 * vb[3]:
                mods.add("cropped to drawn content")
            vb = tb
        _inline_css(src)
        _prefix_ids(src, id + "-")
        if recolor:
            _recolor(src, recolor)
            mods.add(f"recoloured to {recolor}")
        if flip:
            mods.add("mirrored")
        w = w or h
        h = h or w
        s = min(w / vb[2], h / vb[3])
        dw, dh = vb[2] * s, vb[3] * s
        x0, y0 = (x - dw / 2, y - dh / 2) if anchor == "center" else (x, y)
        sx = -s if flip else s
        tr = f"translate({_f(x0 + (dw if flip else 0))},{_f(y0)}) scale({sx:.5f},{s:.5f}) translate({_f(-vb[0])},{_f(-vb[1])})"
        g = self._el("Icons", "g", id=self._uid(id), transform=tr)
        g.set(f"{{{INK_NS}}}label", f"{asset['name']} ({asset['source']}, {asset['license']})")
        for a in ("fill", "stroke", "style", "opacity"):
            if src.get(a):
                g.set(a, src.get(a))
        for ch in list(src):
            if isinstance(ch.tag, str) and self.E.QName(ch).localname in ("title", "desc"):
                continue
            g.append(ch)
        sizes = []
        for el in g.iter(f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}tspan"):
            fs = el.get("font-size") or (re.search(r"font-size\s*:\s*([\d.]+)", el.get("style", "")) or [None, None])[1]
            if fs and re.match(r"[\d.]+", str(fs)) and (el.text or "").strip():
                sizes.append(float(re.match(r"[\d.]+", str(fs)).group(0)) * s)
        self.items[id] = {"kind": "icon", "bbox": (x0, y0, x0 + dw, y0 + dh), "asset": asset["file"],
                          "inner_text_pt": min(sizes) if sizes else None}
        rec = self.assets.setdefault(asset["file"], dict(asset, modifications=set(), used_as=[]))
        rec["modifications"] |= mods
        rec["used_as"].append(id)
        return id

    # --- text
    def text(self, id, x, y, s, size=None, weight="normal", anchor="middle", color=None, inside=None, italic=False):
        """Live (editable) text; y = first baseline; '\\n' = new line. `inside` = id of an icon the text may sit on."""
        size = size or self.st["label_pt"]
        el = self._el("Labels", "text", id=self._uid(id), x=_f(x), y=_f(y), font_family=self.st["font_family"],
                      font_size=_f(size), text_anchor=anchor, fill=color or self.st["text_color"])
        if weight == "bold":
            el.set("font-weight", "bold")
        if italic:
            el.set("font-style", "italic")
        for i, line in enumerate(s.split("\n")):
            t = self._el("Labels", "tspan", parent=el, x=_f(x))
            if i:
                t.set("dy", _f(size * self.st["line_spacing"]))
            t.text = line
        self.items[id] = {"kind": "text", "bbox": text_box(s, x, y, size, anchor, weight), "size": size,
                          "color": color or self.st["text_color"], "inside": inside, "s": s}
        return id

    def label(self, target, s, side="below", gap=None, size=None, id=None, **kw):
        """Label outside an element's bbox: side in below/above/left/right."""
        b = self.items[target]["bbox"]
        gap = self.st["gap"] if gap is None else gap
        size = size or self.st["label_pt"]
        tb = text_box(s, 0, 0, size, "middle", kw.get("weight", "normal"))
        cx, cy = _center(b)
        if side == "below":
            x, y, anc = cx, b[3] + gap - tb[1], "middle"
        elif side == "above":
            x, y, anc = cx, b[1] - gap - tb[3], "middle"
        elif side == "left":
            x, y, anc = b[0] - gap, cy - (tb[1] + tb[3]) / 2, "end"
        else:
            x, y, anc = b[2] + gap, cy - (tb[1] + tb[3]) / 2, "start"
        return self.text(id or f"{target}-label", x, y, s, size=size, anchor=anc, **kw)

    def panel_letter(self, letter, x, y):
        return self.text(f"panel-{letter}", x, y, letter, size=self.st["letter_pt"], weight="bold", anchor="start")

    # --- connectors
    def arrow(self, src, dst, id=None, kind="flow", label=None, label_side="left", curve=0.0, via=None,
              src_side=None, dst_side=None, color=None, dashed=False, gap=None, width=None):
        """kind: 'flow'/'activation' (filled head), 'inhibition' (T-bar), 'line' (no head).
        src/dst: element ids or (x, y) points; via: waypoints (elbow routing); curve: bow as fraction of length;
        src_side/dst_side: force attachment at left/right/top/bottom. dashed = indirect/hypothesised."""
        if id is None:
            id = f"arrow-{src}-{dst}" if isinstance(src, str) and isinstance(dst, str) else f"arrow-{len(self.items)}"
        id = self._uid(id)
        color = color or self.st["arrow_color"]
        lw = width or self.st["arrow_pt"]
        gap = self.st["gap"] if gap is None else gap
        cen = lambda r: tuple(r) if not isinstance(r, str) else _center(self.items[r]["bbox"])
        via = [tuple(v) for v in (via or [])]

        def endpoint(ref, side, toward):
            if not isinstance(ref, str):
                return tuple(ref), None
            b = self.items[ref]["bbox"]
            return (_side_point(b, side) if side else _edge_point(b, toward)), ref

        def shrink(a, b, d):
            L = math.dist(a, b) or 1
            return (a[0] + (b[0] - a[0]) * d / L, a[1] + (b[1] - a[1]) * d / L)
        p0, s_id = endpoint(src, src_side, via[0] if via else cen(dst))
        p1, d_id = endpoint(dst, dst_side, via[-1] if via else cen(src))
        nxt0, prv1 = (via[0] if via else p1), (via[-1] if via else p0)
        if s_id:
            p0 = shrink(p0, nxt0, gap)
        if d_id:
            p1 = shrink(p1, prv1, gap)
        ctrl = None
        if curve and not via:
            mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
            L = math.dist(p0, p1)
            ctrl = (mx - (p1[1] - p0[1]) / L * curve * L, my + (p1[0] - p0[0]) / L * curve * L)
        last = ctrl or prv1
        ux, uy = p1[0] - last[0], p1[1] - last[1]
        L = math.hypot(ux, uy) or 1
        ux, uy = ux / L, uy / L
        hl, hw = self.st["head_len"] * lw, self.st["head_w"] * lw
        headed = kind in ("flow", "activation")
        end = (p1[0] - ux * hl * 0.8, p1[1] - uy * hl * 0.8) if headed else p1
        g = self._el("Connectors", "g", id=id)
        d = f"M{_f(p0[0])},{_f(p0[1])} "
        d += (f"Q{_f(ctrl[0])},{_f(ctrl[1])} {_f(end[0])},{_f(end[1])}" if ctrl else
              "".join(f"L{_f(v[0])},{_f(v[1])} " for v in via) + f"L{_f(end[0])},{_f(end[1])}")
        sh = self._el("Connectors", "path", parent=g, d=d, fill="none", stroke=color, stroke_width=_f(lw),
                      stroke_linecap="butt", stroke_linejoin="round")
        if dashed:
            sh.set("stroke-dasharray", f"{_f(3 * lw)},{_f(2 * lw)}")
        px, py = -uy, ux
        if headed:
            b = (p1[0] - ux * hl, p1[1] - uy * hl)
            pts = [p1, (b[0] + px * hw / 2, b[1] + py * hw / 2), (b[0] - px * hw / 2, b[1] - py * hw / 2)]
            self._el("Connectors", "path", parent=g, d="M" + " L".join(f"{_f(a)},{_f(c)}" for a, c in pts) + " Z",
                     fill=color, stroke="none")
        elif kind == "inhibition":
            bw = hw * 1.3
            self._el("Connectors", "path", parent=g, fill="none", stroke=color, stroke_width=_f(lw * 1.5),
                     d=f"M{_f(p1[0] + px * bw / 2)},{_f(p1[1] + py * bw / 2)} L{_f(p1[0] - px * bw / 2)},{_f(p1[1] - py * bw / 2)}")
        if ctrl:
            ts = [i / 40 for i in range(41)]
            pts = [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * ctrl[0] + t * t * p1[0],
                    (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * ctrl[1] + t * t * p1[1]) for t in ts]
        else:
            nodes, pts = [p0] + via + [p1], []
            for a, b in zip(nodes, nodes[1:]):
                n = max(2, int(math.dist(a, b) / 2))
                pts += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        self.items[id] = {"kind": "arrow", "pts": pts, "src": s_id, "dst": d_id, "bbox": (min(xs), min(ys), max(xs), max(ys)),
                          "length": sum(math.dist(a, b) for a, b in zip(pts, pts[1:])), "label": None}
        if label:
            i = len(pts) // 2
            m, a, b = pts[i], pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
            tx, ty = b[0] - a[0], b[1] - a[1]
            L = math.hypot(tx, ty) or 1
            nx, ny = ty / L, -tx / L          # 'left' of travel direction (screen coords)
            if label_side == "right":
                nx, ny = -nx, -ny
            size = self.st["small_pt"] + 0.5
            tb = text_box(label, 0, 0, size)
            off = 2.0 + abs(nx) * (tb[2] - tb[0]) / 2 + abs(ny) * (tb[3] - tb[1]) / 2
            cx, cy = m[0] + nx * off, m[1] + ny * off
            self.text(id + "-label", cx, cy - (tb[1] + tb[3]) / 2, label, size=size)
            self.items[id]["label"] = id + "-label"
        return id

    # --- QA
    def check(self, min_pt=None, margin=2.0, verbose=True):
        """Geometric QA. Returns issues; 'error' must be fixed, 'warn' must be looked at and justified."""
        min_pt = min_pt or self.st["min_pt"]
        it, iss = self.items, []
        add = lambda sev, kind, a, b="", detail="": iss.append({"severity": sev, "kind": kind, "a": a, "b": b, "detail": detail})
        texts = [k for k, v in it.items() if v["kind"] == "text"]
        icons = [k for k, v in it.items() if v["kind"] == "icon"]
        arrows = [k for k, v in it.items() if v["kind"] == "arrow"]
        for k, v in it.items():
            b = v["bbox"]
            if b[0] < margin or b[1] < margin or b[2] > self.W - margin or b[3] > self.H - margin:
                add("error", "outside-canvas", k, detail=f"bbox {tuple(round(x, 1) for x in b)} vs canvas {self.W:.0f}x{self.H:.0f} pt")
        for k in texts:
            if it[k]["size"] < min_pt:
                add("error", "font-too-small", k, detail=f"{it[k]['size']} pt < {min_pt} pt")
        for i, a in enumerate(texts):
            for b in texts[i + 1:]:
                if _inter(it[a]["bbox"], it[b]["bbox"], 0.5):
                    add("error", "text-overlap", a, b)
        for t in texts:
            for c in icons:
                if it[t]["inside"] != c and _inter(it[t]["bbox"], it[c]["bbox"], 0.5):
                    add("error", "text-on-icon", t, c)
        for c in icons:
            tp = it[c]["inner_text_pt"]
            if tp is not None and tp < min_pt:
                add("error", "icon-text-illegible", c, detail=f"text inside icon prints at {tp:.1f} pt; pick another icon, delete the text, or label it yourself")
        content = [v["bbox"] for k, v in it.items() if v["kind"] != "box"] + [it[k]["bbox"] for k in self.boxes]
        if content:
            cb = (min(b[0] for b in content), min(b[1] for b in content), max(b[2] for b in content), max(b[3] for b in content))
            spare = {"left": cb[0], "top": cb[1], "right": self.W - cb[2], "bottom": self.H - cb[3]}
            for side, v in spare.items():
                if v > 0.08 * (self.W if side in ("left", "right") else self.H) and v > 12:
                    add("warn", "unused-canvas", side, detail=f"{v:.0f} pt empty; shrink the canvas or rebalance")
        for i, a in enumerate(icons):
            for b in icons[i + 1:]:
                if _inter(it[a]["bbox"], it[b]["bbox"]):
                    add("warn", "icon-overlap", a, b, "intended stacking? otherwise separate")
        for r in arrows:
            A = it[r]
            if A["length"] < 10:
                add("warn", "arrow-short", r, detail=f"{A['length']:.1f} pt")
            for t in texts:
                if t != A["label"] and any(_inside(p, it[t]["bbox"], 1.0) for p in A["pts"]):
                    add("error", "arrow-through-text", r, t)
            for c in icons:
                if c not in (A["src"], A["dst"]) and any(_inside(p, it[c]["bbox"], -1.0) for p in A["pts"]):
                    add("error", "arrow-through-icon", r, c)
        for i, a in enumerate(arrows):
            for b in arrows[i + 1:]:
                pa, pb = it[a]["pts"], it[b]["pts"]
                if any(_segs_cross(p, q, u, v) for p, q in zip(pa, pa[1:]) for u, v in zip(pb, pb[1:])):
                    add("warn", "arrows-cross", a, b)
        for t in texts:
            bg = "#FFFFFF"
            for bx in self.boxes:
                if _inter(it[t]["bbox"], it[bx]["bbox"]) and it[bx]["fill"].startswith("#"):
                    bg = it[bx]["fill"]
            if it[t]["color"].startswith("#") and _contrast(it[t]["color"], bg) < 4.5:
                add("warn", "low-contrast", t, detail=f"{it[t]['color']} on {bg}: {_contrast(it[t]['color'], bg):.1f}:1")
        if verbose:
            e = sum(x["severity"] == "error" for x in iss)
            print(f"check: {e} errors, {len(iss) - e} warnings")
            for x in iss:
                print(f"  [{x['severity']}] {x['kind']}: {x['a']} {x['b']} {x['detail']}".rstrip())
        return iss

    # --- output
    def tostring(self, debug=False):
        root = copy.deepcopy(self.root)
        md = self.E.SubElement(root, f"{{{SVG_NS}}}metadata", id="attribution")
        md.text = json.dumps([{k: a.get(k) for k in ("name", "source", "author", "license", "source_url", "doi")}
                              for a in self.assets.values()])
        if debug:
            g = self.E.SubElement(root, f"{{{SVG_NS}}}g", id="debug")
            for v in self.items.values():
                col = {"text": "#FF0000", "icon": "#0055FF", "arrow": "#00AA00"}.get(v["kind"])
                if col:
                    b = v["bbox"]
                    self.E.SubElement(g, f"{{{SVG_NS}}}rect", x=_f(b[0]), y=_f(b[1]), width=_f(b[2] - b[0]),
                                      height=_f(b[3] - b[1]), fill="none", stroke=col, **{"stroke-width": "0.4"})
        return self.E.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode()

    def save_svg(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        open(path, "w").write(self.tostring())
        return path

    def preview(self, path="preview.png", dpi=150, debug=False):
        """PNG for visual inspection; debug=True overlays the boxes check() used (red text, blue icons, green arrows)."""
        import cairosvg
        cairosvg.svg2png(bytestring=self.tostring(debug=debug).encode(), write_to=path, dpi=dpi)
        return path

    def export(self, basename, outdir="figures", dpi=300):
        """Write <basename>.svg/.pdf/.png (PNG at `dpi` with pHYs metadata) and verify physical sizes."""
        import cairosvg
        from PIL import Image
        os.makedirs(outdir, exist_ok=True)
        svg = self.save_svg(os.path.join(outdir, basename + ".svg"))
        pdf, png = svg[:-4] + ".pdf", svg[:-4] + ".png"
        cairosvg.svg2pdf(url=svg, write_to=pdf)
        cairosvg.svg2png(url=svg, write_to=png, dpi=dpi)
        im = Image.open(png)
        im.load()
        im.save(png, dpi=(dpi, dpi))
        exp = (round(self.wmm / 25.4 * dpi), round(self.hmm / 25.4 * dpi))
        rep = {"svg": svg, "pdf": pdf, "png": png, "png_px": im.size, "png_expected_px": exp,
               "png_ok": all(abs(a - b) <= 1 for a, b in zip(im.size, exp))}
        try:
            from pypdf import PdfReader
            mb = PdfReader(pdf).pages[0].mediabox
            rep["pdf_pt"] = (round(float(mb.width), 1), round(float(mb.height), 1))
            rep["pdf_ok"] = abs(float(mb.width) - self.W) < 1 and abs(float(mb.height) - self.H) < 1
        except ImportError:
            rep["pdf_ok"] = None
        return rep

    def write_attribution(self, outdir="figures", basename="figure"):
        """<basename>_attribution.md (paste-ready credit lines + licence obligations) plus .csv and .json."""
        os.makedirs(outdir, exist_ok=True)
        rows = []
        for a in self.assets.values():
            rows.append({"figure": basename, "asset": a["name"], "used_as": ";".join(a["used_as"]), "source": a["source"],
                         "author": a["author"], "author_url": a.get("author_url", ""), "license": a["license"],
                         "license_name": a.get("license_name", ""), "license_url": a.get("license_url", ""),
                         "source_url": a["source_url"], "doi": a.get("doi", ""), "download_url": a["download_url"],
                         "commit": a.get("commit", ""), "retrieved": a["retrieved"], "sha256_original": a["sha256_original"],
                         "modifications": ", ".join(sorted(a["modifications"])), "local_file": a["file"]})
        base = os.path.join(outdir, basename + "_attribution")
        with open(base + ".csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["asset"])
            w.writeheader()
            w.writerows(rows)
        json.dump(rows, open(base + ".json", "w"), indent=1)
        L = [f"# Attribution: {basename}", "", f"Generated {_now()} by biodiagram {__version__}.", "",
             "## Credit lines (paste into the caption or acknowledgements)", ""]
        for r in rows:
            by = f" by {r['author']}" + (f" ({r['author_url']})" if r["author_url"] else "")
            where = f"https://doi.org/{r['doi']}" if r["doi"] else r["source_url"]
            L.append(f"- \"{r['asset']}\"{by}, {r['source']} ({where}), licensed under {r['license_name']} "
                     f"({r['license_url']}). Changes: {r['modifications'] or 'none'}.")
        lic = {r["license"] for r in rows}
        L += ["", "## Licence obligations", ""]
        if any(l.startswith("cc-by") for l in lic):
            L.append("- CC BY: credit the author, link the licence, and indicate the changes (listed above).")
        if any("-sa-" in l for l in lic):
            L.append("- **CC BY-SA asset present**: the assembled figure must itself be released under CC BY-SA. Confirm with the journal.")
        if lic & {"mit", "bsd"}:
            L.append("- MIT/BSD: keep the copyright and licence notice with redistributed copies (keep this file with the figure).")
        if "cc-0" in lic:
            L.append("- CC0: no attribution required; credit given as good practice.")
        if lic - set(LICENSES):
            L.append(f"- **Unrecognised licence(s) {sorted(lic - set(LICENSES))}**: verify terms on the source page before publication.")
        if any(a.get("embedded_raster") for a in self.assets.values()):
            L.append("- WARNING: at least one asset embeds a raster image; check its resolution at print size.")
        L += ["", "## Full provenance", "",
              "| asset | used as | source | author | licence | retrieved | sha256 (original, first 12) |",
              "|---|---|---|---|---|---|---|"]
        L += [f"| {r['asset']} | {r['used_as']} | [{r['source']}]({r['source_url']}) | {r['author']} | {r['license']} "
              f"| {r['retrieved']} | `{r['sha256_original'][:12]}` |" for r in rows]
        open(base + ".md", "w").write("\n".join(L) + "\n")
        return {"md": base + ".md", "csv": base + ".csv", "json": base + ".json", "n_assets": len(rows)}


def contact_sheet(assets, path="contact_sheet.png", cell=160, cols=5):
    """Render fetched candidate icons in a grid (index, name, source, licence) to choose visually."""
    import cairosvg
    from PIL import Image, ImageDraw
    ims = []
    for a in assets:
        try:
            png = cairosvg.svg2png(url=a["file"], output_width=cell - 20)
            ims.append(Image.open(io.BytesIO(png)).convert("RGBA"))
        except Exception:
            ims.append(Image.new("RGBA", (cell - 20, cell - 20), "#FFDDDD"))
    rows = math.ceil(len(ims) / cols) or 1
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 30)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (im, a) in enumerate(zip(ims, assets)):
        x, y = (i % cols) * cell, (i // cols) * (cell + 30)
        im.thumbnail((cell - 20, cell - 20))
        sheet.paste(im, (x + 10, y + 5), im)
        d.text((x + 4, y + cell - 8), f"{i}: {a['name'][:22]}", fill="black")
        d.text((x + 4, y + cell + 6), f"{a['source'].split()[0]} | {a['license']}", fill="#555555")
    sheet.save(path)
    return path
