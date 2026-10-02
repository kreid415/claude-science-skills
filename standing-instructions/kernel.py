import os
import re
import json
import fnmatch
import hashlib
import datetime
import tempfile
import subprocess

SI_FILE = "CONSTRAINTS.md"
SI_SCOPES = ("data", "method", "format", "writing", "collaboration", "process")
SI_CHECK_KEYS = ("forbid", "require", "forbid_literal", "require_literal", "ignore_case")
SI_HEADER_RE = r"^## (SI-\d{2,}) \u00b7 (active|retired) \u00b7 (\w+)\s*$"
SI_NB_RE = r"NB-\d{8}-\d{2,}"
SI_SKIP_DIRS = (".git", "notebook", "node_modules", "__pycache__", ".venv", "venv", ".ipynb_checkpoints")
SI_SKIP_FILES = ("CONSTRAINTS.md",)
SI_HEADER_TEXT = (
    "# Standing constraints\n"
    "<!-- The user's standing rules for this project. Managed by the standing-instructions skill\n"
    "     (si_add / si_retire / si_link). Entries are never deleted; retire them. Check every deliverable\n"
    "     against the active entries before presenting it. -->\n"
)
SI_STOPWORDS = (
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "not", "are", "was", "were", "will",
    "would", "should", "could", "can", "you", "your", "our", "all", "any", "but", "use", "using", "into",
    "than", "then", "them", "they", "their", "there", "here", "when", "what", "which", "why", "how", "who",
    "does", "did", "done", "about", "also", "just", "only", "more", "most", "some", "such", "each", "every",
    "other", "been", "being", "its", "make", "need", "want", "please", "like", "tell", "show", "explain",
    "answer", "before", "after", "shall", "may", "might", "must", "let", "get", "got", "out", "off",
    "now", "new", "old", "one", "two", "way", "etc", "yes", "see", "say", "said", "own", "per", "via",
)
SI_SCOPE_TRIGGERS = {
    "data": ("data", "dataset", "gene", "cell", "sample", "filter", "exclude", "include", "cohort", "species",
             "human", "mouse", "panel", "download", "table", "count", "annotation", "benchmark"),
    "method": ("method", "metric", "model", "score", "scoring", "experiment", "sweep", "hyperparameter",
               "baseline", "split", "seed", "analysis", "evaluate", "protocol", "design", "ablation"),
    "format": ("figure", "plot", "png", "pdf", "svg", "slide", "deck", "latex", "tex", "markdown", "bullet",
               "itemize", "font", "legend", "layout", "render", "build", "table", "panel"),
    "writing": ("write", "draft", "paper", "manuscript", "outline", "abstract", "introduction", "section",
                "summary", "report", "text", "rewrite", "wording", "prose", "caption", "narrative",
                "response", "letter", "readme"),
    "collaboration": ("collaborator", "collaboration", "author", "coauthor", "reviewer", "team", "email"),
    "process": ("plan", "execute", "launch", "submit", "commit", "push", "publish", "approve", "decision",
                "design", "deliverable", "finish", "verify", "proceed"),
}
SI_QUESTION_START = (
    "what", "why", "how", "which", "who", "whom", "whose", "when", "where", "is", "are", "was", "were",
    "do", "does", "did", "can", "could", "should", "would", "will", "shall",
)
SI_DIRECTIVE_START = ("tell me", "show me", "explain", "clarify", "describe", "walk me through", "list the", "give me")
SI_POINTER_RE = r"\banswer\b.{0,20}\b(question|questions)\b.{0,40}\b(previous|above|earlier|last|prior)\b"


def si_error(kind="Error"):
    """Exception class for `kind` (Error, Ledger, Duplicate, Violation, Unanswered, Stale, Scope, Notebook).

    Classes are built lazily because sidecars cannot define top-level classes; all derive from SIError.
    """
    cache = si_error.__dict__.setdefault("classes", {})
    if "Error" not in cache:
        cache["Error"] = type("SIError", (RuntimeError,), {"result": None})
    if kind not in cache:
        cache[kind] = type("SI" + kind + "Error", (cache["Error"],), {})
    return cache[kind]


