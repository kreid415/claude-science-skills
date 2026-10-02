import os
import re
import ast
import json
import glob
import difflib
import builtins
import importlib

FL_VERSION = "1.0"
FL_SEV = {"low": 1, "medium": 2, "high": 3}
FL_LANG_EXT = {".py": "py", ".sh": "sh", ".bash": "sh", ".sbatch": "sh", ".slurm": "sh", ".nf": "nf", ".r": "r"}
FL_SKIP_DIRS = [".git", "__pycache__", "node_modules", ".venv", "venv", ".nextflow", "work", ".snakemake", ".ipynb_checkpoints"]
FL_SUPPRESS_RE = r"(?:#|//)\s*fl:\s*allow\s+([A-Za-z0-9_,]+)\b(.*)$"
FL_RULES = {
    "FL000": ("high", "suppression comment is malformed: needs known rule id(s) and a reason of >=3 chars"),
    "FL001": ("high", "file could not be parsed, so it was NOT linted"),
    "FL101": ("high", "broad/bare except without re-raise or non-zero exit: failure becomes a fallback"),
    "FL102": ("medium", "except block only passes/continues: error swallowed"),
    "FL103": ("high", "R try/tryCatch error handler returns a value instead of stop()"),
    "FL110": ("high", "output/input file picked by position (listdir/glob [0], next(iter())): wrong-file risk"),
    "FL111": ("medium", "shell picks a file with ls | head -1 / $(ls ...)"),
    "FL120": ("high", "set +e disables fail-fast: later failures are invisible"),
    "FL121": ("medium", "'|| true' / '|| :' swallows a non-zero exit"),
    "FL122": ("low", "shell script has no 'set -e' (or -euo pipefail)"),
    "FL123": ("medium", "stderr discarded on a command whose failure matters (activate/git/conda/pip/sbatch/rsync/scp)"),
    "FL130": ("high", "parse_known_args silently discards unknown/misspelled options"),
    "FL131": ("medium", "argparse type=bool: bool('false') is True"),
    "FL140": ("medium", "subprocess.run/os.system result never checked (no check=True and no returncode use)"),
    "FL150": ("high", "numba fastmath=True can silently change metric results (NaN/inf assumptions)"),
    "FL160": ("high", "executor hardcoded in a Nextflow process body overrides profile/config"),
    "FL161": ("medium", "Nextflow ifEmpty([]) / ifEmpty(null) can silently skip downstream consumers"),
    "FL162": ("high", "Nextflow errorStrategy 'ignore' silently drops failed tasks"),
    "FL163": ("medium", "Nextflow boolean-like param used bare: CLI value 'false' is a truthy string"),
    "FL170": ("high", "exit 0 / sys.exit(0) in a script that records failures"),
    "FL171": ("high", "script records failures in handlers but has no non-zero exit path"),
    "FL180": ("medium", "output/cache path under $HOME or ~: home quota / non-scratch storage"),
    "FL181": ("low", "output path in /tmp: ephemeral, may be swept before harvest"),
    "FL230": ("high", "name used but never imported/defined in this file"),
    "FL240": ("medium", "shlex.quote on a string containing $: variable is never expanded"),
    "FL250": ("medium", "lenient parsing (errors='coerce'/'ignore', on_bad_lines='skip') turns bad data into NaN/drops rows"),
}
FL_OUT_RE = r"(save|savez|to_csv|to_parquet|to_pickle|write|dump|output|outdir|out_dir|embed|latent|cache|result|ckpt|checkpoint|\.npz|\.npy|\.h5ad|\.pt\b|mkdir|makedirs|workdir|scratch)"
FL_HOME_RE = r"(expanduser\(\s*['\"]~|Path\.home\(\)|['\"]~/|\$HOME|\$\{HOME\}|environ\[['\"]HOME['\"]\]|environ\.get\(\s*['\"]HOME|path\.expand\(\s*['\"]~|Sys\.getenv\(\s*['\"]HOME)"


def fl_exc(name="FailLoudError"):
    """Return (creating once) an exception class `name`, subclass of FailLoudError (RuntimeError)."""
    cache = fl_exc.__dict__.setdefault("cache", {})
    if "FailLoudError" not in cache:
        cache["FailLoudError"] = type("FailLoudError", (RuntimeError,), {})
    if name not in cache:
        cache[name] = type(name, (cache["FailLoudError"],), {})
    return cache[name]


def fl_raise(kind, msg, result=None):
    exc = fl_exc(kind)(msg)
    exc.result = result
    raise exc


def fl_rules():
    """Rule table: {rule_id: (severity, message)}."""
    return dict(FL_RULES)


def fl_add(findings, path, line, rule, text):
    sev, msg = FL_RULES[rule]
    findings.append({"file": path, "line": int(line), "rule": rule, "severity": sev,
                     "message": msg, "text": (text or "").strip()[:160]})


