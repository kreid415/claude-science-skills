"""Deslop linter: flag mechanical AI writing tells in prose.

Loaded automatically by skill({skill: "deslop"}). Entry points:
    deslop_scan(text_or_path)      -> dict of findings, rhythm, counts
    deslop_report(scan)            -> markdown report string
    deslop_score(scan)             -> 5 dimensions, 1-10 each, plus total

Severity "hard" = essentially always slop. "soft" = context-dependent and
common in legitimate scientific prose; triage against SKILL.md section 1.
"""

import re

DESLOP_VERSION = "1.0"
DESLOP_ABBREV = ("et al.", "e.g.", "i.e.", "cf.", "vs.", "Fig.", "Figs.",
                 "Eq.", "Tab.", "ref.", "approx.", "ca.", "Dr.", "Prof.",
                 "St.", "No.", "vol.", "pp.", "Suppl.", "Inc.", "sp.", "ssp.")


def deslop_patterns():
    """Return [(category, severity, label, pattern), ...] for the scanner."""
    return [
        # ---- typography and formatting (hard) ----
        ("formatting", "hard", "em/en dash", r"[\u2014\u2013]"),
        ("formatting", "hard", "unicode arrow", r"[\u2192\u21d2\u2190\u21d0\u2194]"),
        ("formatting", "hard", "bold-first bullet",
         r"(?m)^\s*(?:[-*+]|\d+\.)\s+\*\*[^*\n]{2,60}\*\*\s*[:.\u2014-]"),
        ("formatting", "hard", "signposted conclusion",
         r"(?mi)^\s*(?:in conclusion|in summary|to summarize|to sum up|"
         r"in closing|all in all)\b"),
        # ---- filler and throat-clearing (hard) ----
        ("filler", "hard", "throat-clearing opener",
         r"(?i)\b(?:here'?s (?:the thing|what|why|how)|let'?s (?:break|dive|"
         r"unpack|explore)|before we (?:begin|dive)|buckle up)\b"),
        ("filler", "hard", "filler transition",
         r"(?i)\b(?:it'?s worth noting that|it is worth noting that|"
         r"it (?:is|'s) important to note that|needless to say|"
         r"that (?:being |said,)|as we (?:all )?know|"
         r"at the end of the day|when it comes to|in today'?s [a-z]+ (?:world|landscape))\b"),
        ("filler", "hard", "emphasis crutch",
         r"(?i)(?:let that sink in|and that matters|make no mistake|"
         r"here'?s the kicker|that'?s the whole point|full stop)\b"),
        ("filler", "hard", "meta-commentary",
         r"(?i)\b(?:in this (?:section|article|post|piece),? (?:we|I) (?:will|'ll)|"
         r"the rest of this (?:essay|post|article|paper)|"
         r"what follows is|by the end of this)\b"),
        ("filler", "hard", "closing gesture",
         r"(?i)\b(?:moving forward|going forward|(?:paves?|paving) the way|"
         r"opens? the door|the road ahead|much (?:work|remains) (?:remains|to be done))\b"),
        # ---- trope vocabulary (hard) ----
        ("trope", "hard", "AI vocabulary tell",
         r"(?i)\b(?:delves?|delving|tapestry|realm of|myriad|plethora|"
         r"treasure trove|testament to|beacon|labyrinth(?:ine)?|"
         r"ever-(?:evolving|changing|growing)|rapidly evolving landscape|"
         r"navigat\w+ the (?:landscape|complexit\w+|challenges)|"
         r"in the world of|unlock(?:ing|s)? the (?:power|potential|secrets)|"
         r"harness(?:ing|es)? the (?:power|potential)|game[- ]chang\w+|"
         r"seamless\w*|robustly (?:demonstrat|show)\w*)\b"),
        ("trope", "hard", "empty importance verb",
         r"(?i)\b(?:underscor\w+|highlight\w* the (?:importance|need|"
         r"significance|value)|sheds? light on|serves? as a (?:reminder|"
         r"testament|foundation|cornerstone)|plays? a (?:crucial|key|vital|"
         r"pivotal|significant) role|cannot be overstated|"
         r"stands? (?:as )?(?:a )?testament)\b"),
        ("trope", "hard", "stakes inflation",
         r"(?i)\b(?:fundamentally (?:reshap\w+|transform\w+|chang\w+|alter\w+)|"
         r"revolutioniz\w+|paradigm shift|unprecedented (?:opportunit|insight)\w*|"
         r"at the forefront of|cutting[- ]edge|"
         r"(?:a|the) new (?:era|frontier|chapter))\b"),
        ("trope", "hard", "vague literature gesture",
         r"(?i)\b(?:(?:a |the )?growing body of (?:literature|evidence|work)|"
         r"(?:has|have) (?:garnered|attracted) (?:significant|considerable|"
         r"increasing) (?:attention|interest)|"
         r"in recent years,? there has been (?:a )?(?:growing|increasing))\b"),
        ("trope", "hard", "magic adverb",
         r"(?i)\b(?:quietly|effortlessly|seamlessly|deftly|elegantly|"
         r"remarkably|strikingly|surprisingly powerful|deceptively simple)\b"),
        ("trope", "hard", "'despite these challenges' formula",
         r"(?i)\bdespite (?:these|the|such) (?:challenges|limitations|"
         r"caveats|shortcomings|obstacles)\b"),
        ("trope", "hard", "patronizing analogy",
         r"(?i)\b(?:think of it (?:as|like)|imagine (?:a|an|if)|"
         r"it'?s (?:basically |essentially |kind of )?like (?:a|an) )\b"),
        ("trope", "hard", "false vulnerability",
         r"(?i)\b(?:(?:we|I) (?:will|'ll)? ?be honest|"
         r"(?:we|I) have to admit|to be (?:completely |totally )?honest|"
         r"(?:this|that) (?:genuinely |really )?surprised (?:us|me))\b"),
        # ---- structure (hard) ----
        ("structure", "hard", "binary contrast",
         r"(?i)\b(?:this|it|that|they)\s*(?:is|are|'s|'re)?n?o?t?\s*"
         r"(?:isn'?t|aren'?t|not)\s+(?:a|an|just|only|merely|about)?\s*"
         r"[^.;:\n]{2,60}[.,;:]\s+(?:it'?s|this is|that'?s|they'?re|it is)\b"),
        ("structure", "hard", "dramatic fragmentation",
         r"(?m)(?:(?:^|(?<=[.!?]\s))[A-Z][\w'-]*(?:\s+[\w'-]+){0,2}[.!]\s+){2}"
         r"[A-Z][\w'-]*(?:\s+[\w'-]+){0,2}[.!]"),
        ("structure", "hard", "negative listing",
         r"(?i)\bnot (?:a|an|the)? ?[\w'-]+\.\s+Not (?:a|an|the)? ?[\w'-]+\."),
        ("structure", "hard", "'not X, but Y' frame",
         r"(?i)\bnot (?:merely|simply|just|only) [^.;\n]{2,60}\bbut (?:rather|also)\b"),
        ("structure", "hard", "self-posed rhetorical question",
         r"(?m)(?:^|(?<=[.!?]\s))[A-Z][^.!?\n]{0,70}\?\s+(?:[A-Z][a-z]+\.|"
         r"Yes\b|No\b|Not\b|Because\b|It\b|Two\b|Three\b)"),
        ("structure", "soft", "tricolon",
         r"(?i)\b\w+(?:ly)?,\s+\w+(?:ly)?,\s+and\s+\w+"),
        ("structure", "soft", "listicle-as-prose",
         r"(?i)\bthe (?:first|second|third) (?:problem|issue|wall|obstacle|"
         r"reason|lesson|thing)\b"),
        ("structure", "soft", "participial after-thought",
         r"(?i),\s+(?:highlighting|underscoring|demonstrating|showcasing|"
         r"reflecting|emphasizing|illustrating|suggesting that this|"
         r"making it|allowing for|paving)\b"),
        # ---- specificity and hedging ----
        ("specificity", "hard", "vague attribution",
         r"(?i)\b(?:(?:researchers|scientists|studies|experts|scholars|"
         r"many authors|some authors) (?:have )?(?:shown|argued|suggested|"
         r"found|noted|demonstrated|believe)|it (?:has|had) long been "
         r"(?:recognized|known|understood|appreciated)|"
         r"it is (?:widely|generally) (?:accepted|believed|recognized))\b"),
        ("specificity", "hard", "hedge stack",
         r"(?i)\b(?:may|might|could|can)\s+(?:potentially|possibly|perhaps|"
         r"conceivably|arguably)\b|\b(?:appears?|seems?)\s+to\s+be\s+"
         r"(?:somewhat|relatively|fairly|rather)\b|"
         r"\bsuggests? that (?:it )?(?:may|might|could) (?:potentially|possibly)\b"),
        ("specificity", "soft", "vague declarative",
         r"(?i)\b(?:the (?:implications|reasons|consequences|stakes|benefits|"
         r"challenges) (?:are|is) (?:significant|structural|clear|profound|"
         r"substantial|far[- ]reaching|manifold)|"
         r"has (?:important|significant|profound) implications)\b"),
        ("specificity", "soft", "unquantified intensifier",
         r"(?i)\b(?:very|really|quite|extremely|incredibly|highly|vastly|"
         r"dramatically|substantially|significantly) (?:important|useful|"
         r"powerful|effective|better|worse|large|small|complex|novel)\b"),
        ("specificity", "soft", "lazy extreme",
         r"(?i)\b(?:every single|all of the|none of the|always|never) "
         r"(?:researcher|scientist|study|paper|method|model|dataset)s?\b"),
        # ---- voice ----
        ("voice", "soft", "passive candidate",
         r"(?i)\b(?:is|are|was|were|been|being|be)\s+(?:\w+ly\s+)?"
         r"(?:observed|found|shown|demonstrated|considered|regarded|believed|"
         r"thought|seen|viewed|understood|known|argued|noted|reported|"
         r"characterized|described|driven|informed|shaped|governed)\b"),
        ("voice", "soft", "false agency",
         r"(?i)\b(?:(?:this|the|our) (?:work|study|analysis|paper|approach|"
         r"framework|result|finding)s? (?:reveals?|demonstrates?|argues?|"
         r"suggests? that we|tells? us|invites?|asks?|wants?|seeks? to))\b"),
        ("voice", "soft", "nominalization",
         r"(?i)\b(?:the (?:utilization|implementation|optimization|"
         r"characterization|identification|determination|quantification) of)\b"),
    ]