def si_raise(kind, message, result=None):
    """Raise the SI exception of `kind` with `message`; `result` is attached as exc.result."""
    exc = si_error(kind)(message)
    exc.result = result
    raise exc


def si_now(date=None):
    """ISO date string: `date` if given (validated), else today."""
    if date is None:
        return datetime.date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date)):
        si_raise("Error", f"date must be YYYY-MM-DD, got {date!r}")
    return str(date)


def si_resolve_root(root=None):
    """Absolute repo root: `root`, else the git toplevel of the cwd. Raises if neither is usable."""
    if root is None:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if r.returncode != 0:
            si_raise("Ledger", "root not given and cwd is not inside a git repo; pass root=<repo root>")
        root = r.stdout.strip()
    root = os.path.abspath(os.path.expanduser(str(root)))
    if not os.path.isdir(root):
        si_raise("Ledger", f"root {root} is not a directory")
    return root


def si_tokens(text):
    """Lowercase content tokens: len>=3, stopwords removed, plural 's' stripped."""
    stop = set(SI_STOPWORDS)
    out = []
    for w in re.findall(r"[A-Za-z0-9_]+", str(text).lower()):
        if len(w) < 3 or w in stop:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def si_norm_check(check):
    """Validate/normalise a rule's automatable check; None for no check. Raises on unknown keys or bad regex."""
    if not check:
        return None
    if not isinstance(check, dict):
        si_raise("Error", f"check must be a dict with keys from {SI_CHECK_KEYS}, got {type(check).__name__}")
    bad = [k for k in check if k not in SI_CHECK_KEYS]
    if bad:
        si_raise("Error", f"unknown check keys {bad}; allowed: {list(SI_CHECK_KEYS)}")
    out = {}
    for k in SI_CHECK_KEYS[:4]:
        vals = check.get(k) or []
        if isinstance(vals, str):
            vals = [vals]
        vals = [str(v) for v in vals]
        if any(not v for v in vals):
            si_raise("Error", f"check['{k}'] contains an empty pattern, which would match everything")
        if k in ("forbid", "require"):
            for v in vals:
                try:
                    re.compile(v)
                except re.error as e:
                    si_raise("Error", f"check['{k}'] pattern {v!r} is not a valid regex: {e}")
        if vals:
            out[k] = vals
    if check.get("ignore_case"):
        out["ignore_case"] = True
    if not any(k in out for k in SI_CHECK_KEYS[:4]):
        si_raise("Error", "check has no patterns; pass None for a rule that can only be checked by hand")
    return out


def si_render(rules):
    """Render rule dicts to CONSTRAINTS.md text (deterministic)."""
    lines = [SI_HEADER_TEXT.rstrip("\n")]
    for r in rules:
        lines += ["", f"## {r['id']} \u00b7 {r['status']} \u00b7 {r['scope']}", f"- rule: {r['rule']}",
                  f"- source: {r['source']}", f"- added: {r['added']}"]
        if r.get("keywords"):
            lines.append("- keywords: " + ", ".join(r["keywords"]))
        if r.get("files"):
            lines.append("- files: " + ", ".join(r["files"]))
        if r.get("check"):
            lines.append("- check: " + json.dumps(r["check"], ensure_ascii=False, sort_keys=True))
        if r.get("notebook"):
            lines.append("- notebook: " + ", ".join(r["notebook"]))
        if r["status"] == "retired":
            lines.append(f"- retired: {r['retired_date']} \u00b7 {r['retired_reason']}")
    return "\n".join(lines) + "\n"