def fl_last(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def fl_dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def fl_is_listing(node):
    if not isinstance(node, ast.Call):
        return False
    nm = fl_last(node.func)
    if nm in ("listdir", "glob", "iglob", "rglob", "iterdir", "scandir"):
        return True
    if nm in ("list", "sorted", "tuple", "iter") and node.args:
        return fl_is_listing(node.args[0])
    return False


def fl_is_exit_call(node):
    return isinstance(node, ast.Call) and fl_dotted(node.func) in ("sys.exit", "exit", "quit", "os._exit", "abort")


def fl_exit_is_zero(node):
    if not node.args:
        return True
    a = node.args[0]
    return isinstance(a, ast.Constant) and a.value in (0, None)


def fl_terminal(body_nodes):
    """True if the statements re-raise or exit non-zero."""
    for st in body_nodes:
        for n in ast.walk(st):
            if isinstance(n, ast.Raise):
                return True
            if fl_is_exit_call(n) and not fl_exit_is_zero(n):
                return True
    return False


def fl_undefined_names(tree):
    bound = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__builtins__", "__package__", "__class__", "__path__", "host", "get_ipython", "display"}
    star = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                bound.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name == "*":
                    star = True
                bound.add(a.asname or a.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            bound.update(n.names)
        elif hasattr(ast, "MatchAs") and isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            bound.add(n.name)
        elif hasattr(ast, "MatchMapping") and isinstance(n, ast.MatchMapping) and n.rest:
            bound.add(n.rest)
    if star:
        return []
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound:
            out.setdefault(n.id, n.lineno)
    return sorted(out.items(), key=lambda kv: kv[1])


def fl_lint_py(path, text, findings):
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as e:
        fl_add(findings, path, e.lineno or 1, "FL001", "SyntaxError: %s" % e.msg)
        return
    lines = text.splitlines()

    def src(n):
        return lines[n - 1] if 0 < n <= len(lines) else ""

    listing_vars = set()
    len_checked = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and fl_is_listing(n.value):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    listing_vars.add(t.id)
        elif isinstance(n, ast.Call) and fl_last(n.func) == "len" and n.args and isinstance(n.args[0], ast.Name):
            len_checked.add(n.args[0].id)
    listing_vars -= len_checked  # a len() check on the matches counts as asserting uniqueness
    handlers = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler):
            handlers.append(n)
            broad = n.type is None
            types = n.type.elts if isinstance(n.type, ast.Tuple) else ([n.type] if n.type is not None else [])
            if any(fl_last(t) in ("Exception", "BaseException") for t in types):
                broad = True
            only_pass = all(isinstance(s, (ast.Pass, ast.Continue)) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)) for s in n.body)
            if broad and not fl_terminal(n.body):
                fl_add(findings, path, n.lineno, "FL101", src(n.lineno))
            elif only_pass:
                fl_add(findings, path, n.lineno, "FL102", src(n.lineno))
        elif isinstance(n, ast.Subscript):
            idx = n.slice
            if isinstance(idx, ast.UnaryOp) and isinstance(idx.op, ast.USub) and isinstance(idx.operand, ast.Constant):
                idx_val = -idx.operand.value
            elif isinstance(idx, ast.Constant):
                idx_val = idx.value
            else:
                idx_val = None
            if idx_val in (0, -1) and isinstance(idx_val, int):
                if fl_is_listing(n.value) or (isinstance(n.value, ast.Name) and n.value.id in listing_vars):
                    fl_add(findings, path, n.lineno, "FL110", src(n.lineno))
        elif isinstance(n, ast.Call):
            nm = fl_last(n.func)
            dn = fl_dotted(n.func)
            if nm == "next" and n.args and fl_is_listing(n.args[0]):
                fl_add(findings, path, n.lineno, "FL110", src(n.lineno))
            if nm == "parse_known_args":
                fl_add(findings, path, n.lineno, "FL130", src(n.lineno))
            if nm == "add_argument":
                for kw in n.keywords:
                    if kw.arg == "type" and isinstance(kw.value, ast.Name) and kw.value.id == "bool":
                        fl_add(findings, path, n.lineno, "FL131", src(n.lineno))
            for kw in n.keywords:
                if kw.arg == "fastmath" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    fl_add(findings, path, n.lineno, "FL150", src(n.lineno))
                if kw.arg == "errors" and isinstance(kw.value, ast.Constant) and kw.value.value in ("coerce", "ignore"):
                    fl_add(findings, path, n.lineno, "FL250", src(n.lineno))
                if kw.arg == "on_bad_lines" and isinstance(kw.value, ast.Constant) and kw.value.value == "skip":
                    fl_add(findings, path, n.lineno, "FL250", src(n.lineno))
                if kw.arg == "error_bad_lines" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                    fl_add(findings, path, n.lineno, "FL250", src(n.lineno))
            if dn in ("subprocess.run", "subprocess.call") and "returncode" not in text:
                chk = [kw for kw in n.keywords if kw.arg == "check"]
                if not (chk and isinstance(chk[0].value, ast.Constant) and chk[0].value.value is True):
                    fl_add(findings, path, n.lineno, "FL140", src(n.lineno))
            if dn == "shlex.quote" and n.args:
                a0 = n.args[0]
                has_dollar = any(isinstance(c, ast.Constant) and isinstance(c.value, str) and "$" in c.value for c in ast.walk(a0))
                if has_dollar:
                    fl_add(findings, path, n.lineno, "FL240", src(n.lineno))
        elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and fl_dotted(n.value.func) == "os.system":
            fl_add(findings, path, n.lineno, "FL140", src(n.lineno))
    # failure recording vs exit status
    recorders = []
    for h in handlers:
        if fl_terminal(h.body):
            continue
        seg = "\n".join(lines[h.lineno - 1:getattr(h, "end_lineno", h.lineno)])
        if re.search(r"fail|error|exception|traceback", seg, re.I):
            recorders.append(h)
    if recorders:
        zero_exits = [n for n in ast.walk(tree) if fl_is_exit_call(n) and fl_exit_is_zero(n) and n.args]
        for n in zero_exits:
            fl_add(findings, path, n.lineno, "FL170", src(n.lineno))
        any_nonzero = any(fl_is_exit_call(n) and not fl_exit_is_zero(n) for n in ast.walk(tree)) or any(
            isinstance(n, ast.Raise) and n.exc is not None and fl_last(n.exc.func if isinstance(n.exc, ast.Call) else n.exc) == "SystemExit"
            for n in ast.walk(tree))
        is_script = any(isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) and n.left.id == "__name__" for n in ast.walk(tree)) or any(
            fl_last(n.func) == "ArgumentParser" for n in ast.walk(tree) if isinstance(n, ast.Call))
        if is_script and not any_nonzero:
            fl_add(findings, path, recorders[0].lineno, "FL171", src(recorders[0].lineno))
    for nm, ln in fl_undefined_names(tree):
        fl_add(findings, path, ln, "FL230", "%s (line: %s)" % (nm, src(ln).strip()))
    fl_lint_lines(path, lines, findings, "py")