def deslop_strip_code(text):
    """Blank out fenced code, inline code, and LaTeX math, preserving lines."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    text = re.sub(r"```.*?```", blank, text, flags=re.S)
    text = re.sub(r"~~~.*?~~~", blank, text, flags=re.S)
    text = re.sub(r"\$\$.*?\$\$", blank, text, flags=re.S)
    text = re.sub(r"\\begin\{(equation|align|gather|verbatim|lstlisting)\*?\}"
                  r".*?\\end\{\1\*?\}", blank, text, flags=re.S)
    text = re.sub(r"`[^`\n]+`", blank, text)
    text = re.sub(r"\$[^$\n]+\$", blank, text)
    return text


def deslop_sentences(text):
    """Split prose into sentences with a light abbreviation guard."""
    flat = re.sub(r"\s+", " ", re.sub(r"(?m)^\s*(?:#{1,6}|[-*+>]|\d+\.)\s+", "", text))
    for a in DESLOP_ABBREV:
        flat = flat.replace(a, a.replace(".", "\u0001"))
    flat = re.sub(r"(?<=\b[A-Z])\.", "\u0001", flat)
    flat = re.sub(r"(?<=\d)\.(?=\d)", "\u0001", flat)
    parts = re.split(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])", flat)
    out = []
    for p in parts:
        s = p.replace("\u0001", ".").strip()
        if len(s.split()) >= 3:
            out.append(s)
    return out


def deslop_rhythm(sentences):
    """Sentence-length statistics plus runs of near-identical length."""
    lens = [len(s.split()) for s in sentences]
    runs = []
    i = 0
    while i < len(lens) - 2:
        j = i
        while (j + 1 < len(lens)
               and abs(lens[j + 1] - lens[i]) <= 2
               and abs(lens[j + 1] - lens[j]) <= 2):
            j += 1
        if j - i + 1 >= 3:
            runs.append({"start_sentence": i + 1, "length": j - i + 1,
                         "words": lens[i:j + 1]})
            i = j
        i += 1
    mean = sum(lens) / len(lens) if lens else 0.0
    var = sum((x - mean) ** 2 for x in lens) / len(lens) if lens else 0.0
    short_end = sum(1 for x in lens if x <= 6)
    return {"n": len(lens), "lengths": lens, "mean": round(mean, 1),
            "sd": round(var ** 0.5, 1), "monotone_runs": runs,
            "n_monotone_runs": len(runs), "n_short_sentences": short_end}


def deslop_scan(text_or_path, skip_code=True, only=None, context=48):
    """Scan prose for mechanical AI tells.

    text_or_path : a string of prose, or a path to a .md/.txt/.tex file.
    skip_code    : blank out code blocks and LaTeX math before scanning.
    only         : None, "hard", or "soft" to filter returned findings.
    context      : characters of excerpt to keep around each match.
    """
    import os
    src = "<string>"
    text = text_or_path
    if isinstance(text_or_path, str) and "\n" not in text_or_path \
            and len(text_or_path) < 400 and os.path.exists(text_or_path):
        src = text_or_path
        text = open(text_or_path, encoding="utf-8", errors="replace").read()
    scan_text = deslop_strip_code(text) if skip_code else text
    starts = [0]
    for i, ch in enumerate(scan_text):
        if ch == "\n":
            starts.append(i + 1)

    def line_of(pos):
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    findings = []
    counts = {}
    seen = set()
    for category, severity, label, pat in deslop_patterns():
        for m in re.finditer(pat, scan_text):
            if not m.group(0).strip():
                continue
            key = (label, m.start())
            if key in seen:
                continue
            seen.add(key)
            a = max(0, m.start() - context // 2)
            b = min(len(scan_text), m.end() + context)
            findings.append({
                "category": category, "severity": severity, "label": label,
                "line": line_of(m.start()), "pos": m.start(),
                "match": m.group(0).strip()[:80],
                "excerpt": re.sub(r"\s+", " ", scan_text[a:b]).strip(),
            })
            counts[label] = counts.get(label, 0) + 1
    findings.sort(key=lambda f: f["pos"])

    sentences = deslop_sentences(scan_text)
    words = len(re.findall(r"\b[\w'-]+\b", scan_text))
    adverbs = re.findall(r"\b\w{4,}ly\b", scan_text)
    n_hard = sum(1 for f in findings if f["severity"] == "hard")
    result = {
        "source": src, "version": DESLOP_VERSION,
        "n_words": words, "n_sentences": len(sentences),
        "n_findings": len(findings), "n_hard": n_hard,
        "n_soft": len(findings) - n_hard,
        "per_1k_words": round(1000.0 * len(findings) / words, 1) if words else 0.0,
        "hard_per_1k_words": round(1000.0 * n_hard / words, 1) if words else 0.0,
        "n_adverbs": len(adverbs), "counts": counts,
        "rhythm": deslop_rhythm(sentences),
        "findings": [f for f in findings
                     if only is None or f["severity"] == only],
    }
    return result


def deslop_score(scan):
    """Heuristic 1-10 per dimension plus total out of 50. Your read overrides."""
    w = max(scan["n_words"], 1)
    r = scan["rhythm"]

    def band(rate, cuts):
        s = 10
        for c in cuts:
            if rate > c:
                s -= 2
        return max(1, s)

    def rate(cats):
        n = sum(1 for f in scan["findings"] if f["category"] in cats)
        return 1000.0 * n / w
    directness = band(rate(("filler", "structure")), (2, 5, 9, 14))
    trust = band(rate(("trope",)), (2, 4, 8, 13))
    authenticity = band(scan["hard_per_1k_words"], (2, 4, 7, 12))
    density = band(rate(("specificity", "voice")) + 1000.0 * scan["n_adverbs"] / w / 2,
                   (6, 11, 18, 26))
    rhythm = 10
    if r["n"] >= 6:
        if r["sd"] < 4:
            rhythm -= 3
        elif r["sd"] < 6:
            rhythm -= 1
        rhythm -= min(4, 2 * r["n_monotone_runs"])
        if r["n_short_sentences"] > 0.25 * r["n"]:
            rhythm -= 1
    rhythm = max(1, rhythm)
    dims = {"directness": directness, "rhythm": rhythm, "trust": trust,
            "authenticity": authenticity, "density": density}
    total = sum(dims.values())
    dims["total"] = total
    dims["verdict"] = ("rewrite the section" if total < 25
                       else "revise" if total < 35 else "acceptable")
    return dims


def deslop_report(scan, include_score=True, max_rows=60):
    """Render a scan as a markdown report."""
    lines = ["# Deslop report", "",
             "Source: `%s`  |  %d words, %d sentences  |  %d findings "
             "(%d hard, %d soft), %.1f per 1k words"
             % (scan["source"], scan["n_words"], scan["n_sentences"],
                scan["n_findings"], scan["n_hard"], scan["n_soft"],
                scan["per_1k_words"]), ""]
    if include_score:
        s = deslop_score(scan)
        lines += ["## Score", "",
                  "| Dimension | Score |", "|---|---|"]
        for k in ("directness", "rhythm", "trust", "authenticity", "density"):
            lines.append("| %s | %d/10 |" % (k.capitalize(), s[k]))
        lines += ["| **Total** | **%d/50** |" % s["total"], "",
                  "Verdict: %s." % s["verdict"], ""]
    if scan["counts"]:
        lines += ["## Findings by pattern", "", "| Pattern | Count |", "|---|---|"]
        for k, v in sorted(scan["counts"].items(), key=lambda kv: -kv[1]):
            lines.append("| %s | %d |" % (k, v))
        lines.append("")
    if scan["findings"]:
        lines += ["## Findings", "",
                  "| Line | Severity | Pattern | Excerpt |", "|---|---|---|---|"]
        for f in scan["findings"][:max_rows]:
            ex = f["excerpt"].replace("|", "\\|")
            lines.append("| %d | %s | %s | %s |"
                         % (f["line"], f["severity"], f["label"], ex))
        if len(scan["findings"]) > max_rows:
            lines.append("")
            lines.append("%d further findings omitted."
                         % (len(scan["findings"]) - max_rows))
        lines.append("")
    r = scan["rhythm"]
    lines += ["## Rhythm", "",
              "Mean sentence %.1f words, sd %.1f. %d run(s) of 3+ sentences "
              "within 2 words of each other."
              % (r["mean"], r["sd"], r["n_monotone_runs"]), ""]
    for run in r["monotone_runs"][:8]:
        lines.append("- sentences %d-%d: %s words"
                     % (run["start_sentence"],
                        run["start_sentence"] + run["length"] - 1,
                        ", ".join(str(x) for x in run["words"])))
    lines += ["", "Hard findings are essentially always slop. Soft findings are "
              "context-dependent: check SKILL.md section 1 before editing, and "
              "never trade a number, a hedge, or a citation for smoother prose."]
    return "\n".join(lines)