def si_parse(text, path="CONSTRAINTS.md"):
    """Parse ledger text into rule dicts. Raises SILedgerError on any malformed entry."""
    blocks = []
    for n, line in enumerate(text.split("\n"), 1):
        h = re.match(SI_HEADER_RE, line)
        if h:
            blocks.append({"id": h.group(1), "status": h.group(2), "scope": h.group(3), "line": n, "f": {}})
        elif line.startswith("## SI-"):
            si_raise("Ledger", f"{path}:{n}: malformed entry header {line!r}; expected '## SI-NN \u00b7 active|retired \u00b7 scope'")
        elif blocks:
            f = re.match(r"^- ([a-z_]+):\s*(.*)$", line)
            if f:
                blocks[-1]["f"][f.group(1)] = f.group(2).strip()
    rules, seen = [], set()
    for b in blocks:
        where = f"{path}:{b['line']} {b['id']}"
        if b["id"] in seen:
            si_raise("Ledger", f"{where}: duplicate id")
        seen.add(b["id"])
        if b["scope"] not in SI_SCOPES:
            si_raise("Ledger", f"{where}: scope {b['scope']!r} not in {list(SI_SCOPES)}")
        f = b["f"]
        for k in ("rule", "source", "added"):
            if not f.get(k):
                si_raise("Ledger", f"{where}: missing '- {k}:' field")
        check = None
        if f.get("check"):
            try:
                check = si_norm_check(json.loads(f["check"]))
            except ValueError as e:
                si_raise("Ledger", f"{where}: check is not valid JSON ({e})")
        r = {"id": b["id"], "status": b["status"], "scope": b["scope"], "rule": f["rule"], "source": f["source"],
             "added": f["added"], "keywords": [x.strip() for x in f.get("keywords", "").split(",") if x.strip()],
             "files": [x.strip() for x in f.get("files", "").split(",") if x.strip()], "check": check,
             "notebook": [x.strip() for x in f.get("notebook", "").split(",") if x.strip()],
             "retired_date": None, "retired_reason": None}
        if b["status"] == "retired":
            m = re.match(r"^(\d{4}-\d{2}-\d{2}) \u00b7 (.+)$", f.get("retired", ""))
            if not m:
                si_raise("Ledger", f"{where}: retired entry needs '- retired: YYYY-MM-DD \u00b7 reason'")
            r["retired_date"], r["retired_reason"] = m.group(1), m.group(2)
        rules.append(r)
    return rules


def si_init(root=None, project=None):
    """Create <root>/CONSTRAINTS.md (never overwrites). Returns {created, path}."""
    root = si_resolve_root(root)
    path = os.path.join(root, SI_FILE)
    if os.path.exists(path):
        return {"created": False, "path": path}
    text = SI_HEADER_TEXT
    if project:
        text = text.replace("# Standing constraints", f"# Standing constraints: {project}", 1)
    with open(path, "w") as fh:
        fh.write(text)
    return {"created": True, "path": path}


def si_load(root=None):
    """Parse the ledger -> {path, rules, active, retired}. Raises if missing or malformed."""
    root = si_resolve_root(root)
    path = os.path.join(root, SI_FILE)
    if not os.path.exists(path):
        si_raise("Ledger", f"{path} does not exist; run si_init(root) and record the user's standing rules")
    rules = si_parse(open(path).read(), path)
    return {"path": path, "rules": rules, "active": [r for r in rules if r["status"] == "active"],
            "retired": [r for r in rules if r["status"] == "retired"]}