def fl_lint_lines(path, lines, findings, lang):
    cm = "//" if lang == "nf" else "#"
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if not s or s.startswith(cm):
            continue
        if lang in ("py", "sh", "r") and re.search(FL_HOME_RE, ln) and re.search(FL_OUT_RE, ln, re.I):
            fl_add(findings, path, i, "FL180", ln)
        if lang in ("py", "sh") and re.search(r"(?<![\w.])/tmp/", ln) and re.search(FL_OUT_RE, ln, re.I) and not re.search(r"mkstemp|NamedTemporary|tempfile", ln):
            fl_add(findings, path, i, "FL181", ln)


def fl_lint_sh(path, text, findings):
    lines = text.splitlines()
    shebang = bool(lines) and lines[0].startswith("#!")
    has_e = False
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if s.startswith("#"):
            continue
        if re.search(r"^\s*set\s+-[a-zA-Z]*e", ln) or re.search(r"set\s+-o\s+errexit", ln):
            has_e = True
        if re.search(r"^\s*set\s+\+[a-zA-Z]*e", ln) or re.search(r"set\s+\+o\s+errexit", ln):
            fl_add(findings, path, i, "FL120", ln)
        if re.search(r"\|\|\s*(true|:)\s*($|[;&)#])", ln):
            fl_add(findings, path, i, "FL121", ln)
        if re.search(r"\bls\b[^|;]*\|\s*head\s+(-n\s*)?-?1\b", ln) or re.search(r"\$\(\s*ls\s", ln):
            fl_add(findings, path, i, "FL111", ln)
        if re.search(r"(2>\s*/dev/null|&>\s*/dev/null|>\s*/dev/null\s+2>&1)", ln) and re.search(r"\b(activate|git|conda|pip|sbatch|rsync|scp)\b", ln):
            fl_add(findings, path, i, "FL123", ln)
        if re.search(r"\bexit\s+0\b", ln):
            prev = "\n".join(lines[max(0, i - 4):i - 1])
            if re.search(r"fail|error", prev + " " + ln, re.I) and not re.search(r"^\s*#", ln):
                fl_add(findings, path, i, "FL170", ln)
    if shebang and not has_e and not any(re.search(r"^\s*set\s+\+[a-zA-Z]*e", l) for l in lines):
        fl_add(findings, path, 1, "FL122", lines[0])
    fl_lint_lines(path, lines, findings, "sh")


def fl_nf_processes(text):
    """Return [(name, body_text, body_start_line)] for `process NAME { ... }` with brace matching that skips strings/comments."""
    out = []
    n = len(text)
    tq = ["'" * 3, '"' * 3]
    for m in re.finditer(r"(?m)^[ \t]*process\s+(\w+)\s*\{", text):
        j = m.end()
        k = j
        depth = 1
        while k < n and depth > 0:
            if text.startswith("//", k):
                e = text.find("\n", k)
                k = n if e < 0 else e
                continue
            if text.startswith("/*", k):
                e = text.find("*/", k + 2)
                k = n if e < 0 else e + 2
                continue
            hit = None
            for t in tq:
                if text.startswith(t, k):
                    hit = t
            if hit:
                e = text.find(hit, k + 3)
                k = n if e < 0 else e + 3
                continue
            c = text[k]
            if c in "\"'":
                k += 1
                while k < n and text[k] != c and text[k] != "\n":
                    k += 2 if text[k] == "\\" else 1
                k += 1
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            k += 1
        out.append((m.group(1), text[j:k - 1], text.count("\n", 0, j) + 1))
    return out


def fl_lint_nf(path, text, findings):
    lines = text.splitlines()
    for name, body, start in fl_nf_processes(text):
        blines = body.split("\n")
        for off, bl in enumerate(blines):
            if re.match(r"^\s*(script|shell|exec)\s*:", bl):
                break
            if bl.strip().startswith("//"):
                continue
            if re.match(r"^\s*executor\s+\S", bl):
                fl_add(findings, path, start + off, "FL160", bl)
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if s.startswith("//"):
            continue
        if re.search(r"ifEmpty\(\s*(\[\s*\]|null)\s*\)", ln):
            fl_add(findings, path, i, "FL161", ln)
        if re.search(r"errorStrategy.*['\"]ignore['\"]", ln):
            fl_add(findings, path, i, "FL162", ln)
        m = re.search(r"\bparams\.((?:allow|enable|skip|use|force|dry|no|require|disable)_\w+)", ln)
        if m and re.search(r"\bif\b|\?|&&|\|\||!", ln) and not re.search(r"toBoolean|==|!=|toString", ln):
            fl_add(findings, path, i, "FL163", ln)
    fl_lint_lines(path, lines, findings, "nf")


def fl_lint_r(path, text, findings):
    lines = text.splitlines()
    for m in re.finditer(r"error\s*=\s*function\s*\([^)]*\)", text):
        rest = text[m.end():]
        stripped = rest.lstrip(" \t\r\n")
        if stripped.startswith("{"):
            depth, k = 0, rest.index("{")
            while k < len(rest):
                depth += 1 if rest[k] == "{" else (-1 if rest[k] == "}" else 0)
                if depth == 0:
                    break
                k += 1
            tail = rest[:k + 1]
        else:
            tail = rest.split("\n", 1)[0]
        if not re.search(r"\bstop\(|\bquit\(|\bq\(", tail):
            fl_add(findings, path, text.count("\n", 0, m.start()) + 1, "FL103", lines[text.count("\n", 0, m.start())])
    for i, ln in enumerate(lines, 1):
        if ln.strip().startswith("#"):
            continue
        if re.search(r"\btry\s*\(.*silent\s*=\s*(TRUE|T)\b", ln):
            fl_add(findings, path, i, "FL103", ln)
    fl_lint_lines(path, lines, findings, "r")


