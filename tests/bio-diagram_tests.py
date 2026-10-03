"""Offline tests for bio-diagram (no network). Known-bad layouts must fail check(); the good one must pass.
Run from the repo root: python tests/bio-diagram_tests.py   (needs cairosvg, lxml, pillow, pypdf, fontconfig)."""
import os, sys, json, tempfile, hashlib
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bio-diagram", "scripts"))
import biodiagram as bd

tmp = tempfile.mkdtemp()
os.chdir(tmp)
os.makedirs("fig_assets/icons")

EVIL = """<?xml version="1.0"?>
<!DOCTYPE svg [<!ENTITY ns_svg "http://www.w3.org/2000/svg"><!ENTITY xxe SYSTEM "file:///etc/hostname">]>
<svg xmlns="&ns_svg;" viewBox="0 0 100 100" onload="alert(1)">
<style>.cls-1{fill:#D55E00} path{stroke:#000}</style><script>alert(2)</script>
<defs><linearGradient id="g1"><stop offset="0" stop-color="#fff"/></linearGradient></defs>
<rect class="cls-1" x="10" y="10" width="80" height="80"/><circle cx="50" cy="50" r="10" fill="url(#g1)"/>
<image href="http://example.com/x.png" width="1" height="1"/></svg>"""
TINY_TEXT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect x="0" y="0" width="100" height="100" fill="#56B4E9"/>
<text x="5" y="50" font-size="4">tiny</text></svg>"""


def make_asset(name, svg):
    p = f"fig_assets/icons/{name}.svg"
    clean = bd.sanitize_svg_text(svg)
    open(p, "w").write(clean)
    a = {"source": "Test", "name": name, "license": "cc-by-4.0", "author": "Tester", "author_url": "",
         "source_url": "https://example.org/" + name, "download_url": "https://example.org/" + name, "file": p,
         "retrieved": "2026-01-01T00:00:00Z", "sha256_original": hashlib.sha256(svg.encode()).hexdigest(),
         "license_name": bd.LICENSES["cc-by-4.0"][0], "license_url": bd.LICENSES["cc-by-4.0"][1]}
    json.dump(a, open(p + ".json", "w"))
    return a


fails = 0


def expect(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    fails += not cond


a = make_asset("evil", EVIL)
t = make_asset("tiny", TINY_TEXT)
s = open(a["file"]).read()
expect("script" not in s and "onload" not in s and "ENTITY" not in s and "example.com" not in s, "sanitizer strips script/handlers/entities/external refs")

# good layout
d = bd.Diagram(100, 40)
d.icon("a", a, 40, 55, w=40); d.icon("b", a, 240, 55, w=40)
d.label("a", "Source"); d.label("b", "Target")
d.arrow("a", "b", label="step")
iss = d.check(verbose=False)
expect(not [x for x in iss if x["severity"] == "error"], f"good layout passes ({[x['kind'] for x in iss]})")
svg = d.tostring()
expect("#D55E00" in svg and "cls-1" not in svg and "a-g1" in svg and "b-g1" in svg, "CSS inlined, ids prefixed per icon")
expect("<style" not in svg, "no <style> left to leak between icons")
rep = d.export("good", outdir="out")
expect(rep["png_ok"] and rep["pdf_ok"], f"export sizes verified {rep['png_px']} {rep.get('pdf_pt')}")
att = d.write_attribution("out", "good")
md = open(att["md"]).read()
expect("Tester" in md and "CC BY 4.0" in md and "scaled" in md, "attribution lists author, licence, changes")

# known-bad layouts
def kinds(build):
    d = bd.Diagram(100, 40); build(d); return {x["kind"] for x in d.check(verbose=False) if x["severity"] == "error"}

expect("text-overlap" in kinds(lambda d: (d.text("x", 100, 50, "label one"), d.text("y", 105, 52, "label two"))), "detects text-text overlap")
expect("text-on-icon" in kinds(lambda d: (d.icon("a", a, 100, 55, w=40), d.text("x", 100, 57, "on icon"))), "detects text on icon")
expect("arrow-through-icon" in kinds(lambda d: (d.icon("a", a, 30, 55, w=30), d.icon("m", a, 140, 55, w=30), d.icon("b", a, 250, 55, w=30), d.arrow("a", "b"))), "detects arrow through icon")
expect("arrow-through-text" in kinds(lambda d: (d.text("x", 140, 58, "in the way"), d.arrow((20, 55), (260, 55)))), "detects arrow through text")
expect("font-too-small" in kinds(lambda d: d.text("x", 100, 50, "small", size=4)), "detects font below floor")
expect("outside-canvas" in kinds(lambda d: d.text("x", 2, 50, "clipped label", anchor="middle")), "detects text outside canvas")
expect("icon-text-illegible" in kinds(lambda d: d.icon("t", t, 100, 55, w=40)), "detects illegible text baked into icon")

print(f"{'all passed' if not fails else str(fails) + ' failed'}")
sys.exit(1 if fails else 0)