def si_update(root, fn):
    """Load the ledger under a cross-process lock, apply fn(rules) -> result, write atomically."""
    import fcntl
    root = si_resolve_root(root)
    lock = os.path.join(tempfile.gettempdir(), "si_" + hashlib.md5(root.encode()).hexdigest() + ".lock")
    with open(lock, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        led = si_load(root)
        out = fn(led["rules"])
        tmp = led["path"] + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(si_render(led["rules"]))
        os.replace(tmp, led["path"])
    return out


def si_nb_ids(root):
    """Set of NB entry ids present in <root>/notebook/*.md, or None if the notebook directory is absent."""
    d = os.path.join(root, "notebook")
    if not os.path.isdir(d):
        return None
    ids = set()
    for fn in os.listdir(d):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", fn):
            ids.update(re.findall(r"^## (" + SI_NB_RE + r") \u00b7", open(os.path.join(d, fn)).read(), re.M))
    return ids


def si_add(rule, scope, source, root=None, check=None, keywords=None, files=None, notebook=None, date=None, force=False):
    """Append an active rule to CONSTRAINTS.md. Call the moment the user states or corrects something.

    rule: the user's words, one sentence. source: quoted words + YYYY-MM-DD, or an NB-... DECISION id.
    check: optional dict {forbid, require, forbid_literal, require_literal, ignore_case} for si_check_output.
    keywords: extra trigger words for si_applicable ("*" = always applies). files: globs the check applies to.
    Returns {id, rule, path, nb_entry_suggestion, next_steps}; raises on bad scope/source/regex or a near-duplicate.
    """
    root = si_resolve_root(root)
    rule = re.sub(r"\s+", " ", str(rule or "")).strip()
    source = re.sub(r"\s+", " ", str(source or "")).strip()
    if len(rule) < 8:
        si_raise("Error", "rule is empty or too short; record the user's own words")
    if scope not in SI_SCOPES:
        si_raise("Error", f"scope {scope!r} not in {list(SI_SCOPES)}")
    nb_in_source = re.findall(SI_NB_RE, source)
    quoted = re.search(r"[\"\u201c][^\"\u201d]{4,}[\"\u201d]", source) and re.search(r"\d{4}-\d{2}-\d{2}", source)
    if not (nb_in_source or quoted):
        si_raise("Error", "source must quote the user's words in double quotes with a date (YYYY-MM-DD), "
                          "or cite an NB-YYYYMMDD-NN DECISION id; a rule without a traceable source is a guess")
    nb_ids = list(dict.fromkeys(nb_in_source + [str(x) for x in (notebook or [])]))
    if nb_ids:
        known = si_nb_ids(root)
        if known is None:
            si_raise("Notebook", f"{nb_ids} cited but {root}/notebook does not exist; cannot verify the entry")
        missing = [x for x in nb_ids if x not in known]
        if missing:
            si_raise("Notebook", f"notebook entries not found: {missing}")
    check = si_norm_check(check)
    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    fls = [str(g).strip() for g in (files or []) if str(g).strip()]
    toks = set(si_tokens(rule))

    def add(rules):
        for r in rules:
            if r["status"] != "active" or force:
                continue
            o = set(si_tokens(r["rule"]))
            if toks and o and len(toks & o) / len(toks | o) >= 0.8:
                si_raise("Duplicate", f"near-duplicate of active {r['id']}: {r['rule']!r}. Update it with si_retire + "
                                      "si_add (quote the new wording), or pass force=True if they genuinely differ")
        n = 1 + max([int(r["id"][3:]) for r in rules] or [0])
        new = {"id": f"SI-{n:02d}", "status": "active", "scope": scope, "rule": rule, "source": source,
               "added": si_now(date), "keywords": kws, "files": fls, "check": check, "notebook": nb_ids,
               "retired_date": None, "retired_reason": None}
        rules.append(new)
        return new

    new = si_update(root, add)
    sug = None
    if not nb_in_source:
        sug = {"type": "DECISION", "title": f"Standing rule {new['id']}: {rule[:60]}",
               "fields": {"decision": rule, "alternatives": "none (standing rule stated by the user)",
                          "rationale": f"user instruction, recorded in CONSTRAINTS.md as {new['id']}; source: {source}",
                          "decided by": "user"}}
    steps = ["save CONSTRAINTS.md as an artifact (version_of the previous one)"]
    if sug:
        steps.insert(0, "mirror to the lab notebook with nb_entry(**suggestion), then si_link(id, <NB id>)")
    return {"id": new["id"], "rule": new, "path": os.path.join(root, SI_FILE), "nb_entry_suggestion": sug,
            "next_steps": steps}


def si_retire(id, reason, root=None, date=None):
    """Mark rule `id` retired (never deletes). `reason` must say who/what lifted it (the user's words + date)."""
    reason = re.sub(r"\s+", " ", str(reason or "")).strip()
    if len(reason) < 8:
        si_raise("Error", "reason is required (the user's words lifting the rule, or what superseded it)")

    def ret(rules):
        hit = [r for r in rules if r["id"] == id]
        if not hit:
            si_raise("Ledger", f"{id} not found; known: {[r['id'] for r in rules]}")
        if hit[0]["status"] != "active":
            si_raise("Ledger", f"{id} is already retired ({hit[0]['retired_reason']})")
        hit[0].update(status="retired", retired_date=si_now(date), retired_reason=reason)
        return dict(hit[0])

    r = si_update(root, ret)
    return {"id": id, "retired": r, "next_steps": ["write the lifting decision to the notebook (nb_entry DECISION or "
                                                  "CORRECTION)", "save CONSTRAINTS.md as an artifact version"]}


def si_link(id, nb_id, root=None):
    """Attach a lab-notebook entry id to rule `id` (verified to exist in notebook/)."""
    root = si_resolve_root(root)
    known = si_nb_ids(root)
    if known is None:
        si_raise("Notebook", f"{root}/notebook does not exist; cannot link {nb_id}")
    if nb_id not in known:
        si_raise("Notebook", f"{nb_id} is not an entry in {root}/notebook")

    def link(rules):
        hit = [r for r in rules if r["id"] == id]
        if not hit:
            si_raise("Ledger", f"{id} not found")
        if nb_id not in hit[0]["notebook"]:
            hit[0]["notebook"].append(nb_id)
        return dict(hit[0])

    return si_update(root, link)


def si_rules_arg(rules):
    """Normalise a rules argument (list of rules, one rule, or an si_applicable result) to a list."""
    if isinstance(rules, dict) and "matched" in rules:
        rules = rules["matched"]
    elif isinstance(rules, dict) and "id" in rules:
        rules = [rules]
    if not isinstance(rules, (list, tuple)):
        si_raise("Error", "rules must be a list of rule dicts or the result of si_applicable")
    for r in rules:
        if not isinstance(r, dict) or "id" not in r or "rule" not in r:
            si_raise("Error", f"not a rule dict: {r!r}")
        if r.get("status", "active") != "active":
            si_raise("Error", f"{r['id']} is retired; do not check output against it")
    return list(rules)


def si_applicable(task_text, scopes=None, root=None, rules=None):
    """Active rules that bear on a task: explicit scopes, keyword overlap, or scope trigger words.

    Over-inclusion is deliberate (a missed rule costs the user a repeated instruction). Returns
    {matched: [rule + why + score], unmatched: [ids], ledger_active, warnings}.
    """
    if not str(task_text or "").strip():
        si_raise("Error", "task_text is empty; describe the deliverable being produced")
    if scopes is not None:
        bad = [s for s in scopes if s not in SI_SCOPES]
        if bad:
            si_raise("Error", f"unknown scopes {bad}; allowed {list(SI_SCOPES)}")
    active = si_rules_arg(rules) if rules is not None else si_load(root)["active"]
    ttoks = set(si_tokens(task_text))
    hit_scopes = {s for s, trig in SI_SCOPE_TRIGGERS.items() if ttoks & set(si_tokens(" ".join(trig)))}
    matched, unmatched = [], []
    for r in active:
        why = []
        if scopes and r["scope"] in scopes:
            why.append(f"scope {r['scope']} requested")
        for k in r.get("keywords") or []:
            kt = set(si_tokens(k))
            if k == "*":
                why.append("always applies")
            elif kt and kt <= ttoks:
                why.append(f"keyword '{k}'")
        shared = sorted(set(si_tokens(r["rule"])) & ttoks)
        if len(shared) >= 2:
            why.append("rule words: " + ", ".join(shared[:5]))
        if r["scope"] in hit_scopes:
            why.append(f"task looks like {r['scope']} work")
        if why:
            matched.append(dict(r, why=why, score=len(why)))
        else:
            unmatched.append(r["id"])
    matched.sort(key=lambda r: (-r["score"], r["id"]))
    warnings = []
    if not active:
        warnings.append("ledger has no active rules; if the user has stated rules in this project, record them with si_add")
    return {"matched": matched, "unmatched": unmatched, "ledger_active": len(active), "warnings": warnings}


def si_targets(text_or_path):
    """Resolve input to [(name, text)]: files are read, strings with newlines are text, missing paths raise."""
    items = text_or_path if isinstance(text_or_path, (list, tuple)) else [text_or_path]
    out = []
    for it in items:
        s = os.fspath(it) if hasattr(it, "__fspath__") else it
        if not isinstance(s, str):
            si_raise("Error", f"expected text or path, got {type(s).__name__}")
        short = "\n" not in s and len(s) < 1024
        if short and os.path.isfile(os.path.expanduser(s)):
            out.append((s, open(os.path.expanduser(s), errors="replace").read()))
        elif short and os.path.isdir(os.path.expanduser(s)):
            si_raise("Error", f"{s} is a directory; pass the deliverable files (list of paths)")
        elif short and re.fullmatch(r"[~\w./\\-]+\.[A-Za-z0-9]{1,6}", s):
            si_raise("Error", f"{s} looks like a path but does not exist; the deliverable was not written")
        else:
            out.append((None, s))
    if not out:
        si_raise("Error", "nothing to check")
    return out


def si_check_output(text_or_path, rules, strict=True, allow_empty=False):
    """Check a deliverable (text, file path, or list of paths) against rules' automatable patterns.

    Rules without a check are returned under `manual` and must be verified by hand and reported. Raises
    SIViolationError (with .result) when strict and any pattern rule is violated.
    """
    rl = si_rules_arg(rules)
    if not rl and not allow_empty:
        si_raise("Error", "no rules supplied; call si_applicable first (pass allow_empty=True only if it returned none)")
    res = {"ok": True, "checked": [], "passed": [], "violations": [], "manual": [], "skipped": []}
    for r in rl:
        if not r.get("check"):
            res["manual"].append({"id": r["id"], "rule": r["rule"]})
    for name, text in si_targets(text_or_path):
        for r in rl:
            chk = r.get("check")
            if not chk:
                continue
            if name and r.get("files") and not any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(os.path.basename(name), g) for g in r["files"]):
                res["skipped"].append({"id": r["id"], "file": name, "reason": f"file does not match {r['files']}"})
                continue
            flags = re.M | (re.I if chk.get("ignore_case") else 0)
            if r["id"] not in res["checked"]:
                res["checked"].append(r["id"])
            pats = [("forbid", p, p) for p in chk.get("forbid", [])] + [("forbid", p, re.escape(p)) for p in chk.get("forbid_literal", [])]
            reqs = [("require", p, p) for p in chk.get("require", [])] + [("require", p, re.escape(p)) for p in chk.get("require_literal", [])]
            for kind, shown, rx in pats + reqs:
                found = list(re.finditer(rx, text, flags))
                if kind == "forbid" and found:
                    hits = [{"line": text.count("\n", 0, m.start()) + 1,
                             "text": text.split("\n")[text.count("\n", 0, m.start())].strip()[:100]} for m in found[:5]]
                    res["violations"].append({"id": r["id"], "rule": r["rule"], "file": name, "kind": "forbidden",
                                              "pattern": shown, "count": len(found), "hits": hits})
                elif kind == "require" and not found:
                    res["violations"].append({"id": r["id"], "rule": r["rule"], "file": name, "kind": "missing",
                                              "pattern": shown, "count": 0, "hits": []})
    bad = {v["id"] for v in res["violations"]}
    res["passed"] = [i for i in res["checked"] if i not in bad]
    res["ok"] = not res["violations"]
    res["needs_manual_review"] = bool(res["manual"])
    if strict and not res["ok"]:
        msg = ["deliverable violates standing rules:"]
        for v in res["violations"]:
            where = f" in {v['file']}" if v["file"] else ""
            at = "; ".join(f"L{h['line']}: {h['text']}" for h in v["hits"][:3])
            msg.append(f"  {v['id']} ({v['rule']}): {v['kind']} pattern {v['pattern']!r}{where} {at}".rstrip())
        si_raise("Violation", "\n".join(msg), res)
    return res