def fl_collect_files(paths, exclude=None):
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    exclude = list(exclude or [])
    files = []
    for p in paths:
        p = os.fspath(p)
        if os.path.isdir(p):
            for root, dirs, fs in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in FL_SKIP_DIRS)
                for f in sorted(fs):
                    if os.path.splitext(f)[1].lower() in FL_LANG_EXT:
                        files.append(os.path.join(root, f))
        elif os.path.isfile(p):
            if os.path.splitext(p)[1].lower() not in FL_LANG_EXT:
                raise ValueError("fl_lint: unsupported file type %r (supported: %s)" % (p, sorted(FL_LANG_EXT)))
            files.append(p)
        else:
            raise FileNotFoundError("fl_lint: path does not exist: %r" % p)
    files = [f for f in files if not any(x in f for x in exclude)]
    if not files:
        raise ValueError("fl_lint: 0 lintable files under %r - a scan of nothing proves nothing" % (paths,))
    return files


def fl_lint(paths, fail_on="medium", strict=True, exclude=None):
    """Static scan of .py/.sh/.nf/.R files for silent-failure patterns.

    Returns {ok, findings, suppressed, files_scanned, counts, fail_on}. Raises FLLintError when strict and any
    unsuppressed finding has severity >= fail_on. Suppress inline: `# fl: allow FL101 <reason>` on the line or the
    comment-only line above (`// fl: allow ...` in .nf). A reason is mandatory (else FL000, which cannot be suppressed).
    """
    if fail_on not in FL_SEV:
        raise ValueError("fail_on must be one of %s" % sorted(FL_SEV))
    files = fl_collect_files(paths, exclude)
    findings, suppressed = [], []
    for f in files:
        with open(f, encoding="utf-8", errors="strict") as fh:
            text = fh.read()
        lang = FL_LANG_EXT[os.path.splitext(f)[1].lower()]
        raw = []
        {"py": fl_lint_py, "sh": fl_lint_sh, "nf": fl_lint_nf, "r": fl_lint_r}[lang](f, text, raw)
        lines = text.splitlines()
        allow = {}
        for i, ln in enumerate(lines, 1):
            m = re.search(FL_SUPPRESS_RE, ln)
            if not m:
                continue
            ids = [x for x in m.group(1).split(",") if x]
            reason = m.group(2).strip(" -:\u2014\t")
            if len(reason) < 3 or any(x not in FL_RULES or x == "FL000" for x in ids):
                fl_add(raw, f, i, "FL000", ln)
                continue
            allow[i] = {x: reason for x in ids}
        for fd in raw:
            L = fd["line"]
            reason = None
            if fd["rule"] != "FL000":
                if L in allow and fd["rule"] in allow[L]:
                    reason = allow[L][fd["rule"]]
                elif (L - 1) in allow and L >= 2 and lines[L - 2].lstrip().startswith(("#", "//")) and fd["rule"] in allow[L - 1]:
                    reason = allow[L - 1][fd["rule"]]
            if reason:
                fd = dict(fd, reason=reason)
                suppressed.append(fd)
            else:
                findings.append(fd)
    findings.sort(key=lambda d: (d["file"], d["line"], d["rule"]))
    counts = {}
    for fd in findings:
        counts[fd["rule"]] = counts.get(fd["rule"], 0) + 1
    blocking = [fd for fd in findings if FL_SEV[fd["severity"]] >= FL_SEV[fail_on]]
    res = {"ok": not blocking, "findings": findings, "suppressed": suppressed, "files_scanned": len(files),
           "counts": counts, "fail_on": fail_on}
    if strict and blocking:
        shown = "\n".join("  %s:%d [%s/%s] %s" % (d["file"], d["line"], d["rule"], d["severity"], d["message"]) for d in blocking[:25])
        fl_raise("FLLintError", "fl_lint: %d blocking finding(s) in %d files (fail_on=%s):\n%s%s\nFix, or suppress with '# fl: allow <RULE> <reason>'." % (
            len(blocking), len(files), fail_on, shown, "\n  ... (%d more)" % (len(blocking) - 25) if len(blocking) > 25 else ""), res)
    return res


def fl_suggest(name, candidates, n=5):
    cands = [str(c) for c in candidates]
    name = str(name)
    low = name.lower()
    strong = [c for c in cands if c.lower() == low or (len(low) >= 3 and (low in c.lower() or c.lower() in low))]
    strong.sort(key=lambda c: abs(len(c) - len(name)))
    fuzzy = [c for c in difflib.get_close_matches(name, cands, n=n, cutoff=0.6) if c not in strong]
    return (strong + fuzzy)[:n]


def fl_norm_key(k):
    return str(k).lstrip("-").replace("-", "_")


def fl_parser_actions(parser):
    acts = []
    for a in parser._actions:
        if hasattr(a, "choices") and isinstance(getattr(a, "choices", None), dict) and a.__class__.__name__ == "_SubParsersAction":
            for sp in a.choices.values():
                acts.extend(fl_parser_actions(sp))
        else:
            acts.append(a)
    return acts


def fl_args_consumed(config, parser, strict=True, ignore=None, provided=None, check_required=True):
    """Fail on config keys (or argv tokens) the argparse parser does not define, and on required args absent from config.

    config: dict of {key: value}, or a list of argv tokens like ['--lr', '1e-3', '--seed=1'].
    ignore: keys the launcher (not the runner) consumes - must be listed explicitly.
    provided: dests the launcher supplies by other means (e.g. env). Returns {ok, unknown, missing_required, consumed}.
    """
    ignore = set(fl_norm_key(k) for k in (ignore or []))
    provided = set(fl_norm_key(k) for k in (provided or []))
    if isinstance(config, dict):
        keys = [fl_norm_key(k) for k in config.keys()]
    elif isinstance(config, (list, tuple)):
        keys = []
        for t in config:
            t = str(t)
            if t.startswith("-") and not re.match(r"^-\d", t):
                keys.append(fl_norm_key(t.split("=", 1)[0]))
    else:
        raise TypeError("fl_args_consumed: config must be a dict or an argv token list, got %s" % type(config).__name__)
    acts = [a for a in fl_parser_actions(parser) if a.__class__.__name__ != "_HelpAction"]
    known = {}
    for a in acts:
        known[fl_norm_key(a.dest)] = a
        for o in a.option_strings:
            known[fl_norm_key(o)] = a
    unknown, consumed = {}, []
    for k in keys:
        if k in ignore:
            continue
        if k in known:
            consumed.append(k)
        else:
            unknown[k] = fl_suggest(k, known.keys())
    missing = []
    if check_required:
        have = set(keys) | provided
        for a in acts:
            if a.option_strings and getattr(a, "required", False):
                names = {fl_norm_key(a.dest)} | {fl_norm_key(o) for o in a.option_strings}
                if not (names & have):
                    missing.append(a.dest)
    res = {"ok": not unknown and not missing, "unknown": unknown, "missing_required": missing, "consumed": consumed}
    if strict and not res["ok"]:
        parts = []
        if unknown:
            parts.append("keys the parser does not define (silently dropped by parse_known_args-style runners): " + "; ".join(
                "%s -> did you mean %s?" % (k, s) if s else "%s -> no close match" % k for k, s in unknown.items()))
        if missing:
            parts.append("required args absent from config: %s" % missing)
        fl_raise("FLArgsError", "fl_args_consumed: " + " | ".join(parts), res)
    return res