def si_sentences(message):
    """Split a user message into sentences/lines, stripping list markers and leading fillers."""
    out = []
    for part in re.split(r"(?<=[.?!])\s+|\n+", str(message)):
        s = re.sub(r"^(?:\s*(?:\d+[.)]|[-*])\s+|\s*(?:and|also|but|so|ok|okay|now)\b[,\s]*)+", "", part.strip(), flags=re.I)
        if s:
            out.append(s)
    return out


def si_open_questions(user_messages, answered=None, strict=True):
    """Question/explain-request sentences in user messages not yet addressed.

    answered: list of strings (the questions you answered, verbatim or paraphrased, or the answer text);
    a question counts as answered when >=60% of its content words appear in one item. Raises
    SIUnansweredError when strict and any remain.
    """
    msgs = [user_messages] if isinstance(user_messages, str) else list(user_messages or [])
    if not msgs:
        si_raise("Error", "no user messages supplied")
    ans = [str(a) for a in (answered or [])]
    atoks = [set(si_tokens(a)) for a in ans]
    qs, pointer = [], None
    for i, m in enumerate(msgs):
        for s in si_sentences(m):
            low = s.lower()
            if re.search(SI_POINTER_RE, low):
                pointer = i
                continue
            q = s.endswith("?")
            if not q and not re.match(r"^(do not|don't|dont)\b", low):
                q = bool(re.match(r"^(" + "|".join(SI_QUESTION_START) + r")\b", low))
            if not q:
                q = any(low.startswith(d) for d in SI_DIRECTIVE_START)
            if q:
                qs.append((i, s))
    if pointer is not None and not any(i < pointer for i, _ in qs):
        si_raise("Error", "the user refers to questions in an earlier message, but none precede it in user_messages; "
                          "pass the preceding messages too")
    items = []
    for i, s in qs:
        qt = set(si_tokens(s))
        cov = max([len(qt & a) / len(qt) for a in atoks] or [0.0]) if qt else 0.0
        items.append({"msg": i, "text": s, "answered": cov >= 0.6, "coverage": round(cov, 2)})
    un = [x["text"] for x in items if not x["answered"]]
    res = {"questions": items, "unanswered": un, "n_questions": len(items), "ok": not un}
    if strict and un:
        si_raise("Unanswered", f"{len(un)} user question(s) not yet answered; answer them before executing the plan:\n  - "
                               + "\n  - ".join(un), res)
    return res