def fl_row_list(rows):
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict(orient="records")
    rows = list(rows)
    for r in rows:
        if not isinstance(r, dict):
            raise TypeError("fl_unique_outputs: rows must be dicts (or a DataFrame), got %s" % type(r).__name__)
    return rows


def fl_unique_outputs(rows, path_template_or_fn, swept_keys, strict=True, reserved_paths=None, allow_duplicates=False):
    """Fail if two configs map to one output path, or a swept key does not influence the path.

    path_template_or_fn: 'emb/{dataset}_{model}_s{seed}.npz' or callable(row)->str. A resume/dedup key function
    can be passed here too: if it collides, resume will skip configs that were never run.
    reserved_paths: paths owned by other experiments (a collision overwrites their outputs).
    """
    import string
    rows = fl_row_list(rows)
    if not rows:
        raise ValueError("fl_unique_outputs: no rows - nothing to check")
    swept_keys = list(swept_keys)
    missing_keys, paths = [], []
    fields = set()
    if isinstance(path_template_or_fn, str):
        fields = {f for _, f, _, _ in string.Formatter().parse(path_template_or_fn) if f}
        fn = lambda r: path_template_or_fn.format(**r)
    elif callable(path_template_or_fn):
        fn = path_template_or_fn
    else:
        raise TypeError("path_template_or_fn must be a str template or callable")
    for i, r in enumerate(rows):
        try:
            paths.append(os.path.normpath(str(fn(r))))
        except KeyError as e:
            missing_keys.append((i, str(e)))
            paths.append(None)
    sweep_absent = [k for k in swept_keys if not any(k in r for r in rows)]
    not_in_template = [k for k in swept_keys if fields and k not in fields]
    single_valued = [k for k in swept_keys if k not in sweep_absent and len({repr(r.get(k)) for r in rows}) < 2]
    by_path = {}
    for i, p in enumerate(paths):
        if p is not None:
            by_path.setdefault(p, []).append(i)
    duplicates, collisions = [], []
    for p, idx in by_path.items():
        if len(idx) > 1:
            if len({repr(sorted(rows[i].items(), key=lambda kv: str(kv[0]))) for i in idx}) == 1:
                duplicates.append({"path": p, "rows": idx})
            else:
                collisions.append({"path": p, "rows": idx, "differing_keys": sorted(
                    k for k in set().union(*[rows[i].keys() for i in idx]) if len({repr(rows[i].get(k)) for i in idx}) > 1)})
    not_encoded = []
    for k in swept_keys:
        if k in sweep_absent:
            continue
        groups = {}
        for i, r in enumerate(rows):
            if paths[i] is None:
                continue
            sig = tuple(sorted((str(kk), repr(v)) for kk, v in r.items() if kk != k))
            groups.setdefault(sig, {}).setdefault(paths[i], set()).add(repr(r.get(k)))
        if any(len(vals) > 1 for g in groups.values() for vals in g.values()):
            not_encoded.append(k)
    reserved = set(os.path.normpath(str(p)) for p in (reserved_paths or []))
    reserved_hits = sorted(p for p in by_path if p in reserved)
    warnings = ["swept key %r has a single value - not actually swept" % k for k in single_valued]
    bad = bool(missing_keys or sweep_absent or not_in_template or collisions or not_encoded or reserved_hits or (duplicates and not allow_duplicates))
    res = {"ok": not bad, "n_rows": len(rows), "n_unique_paths": len(by_path), "collisions": collisions[:20],
           "duplicates": duplicates[:20], "keys_not_in_path": sorted(set(not_in_template) | set(not_encoded)),
           "swept_keys_absent_from_rows": sweep_absent, "rows_missing_template_keys": missing_keys[:20],
           "reserved_collisions": reserved_hits[:20], "warnings": warnings}
    if strict and bad:
        msg = ["fl_unique_outputs: %d rows -> %d distinct paths." % (len(rows), len(by_path))]
        if res["keys_not_in_path"]:
            msg.append("swept keys that do not change the path (later configs overwrite earlier): %s" % res["keys_not_in_path"])
        if collisions:
            msg.append("collisions e.g. %s <- rows %s (differ in %s)" % (collisions[0]["path"], collisions[0]["rows"][:6], collisions[0]["differing_keys"]))
        if duplicates and not allow_duplicates:
            msg.append("%d duplicate identical configs e.g. %s" % (len(duplicates), duplicates[0]["path"]))
        if sweep_absent:
            msg.append("swept keys absent from every row: %s" % sweep_absent)
        if missing_keys:
            msg.append("rows lacking template keys: %s" % missing_keys[:3])
        if reserved_hits:
            msg.append("paths owned by another experiment: %s" % reserved_hits[:3])
        fl_raise("FLUniqueOutputsError", " ".join(msg), res)
    return res