def si_propagate(old_phrase, roots, extra_texts=None, regex=False, ignore_case=True, strict=True, exclude_dirs=None):
    """Find stale statements of a corrected claim in files under `roots` and in `extra_texts` {name: text}.

    Whitespace-insensitive (matches across line wraps). Skips .git, notebook/ (append-only history) and
    CONSTRAINTS.md. Raises SIStaleError listing every hit when strict; fix them all, then re-run until clean.
    Memory rows and artifact bodies are not files: pass their text via extra_texts.
    """
    phrase = str(old_phrase or "").strip()
    if len(phrase) < 3:
        si_raise("Error", "old_phrase must be at least 3 characters")
    rx = phrase if regex else r"\s+".join(re.escape(t) for t in phrase.split())
    try:
        cre = re.compile(rx, re.M | (re.I if ignore_case else 0))
    except re.error as e:
        si_raise("Error", f"old_phrase is not a valid regex: {e}")
    roots = [roots] if isinstance(roots, str) else list(roots or [])
    skip = set(SI_SKIP_DIRS) | set(exclude_dirs or [])
    files, skipped = [], []
    for rt in roots:
        rt = os.path.abspath(os.path.expanduser(rt))
        if os.path.isfile(rt):
            files.append((rt, os.path.dirname(rt)))
        elif os.path.isdir(rt):
            for dp, dn, fn in os.walk(rt):
                dn[:] = [d for d in dn if d not in skip]
                files += [(os.path.join(dp, f), rt) for f in fn if f not in SI_SKIP_FILES]
        else:
            si_raise("Error", f"root {rt} does not exist")
    texts = []
    for p, base in files:
        try:
            if os.path.getsize(p) > 5000000:
                skipped.append({"path": p, "reason": "larger than 5 MB"})
                continue
            raw = open(p, "rb").read()
        except OSError as e:
            skipped.append({"path": p, "reason": str(e)})
            continue
        if b"\0" in raw[:4096]:
            continue
        texts.append((os.path.relpath(p, base), raw.decode("utf-8", errors="replace")))
    texts += [(f"<{k}>", str(v)) for k, v in (extra_texts or {}).items()]
    if not texts:
        si_raise("Error", f"scanned nothing under {roots}; a clean result would be vacuous")
    hits = []
    for name, txt in texts:
        lines = txt.split("\n")
        for m in cre.finditer(txt):
            ln = txt.count("\n", 0, m.start())
            hits.append({"path": name, "line": ln + 1, "text": lines[ln].strip()[:120]})
    res = {"phrase": phrase, "hits": hits, "n_scanned": len(texts), "skipped": sorted(skip) + list(SI_SKIP_FILES),
           "unreadable": skipped, "clean": not hits}
    if strict and hits:
        shown = [f"  {h['path']}:{h['line']}: {h['text']}" for h in hits[:20]]
        si_raise("Stale", f"{len(hits)} stale statement(s) of {phrase!r} remain; fix each, then re-run:\n" + "\n".join(shown), res)
    return res