def fl_check_array(arr, label):
    import numpy as np
    arr = np.asarray(arr)
    if arr.size == 0:
        return "%s: empty array shape=%s" % (label, arr.shape)
    if arr.dtype.kind in "fc":
        bad = int((~np.isfinite(arr)).sum())
        if bad:
            return "%s: %d/%d non-finite (%.1f%%)" % (label, bad, arr.size, 100.0 * bad / arr.size)
    elif arr.dtype.kind not in "iub":
        return "%s: non-numeric dtype %s" % (label, arr.dtype)
    return None


def fl_check_one(spec, full, check_finite, allow_unchecked, rep):
    ext = os.path.splitext(full)[1].lower()
    fin = spec.get("finite", check_finite)
    p = spec["path"]
    if ext == ".npy":
        import numpy as np
        msg = fl_check_array(np.load(full, allow_pickle=False), p) if fin else None
        if msg:
            rep["nonfinite"].append(msg)
    elif ext == ".npz":
        import numpy as np
        z = np.load(full, allow_pickle=False)
        names = list(z.files)
        if not names:
            rep["empty"].append("%s: npz has no arrays" % p)
        for k in spec.get("keys", []):
            if k not in names:
                rep["missing"].append("%s: required key %r absent (has %s)" % (p, k, names))
        for k in names:
            msg = fl_check_array(z[k], "%s[%s]" % (p, k)) if fin else None
            if msg:
                rep["nonfinite"].append(msg)
    elif ext == ".h5ad":
        try:
            import anndata
        except ImportError:
            anndata = None
        if anndata is None:
            (rep["warnings"] if allow_unchecked else rep["unverifiable"]).append("%s: anndata not importable, cannot check finiteness" % p)
            return
        ad = anndata.read_h5ad(full)
        want = spec.get("obsm", None)
        keys = list(ad.obsm.keys())
        if want not in (None, True, False):
            for k in want:
                if k not in keys:
                    rep["missing"].append("%s: obsm[%r] absent (has %s)" % (p, k, keys))
            keys = [k for k in keys if k in want]
        elif want is True and not keys:
            rep["missing"].append("%s: obsm is empty (an embedding output must have obsm entries)" % p)
        elif want is None and not keys:
            rep["warnings"].append("%s: obsm is empty; pass obsm=True/[keys] if this file should carry an embedding" % p)
        for k in keys:
            msg = fl_check_array(ad.obsm[k], "%s obsm[%s]" % (p, k)) if fin else None
            if msg:
                rep["nonfinite"].append(msg)
        if spec.get("check_X") and fin:
            X = ad.X.data if hasattr(ad.X, "data") and hasattr(ad.X, "indptr") else ad.X
            msg = fl_check_array(X, "%s X" % p)
            if msg:
                rep["nonfinite"].append(msg)
    elif ext in (".csv", ".tsv"):
        import pandas as pd
        try:
            df = pd.read_csv(full, sep="," if ext == ".csv" else "\t")
        except pd.errors.EmptyDataError:
            rep["empty"].append("%s: no header/columns (headerless or empty CSV)" % p)
            return
        if len(df) < spec.get("min_rows", 1):
            rep["empty"].append("%s: %d data rows (< min_rows=%d)" % (p, len(df), spec.get("min_rows", 1)))
        num = df.select_dtypes("number")
        nn = [c for c in num.columns if num[c].isna().any()]
        if nn:
            (rep["nonfinite"] if spec.get("finite") else rep["warnings"]).append("%s: NaN in columns %s" % (p, nn[:8]))
    elif ext == ".json":
        with open(full) as fh:
            obj = json.load(fh)
        if obj in ({}, [], None, ""):
            rep["empty"].append("%s: json is empty" % p)


def fl_failed_rows(results_csv, expected_rows, rep):
    import pandas as pd
    try:
        df = pd.read_csv(results_csv)
    except (pd.errors.EmptyDataError, FileNotFoundError) as e:
        rep["failed_rows"].append("%s: unreadable results csv (%s)" % (results_csv, type(e).__name__))
        return
    if len(df) == 0:
        rep["failed_rows"].append("%s: results csv has a header but no rows" % results_csv)
        return
    if expected_rows is not None and len(df) != expected_rows:
        rep["failed_rows"].append("%s: %d rows, expected %d" % (results_csv, len(df), expected_rows))
    cols = {c.lower(): c for c in df.columns}
    found = False
    for name in ("status", "state", "outcome", "result"):
        if name in cols:
            found = True
            s = df[cols[name]].astype(str).str.lower()
            bad = s.str.contains(r"fail|error|oom|timeout|timed_out|killed|cancel|diverg|nan", regex=True)
            if bad.any():
                rep["failed_rows"].append("%s: %d rows with failing %s (e.g. %s)" % (results_csv, int(bad.sum()), cols[name], sorted(set(s[bad]))[:3]))
    for name in ("error", "exception", "traceback", "err"):
        if name in cols:
            found = True
            e = df[cols[name]]
            bad = e.notna() & (e.astype(str).str.strip() != "")
            if bad.any():
                rep["failed_rows"].append("%s: %d rows with non-empty %s" % (results_csv, int(bad.sum()), cols[name]))
    for name in ("failed", "failure"):
        if name in cols:
            found = True
            bad = df[cols[name]].astype(str).str.lower().isin(["true", "1", "yes"])
            if bad.any():
                rep["failed_rows"].append("%s: %d rows with %s=True" % (results_csv, int(bad.sum()), cols[name]))
    for name in ("success", "ok", "succeeded"):
        if name in cols:
            found = True
            bad = df[cols[name]].astype(str).str.lower().isin(["false", "0", "no"])
            if bad.any():
                rep["failed_rows"].append("%s: %d rows with %s=False" % (results_csv, int(bad.sum()), cols[name]))
    for name in ("returncode", "exit_code", "exitcode"):
        if name in cols:
            found = True
            bad = pd.to_numeric(df[cols[name]], errors="coerce").fillna(-1) != 0
            if bad.any():
                rep["failed_rows"].append("%s: %d rows with non-zero %s" % (results_csv, int(bad.sum()), cols[name]))
    if not found:
        rep["warnings"].append("%s: no status/error/failed/success/returncode column - failed rows cannot be detected" % results_csv)


def fl_completion_gate(expected, found_dir, check_finite=True, results_csv=None, strict=True, done_marker=None,
                       expected_rows=None, allow_unchecked=False):
    """Return ok only if every expected output exists, is non-empty, is finite (npy/npz/h5ad obsm), and no failed rows are recorded.

    expected: list of relative paths or dicts {path, min_bytes, keys, obsm, finite, min_rows, check_X}. No globs: enumerate
    exactly what a complete run produces (every seed/config), so a partial shard cannot pass.
    Call BEFORE writing the .done marker; with done_marker=<path> the marker is written only on success, and a stale marker
    left by an earlier run is reported (never silently trusted).
    """
    expected = list(expected)
    if not expected:
        raise ValueError("fl_completion_gate: empty expected list - a gate over nothing always passes")
    rep = {"missing": [], "empty": [], "nonfinite": [], "unreadable": [], "unverifiable": [], "failed_rows": [], "warnings": []}
    for e in expected:
        spec = {"path": e} if isinstance(e, (str, os.PathLike)) else dict(e)
        spec["path"] = os.fspath(spec["path"])
        if any(c in spec["path"] for c in "*?["):
            raise ValueError("fl_completion_gate: glob in expected path %r - list exact files" % spec["path"])
        full = spec["path"] if os.path.isabs(spec["path"]) else os.path.join(found_dir, spec["path"])
        if not os.path.isfile(full):
            rep["missing"].append(spec["path"])
            continue
        if os.path.getsize(full) < spec.get("min_bytes", 1):
            rep["empty"].append("%s: %d bytes" % (spec["path"], os.path.getsize(full)))
            continue
        try:
            fl_check_one(spec, full, check_finite, allow_unchecked, rep)
        except Exception as ex:
            rep["unreadable"].append("%s: %s: %s" % (spec["path"], type(ex).__name__, ex))
    if results_csv is not None:
        rc = results_csv if os.path.isabs(results_csv) else os.path.join(found_dir, results_csv)
        fl_failed_rows(rc, expected_rows, rep)
    problems = {k: v for k, v in rep.items() if k != "warnings" and v}
    stale = None
    if done_marker and os.path.exists(done_marker) and problems:
        stale = done_marker
    res = dict(rep, ok=not problems, n_expected=len(expected), stale_marker=stale)
    if strict and problems:
        msg = "; ".join("%s(%d): %s" % (k, len(v), v[:3]) for k, v in problems.items())
        if stale:
            msg += "; STALE marker %s exists from an earlier run - remove it so --resume re-runs this shard" % stale
        fl_raise("FLCompletionError", "fl_completion_gate: %d/%d expected outputs failed - %s. Do NOT write .done." % (
            sum(len(v) for k, v in problems.items() if k in ("missing", "empty", "unreadable")) or len(problems), len(expected), msg), res)
    if res["ok"] and done_marker:
        with open(done_marker, "w") as fh:
            json.dump({"n_expected": len(expected), "warnings": rep["warnings"]}, fh)
    return res


def fl_columns(obj, names, values=None, where="auto", strict=True):
    """Exact-name check of columns (DataFrame, or AnnData obs/var/obsm/layers/uns) with close-match suggestions.

    values: optional {column: [expected values]} - each must occur exactly in the column (catches 'scVI' vs 'scVI (LDVAE)').
    Warns (result['shadowed']) for names that collide with DataFrame attributes (df.head), which silently resolve to methods.
    """
    if isinstance(names, str):
        raise TypeError("fl_columns: names must be a list of column names, not a single string")
    names = list(names)
    pools = {}
    if hasattr(obj, "obs") and hasattr(obj, "var"):
        w = ["obs"] if where == "auto" else ([where] if isinstance(where, str) else list(where))
        for part in w:
            pools[part] = list(getattr(obj, part).columns) if part in ("obs", "var") else list(getattr(obj, part).keys())
        frames = {"obs": obj.obs, "var": obj.var}
    elif hasattr(obj, "columns"):
        pools["columns"] = list(obj.columns)
        frames = {"columns": obj}
    else:
        raise TypeError("fl_columns: expected a DataFrame or AnnData, got %s" % type(obj).__name__)
    allcols = [c for v in pools.values() for c in v]
    missing = {n: fl_suggest(n, allcols) for n in names if n not in allcols}
    dups = sorted({c for c in allcols if allcols.count(c) > 1 and c in names})
    bad_values = {}
    for col, vals in (values or {}).items():
        part = next((p for p in frames if col in pools.get(p, [])), None)
        if part is None:
            continue
        actual = list(frames[part][col].dropna().unique())
        miss = {v: fl_suggest(v, actual) for v in vals if v not in actual}
        if miss:
            bad_values[col] = miss
    try:
        import pandas as pd
        shadowed = [n for n in names if isinstance(n, str) and hasattr(pd.DataFrame, n)]
    except ImportError:
        shadowed = []
    res = {"ok": not missing and not bad_values and not dups, "missing": missing, "bad_values": bad_values,
           "duplicated": dups, "shadowed": shadowed, "available": allcols[:60]}
    if strict and not res["ok"]:
        parts = []
        if missing:
            parts.append("absent columns: " + "; ".join("%r -> %s" % (k, v or "no close match") for k, v in missing.items()))
        if bad_values:
            parts.append("absent values: " + "; ".join("%s: %s" % (c, {k: v or "no close match" for k, v in m.items()}) for c, m in bad_values.items()))
        if dups:
            parts.append("duplicated column names: %s" % dups)
        fl_raise("FLColumnsError", "fl_columns: " + " | ".join(parts), res)
    return res


def fl_version_ok(installed, spec):
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
    spec = spec.strip()
    if spec[:1].isdigit():
        spec = "==" + spec
    return Version(installed) in SpecifierSet(spec, prereleases=True)