def si_scope_check(allowed, root=None, base="HEAD", strict=True):
    """Files changed since `base` (committed, staged, unstaged, untracked) that fall outside `allowed` globs.

    Record `git rev-parse HEAD` when a task starts and pass it as base to also catch commits made during it.
    """
    root = si_resolve_root(root)
    allowed = [allowed] if isinstance(allowed, str) else list(allowed or [])
    if not allowed:
        si_raise("Error", "allowed is empty; list the paths/globs the user's request covers")

    def git(*a):
        r = subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True)
        if r.returncode != 0:
            si_raise("Scope", f"git {' '.join(a)} failed: {r.stderr.strip()[:200]}")
        return [x for x in r.stdout.split("\n") if x]

    changed = sorted(set(git("diff", "--name-only", base) + git("ls-files", "--others", "--exclude-standard")))

    def ok(p):
        return any(fnmatch.fnmatch(p, g) or (g.endswith("/") and p.startswith(g)) for g in allowed)

    inside = [p for p in changed if ok(p)]
    outside = [p for p in changed if not ok(p)]
    res = {"changed": changed, "in_scope": inside, "out_of_scope": outside, "ok": not outside, "base": base}
    if strict and outside:
        si_raise("Scope", "files changed outside the requested scope; revert them or ask the user first:\n  - "
                          + "\n  - ".join(outside), res)
    return res