def fl_env_guard(require_cuda=True, packages=None, imports=None, env_vars=None, not_under_home=None,
                 require_thread_caps=False, probe_alloc=True, strict=True):
    """Assert the runtime is what the job assumes, before launching hours of work.

    packages: {'torch': '2.4.1', 'numpy': '>=1.26,<3'} exact or specifier. imports: module names that must all import
    (reports every failure, not the first). env_vars: names that must be set and non-empty in THIS process (children
    inherit only exported vars). not_under_home: env vars whose value must not be inside ~ (cache/scratch dirs).
    require_cuda: torch must be a CUDA build, see >=1 GPU and allocate on it.
    """
    problems, info = [], {}
    if require_cuda:
        try:
            torch = importlib.import_module("torch")
        except Exception as ex:
            torch = None
            problems.append("torch not importable (%s: %s) but require_cuda=True" % (type(ex).__name__, ex))
        if torch is not None:
            ver = str(getattr(torch, "__version__", "?"))
            cu = getattr(getattr(torch, "version", None), "cuda", None)
            info["torch"] = ver
            info["torch_cuda_build"] = cu
            if cu is None or "+cpu" in ver:
                problems.append("torch %s is a CPU-only build (torch.version.cuda=%r); a conda fork/solve probably replaced the CUDA wheel" % (ver, cu))
            elif not torch.cuda.is_available() or torch.cuda.device_count() < 1:
                problems.append("torch %s has CUDA %s but no GPU is visible (CUDA_VISIBLE_DEVICES=%r)" % (ver, cu, os.environ.get("CUDA_VISIBLE_DEVICES")))
            else:
                info["gpus"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
                if probe_alloc:
                    try:
                        torch.zeros(1, device="cuda")
                    except Exception as ex:
                        problems.append("allocating on cuda failed: %s: %s" % (type(ex).__name__, ex))
    for name, spec in (packages or {}).items():
        try:
            from importlib import metadata
            inst = metadata.version(name)
        except Exception:
            try:
                inst = str(importlib.import_module(name).__version__)
            except Exception:
                problems.append("package %s not installed (want %s)" % (name, spec))
                continue
        info["pkg:" + name] = inst
        try:
            if not fl_version_ok(inst, spec):
                problems.append("package %s==%s does not satisfy %r" % (name, inst, spec))
        except ImportError:
            problems.append("cannot compare versions: 'packaging' not installed")
    failed_imports = {}
    for m in (imports or []):
        try:
            importlib.import_module(m)
        except Exception as ex:
            failed_imports[m] = "%s: %s" % (type(ex).__name__, str(ex)[:120])
    if failed_imports:
        problems.append("%d/%d required imports failed: %s" % (len(failed_imports), len(list(imports)), failed_imports))
    for v in (env_vars or []):
        if not os.environ.get(v):
            problems.append("env var %s unset/empty in this process (not exported to children?)" % v)
    home = os.path.realpath(os.path.expanduser("~"))
    for v in (not_under_home or []):
        val = os.environ.get(v)
        if not val:
            problems.append("env var %s unset (defaults usually land in $HOME)" % v)
        elif os.path.realpath(os.path.expanduser(val)).startswith(home + os.sep):
            problems.append("env var %s=%s is under $HOME" % (v, val))
    if require_thread_caps:
        for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            if not os.environ.get(v):
                problems.append("thread cap %s unset: parallel metric jobs will oversubscribe cores" % v)
    res = {"ok": not problems, "problems": problems, "info": info, "failed_imports": failed_imports}
    if strict and problems:
        fl_raise("FLEnvError", "fl_env_guard: %d problem(s): %s" % (len(problems), " | ".join(problems)), res)
    return res


def fl_mutation_check(check_fn, good_input, bad_inputs, strict=True, wrong_reason_types=None):
    """Prove a test/verifier can fail: it must accept good_input and reject every bad input, for the right reason.

    check_fn(x): 'passes' if it returns without raising and not False / {'ok': False}; 'fails' if it raises or returns that.
    bad_inputs: list, or {name: input} or {name: (input, regex_that_failure_message_must_match)}.
    Failures caused by harness errors (NameError, FileNotFoundError, ...) are reported as wrong_reason, not as catches.
    Use good_input from the REAL producer, not a fixture built in the checker's own convention.
    """
    if wrong_reason_types is None:
        wrong_reason_types = ["NameError", "AttributeError", "TypeError", "ImportError", "ModuleNotFoundError", "FileNotFoundError",
                              "KeyError", "IndexError", "SyntaxError", "UnboundLocalError"]
    if not bad_inputs:
        raise ValueError("fl_mutation_check: need >=1 known-bad input - without one the check is unfalsified")
    if not isinstance(bad_inputs, dict):
        bad_inputs = {"bad_%d" % i: b for i, b in enumerate(bad_inputs)}

    def run(x):
        try:
            r = check_fn(x)
        except Exception as ex:
            return False, type(ex).__name__, "%s: %s" % (type(ex).__name__, ex)
        if r is False or (isinstance(r, dict) and r.get("ok") is False):
            return False, "returned-false", str(r)[:200]
        return True, None, None

    gp, gt, gm = run(good_input)
    caught, missed, wrong = {}, [], {}
    for name, item in bad_inputs.items():
        pat = None
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], str):
            item, pat = item
        ok, et, msg = run(item)
        if ok:
            missed.append(name)
        elif et in wrong_reason_types or (pat and not re.search(pat, msg or "")):
            wrong[name] = msg
        else:
            caught[name] = msg
    res = {"ok": gp and not missed and not wrong, "good_passed": gp, "good_error": None if gp else gm,
           "caught": caught, "missed": missed, "wrong_reason": wrong}
    if strict and not res["ok"]:
        parts = []
        if not gp:
            parts.append("check REJECTS the known-good input (%s) - over-strict or broken harness" % gm)
        if missed:
            parts.append("check ACCEPTS known-bad inputs %s - vacuous/blind verifier" % missed)
        if wrong:
            parts.append("check failed for the wrong reason on %s" % {k: v[:80] for k, v in wrong.items()})
        fl_raise("FLMutationError", "fl_mutation_check: " + " | ".join(parts), res)
    return res