def si_sync_notebook(root=None, strict=True):
    """Reconcile the ledger with notebook DECISION entries.

    Reports user-made DECISIONs not mirrored to the ledger (`unmirrored`: triage each), ledger links to
    missing entries (`dangling`) and mirrored decisions later CORRECTed (`corrected`). Raises when strict and
    dangling or corrected is non-empty.
    """
    root = si_resolve_root(root)
    d = os.path.join(root, "notebook")
    if not os.path.isdir(d):
        si_raise("Notebook", f"{d} does not exist; nothing to sync (run nb_init if the project keeps a notebook)")
    ents, cur = [], None
    for fn in sorted(os.listdir(d)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", fn):
            continue
        for line in open(os.path.join(d, fn)).read().split("\n"):
            h = re.match(r"^## (" + SI_NB_RE + r") \u00b7 (\S+) \u00b7 ([A-Z]+)", line)
            if h:
                cur = {"id": h.group(1), "type": h.group(3), "title": "", "f": {}}
                ents.append(cur)
            elif cur is not None:
                t = re.match(r"^\*\*(.+)\*\*\s*$", line)
                if t and not cur["title"]:
                    cur["title"] = t.group(1)
                f = re.match(r"^- ([a-z ]+):\s*(.*)$", line)
                if f:
                    cur["f"][f.group(1).strip()] = f.group(2).strip()
    ids = {e["id"] for e in ents}
    led = si_load(root)
    linked = {}
    for r in led["rules"]:
        for x in set(r["notebook"]) | set(re.findall(SI_NB_RE, r["source"])):
            linked.setdefault(x, []).append(r["id"])
    corrections = {e["f"].get("corrects"): e["id"] for e in ents if e["type"] == "CORRECTION"}
    res = {"unmirrored": [{"id": e["id"], "title": e["title"], "decision": e["f"].get("decision", "")}
                          for e in ents if e["type"] == "DECISION" and re.search(r"\buser\b", e["f"].get("decided by", ""), re.I)
                          and e["id"] not in linked],
           "dangling": [{"rule": r, "nb": x} for x, rs in linked.items() if x not in ids for r in rs],
           "corrected": [{"rule": r, "nb": x, "corrected_by": corrections[x]} for x, rs in linked.items()
                         if x in corrections for r in rs]}
    res["ok"] = not (res["dangling"] or res["corrected"])
    if strict and not res["ok"]:
        si_raise("Notebook", f"ledger and notebook disagree: dangling={res['dangling']} corrected={res['corrected']}; "
                             "update the ledger (si_retire + si_add) to match", res)
    return res
