import hashlib
import json
import math
import os
import re
import subprocess
from datetime import datetime, timezone

CG_VERSION = "1"
CG_PLACEHOLDER = r"\{\{\s*([A-Za-z0-9_.\-]+)\s*(?::([^{}]*))?\}\}"
CG_NUMBER = r"(?<![\w.])([-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?)(%?)(?![A-Za-z_])"
CG_RANGE = r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:\u2013|\u2014|-|to)\s*(\d+(?:\.\d+)?)(%?)(?![\w])"
CG_QUANT = r"\b(all|every|each|always|never|only|none|monotonic(?:ally)?|consistently|uniformly|top[- ]?\d+|tops|identical|without exception|in every|across all)\b"
CG_YEAR = r"(?:19|20)\d\d"
CG_DATE = r"\d{4}-\d{2}-\d{2}"


# ---------------------------------------------------------------- plumbing
def cg_error(kind="base"):
    """Exception class for a check kind; all subclass ClaimGateError (an AssertionError)."""
    cache = cg_error.__dict__.setdefault("cache", {})
    if "base" not in cache:
        cache["base"] = type("ClaimGateError", (AssertionError,), {})
    if kind not in cache:
        cache[kind] = type("CG" + kind.title() + "Error", (cache["base"],), {})
    return cache[kind]


def cg_fail(kind, message):
    raise cg_error(kind)("[claim-gate:" + kind + "] " + message)


def cg_sha256(path):
    """sha256 hex of a file. Call BEFORE editing to get the before_sha256 for cg_changed."""
    if not os.path.isfile(path):
        cg_fail("readback", "not a file: %s" % path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cg_source(path):
    st = os.stat(path)
    return {
        "path": os.path.abspath(path),
        "sha256": cg_sha256(path),
        "size": st.st_size,
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def cg_has_nan(value):
    if isinstance(value, float):
        return math.isnan(value)
    if isinstance(value, dict):
        return any(cg_has_nan(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(cg_has_nan(v) for v in value)
    return False


def cg_native(v):
    if hasattr(v, "item") and getattr(v, "shape", None) == ():
        return v.item()
    if hasattr(v, "tolist"):
        return v.tolist()
    return v


# ---------------------------------------------------------------- 1. readback
def cg_readback(path, selector=None, allow_nan=False):
    """Load value(s) from a saved CSV/TSV/parquet/JSON/npz/npy file.

    selector keys: column (str|list), where ({col: value|list}), row (int, position within
    matches), expect_rows (int), key (dotted JSON path or npz array name), index (npz/npy).
    Returns {value, path, sha256, mtime_utc, ...}. Raises on a missing file, empty selector,
    unknown column/key, zero-row match, wrong match count, or NaN (unless allow_nan).
    """
    import numpy as np
    import pandas as pd

    sel = dict(selector or {})
    allowed = {"column", "where", "row", "key", "index", "expect_rows"}
    if set(sel) - allowed:
        cg_fail("readback", "unknown selector keys %s; allowed %s" % (sorted(set(sel) - allowed), sorted(allowed)))
    if not sel:
        cg_fail("readback", "empty selector: say exactly which value the claim rests on")
    if not os.path.isfile(path):
        cg_fail("readback", "file not found: %s (a claim cannot rest on a file that is not there)" % path)
    if os.path.getsize(path) == 0:
        cg_fail("readback", "file is empty (0 bytes): %s" % path)
    ext = os.path.splitext(path)[1].lower()
    src = cg_source(path)
    n_matched = None
    if ext in (".csv", ".tsv", ".parquet"):
        if ext == ".parquet":
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path, sep="\t" if ext == ".tsv" else ",")
        mask = np.ones(len(df), dtype=bool)
        for col, want in (sel.get("where") or {}).items():
            if col not in df.columns:
                cg_fail("readback", "where-column %r not in %s; columns: %s" % (col, path, list(df.columns)))
            s = df[col]
            if isinstance(want, (list, tuple, set)):
                m = s.isin(list(want)) | s.astype(str).isin([str(w) for w in want])
            elif isinstance(want, float):
                m = np.isclose(pd.to_numeric(s, errors="coerce").to_numpy(dtype=float), want)
            else:
                m = (s == want) | (s.astype(str) == str(want))
            mask &= np.asarray(m, dtype=bool)
        sub = df[mask]
        n_matched = len(sub)
        if n_matched == 0:
            hints = {c: sorted(map(str, df[c].dropna().unique()))[:12] for c in (sel.get("where") or {})}
            cg_fail("readback", "where=%r matched 0 rows in %s; values present: %s" % (sel["where"], path, hints))
        if sel.get("expect_rows") is not None and n_matched != sel["expect_rows"]:
            cg_fail("readback", "expected %s matching rows, found %d in %s" % (sel["expect_rows"], n_matched, path))
        if sel.get("row") is not None:
            if not -n_matched <= sel["row"] < n_matched:
                cg_fail("readback", "row=%s out of range for %d matched rows" % (sel["row"], n_matched))
            sub = sub.iloc[[sel["row"]]]
        col = sel.get("column")
        if col is None:
            value = sub.to_dict(orient="records")
        else:
            cols = [col] if isinstance(col, str) else list(col)
            missing = [c for c in cols if c not in sub.columns]
            if missing:
                cg_fail("readback", "column(s) %s not in %s; columns: %s" % (missing, path, list(df.columns)))
            if isinstance(col, str):
                vals = [cg_native(v) for v in sub[col].tolist()]
                value = vals[0] if len(vals) == 1 else vals
            else:
                value = sub[cols].to_dict(orient="records")
    elif ext == ".json":
        with open(path) as f:
            obj = json.load(f)
        value = obj
        for part in [p for p in str(sel.get("key", "")).split(".") if p != ""]:
            if isinstance(value, list) and part.lstrip("-").isdigit():
                try:
                    value = value[int(part)]
                except IndexError:
                    cg_fail("readback", "list index %s out of range in %s" % (part, path))
            elif isinstance(value, dict) and part in value:
                value = value[part]
            else:
                avail = list(value) if isinstance(value, dict) else type(value).__name__
                cg_fail("readback", "key %r not found at this level of %s; available: %s" % (part, path, avail))
    elif ext in (".npz", ".npy"):
        if ext == ".npz":
            z = np.load(path, allow_pickle=False)
            k = sel.get("key")
            if k not in z.files:
                cg_fail("readback", "array %r not in %s; arrays: %s" % (k, path, z.files))
            arr = z[k]
        else:
            arr = np.load(path, allow_pickle=False)
        idx = sel.get("index")
        if idx is not None:
            arr = arr[tuple(idx) if isinstance(idx, (list, tuple)) else idx]
        value = cg_native(arr)
    else:
        cg_fail("readback", "unsupported file type %r (csv, tsv, parquet, json, npz, npy)" % ext)
    if not allow_nan and cg_has_nan(value):
        cg_fail("readback", "selected value contains NaN in %s: the quantity is undefined/non-computable. "
                "Report it as undefined with the reason, not as a number (allow_nan=True to override)." % path)
    short = json.dumps(value, default=str)
    short = short if len(short) <= 80 else short[:77] + "..."
    return {"check": "readback", "ok": True, "value": value, "n_matched": n_matched, "selector": sel,
            "path": src["path"], "sha256": src["sha256"], "mtime_utc": src["mtime_utc"],
            "summary": "%s %s = %s" % (os.path.basename(path), json.dumps(sel, default=str), short),
            "sources": [src]}


# ---------------------------------------------------------------- 2. changed
def cg_changed(path, before_sha256, strict=True):
    """Fail if `path` is byte-identical to its pre-edit version (hash taken with cg_sha256)."""
    b = str(before_sha256).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", b):
        cg_fail("changed", "before_sha256 must be a 64-hex sha256 taken with cg_sha256() BEFORE the edit; got %r" % before_sha256)
    src = cg_source(path)
    if src["size"] == 0:
        cg_fail("changed", "%s is empty after the edit" % path)
    changed = src["sha256"] != b
    msg = ("%s differs from its pre-edit version" % os.path.basename(path)) if changed else (
        "%s is byte-identical to its pre-edit version: the edit was NOT persisted. Re-run the save/copy, "
        "re-check, and do not say it was fixed." % path)
    if strict and not changed:
        cg_fail("changed", msg)
    return {"check": "changed", "ok": changed, "before_sha256": b, "after_sha256": src["sha256"],
            "summary": msg, "sources": [src]}


# ---------------------------------------------------------------- 3. reconcile
def cg_reconcile(strict=True, **counts):
    """Stated totals must equal their parts.

    Reserved kwargs: parts (list|dict of ints, summed), partitions ({name: collection of ids},
    must be disjoint), items (collection, counted, duplicates flagged). Every other kwarg is a
    stated count (total=125, summary=43, files=len(files)) and all of them must be equal.
    """
    labels, problems = {}, []

    def as_int(name, v):
        if isinstance(v, bool) or not float(v).is_integer():
            cg_fail("reconcile", "count %r=%r is not an integer" % (name, v))
        return int(v)

    for k, v in counts.items():
        if k not in ("parts", "partitions", "items"):
            labels[k] = as_int(k, v)
    if counts.get("parts") is not None:
        p = counts["parts"]
        vals = list(p.values()) if isinstance(p, dict) else list(p)
        labels["sum(parts)"] = sum(as_int("part", x) for x in vals)
    if counts.get("partitions") is not None:
        sets = {n: set(c) for n, c in counts["partitions"].items()}
        names = list(sets)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                both = sets[names[i]] & sets[names[j]]
                if both:
                    problems.append("partitions %r and %r overlap on %d item(s): %s" % (
                        names[i], names[j], len(both), sorted(map(str, both))[:8]))
        labels["sum(partition sizes)"] = sum(len(s) for s in sets.values())
        labels["len(union(partitions))"] = len(set().union(*sets.values())) if sets else 0
    if counts.get("items") is not None:
        it = list(counts["items"])
        labels["len(items)"] = len(it)
        try:
            dups = len(it) - len(set(it))
        except TypeError:
            dups = 0
        if dups:
            problems.append("items contains %d duplicate(s)" % dups)
    if len(labels) < 2:
        cg_fail("reconcile", "need at least two quantities to reconcile; got %s" % labels)
    if len(set(labels.values())) > 1:
        problems.append("counts disagree: %s" % labels)
    ok = not problems
    msg = ("counts agree: %s" % labels) if ok else "; ".join(problems)
    if strict and not ok:
        cg_fail("reconcile", msg)
    return {"check": "reconcile", "ok": ok, "counts": labels, "problems": problems, "summary": msg, "sources": []}


def cg_reconcile_table(df, expected_n=None, by=None, expected_keys=None, value_col=None, balanced=False, strict=True):
    """Rows must equal the expected configurations.

    expected_n: total rows. by: key columns identifying a config. expected_keys: iterable of
    expected key tuples (missing/unexpected/duplicate detected). value_col: no NaN allowed.
    balanced: every `by` group has the same row count.
    """
    import pandas as pd

    if not isinstance(df, pd.DataFrame):
        cg_fail("reconcile", "df must be a DataFrame")
    if expected_n is None and expected_keys is None and value_col is None and not balanced:
        cg_fail("reconcile", "nothing to check: pass expected_n, expected_keys, value_col or balanced")
    if (expected_keys is not None or balanced) and not by:
        cg_fail("reconcile", "expected_keys/balanced need by=[key columns]")
    problems, detail = [], {"n_rows": len(df)}
    by = [by] if isinstance(by, str) else (list(by) if by else None)
    if by:
        miss = [c for c in by if c not in df.columns]
        if miss:
            cg_fail("reconcile", "by-columns %s not in df; columns: %s" % (miss, list(df.columns)))
    if expected_n is not None and len(df) != expected_n:
        problems.append("table has %d rows, expected %d" % (len(df), expected_n))
    if by:
        keys = [k[0] if len(by) == 1 else k for k in df[by].itertuples(index=False, name=None)]
        counts = {}
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
        dups = {k: c for k, c in counts.items() if c > 1}
        if dups and expected_keys is not None:
            problems.append("%d duplicated config(s), e.g. %s" % (len(dups), list(dups.items())[:5]))
        if expected_keys is not None:
            exp = set((k[0] if (len(by) == 1 and isinstance(k, tuple)) else k) for k in expected_keys)
            missing, extra = sorted(exp - set(counts), key=str), sorted(set(counts) - exp, key=str)
            detail.update(n_missing=len(missing), n_unexpected=len(extra))
            if missing:
                problems.append("%d expected config(s) missing, e.g. %s" % (len(missing), missing[:6]))
            if extra:
                problems.append("%d unexpected config(s), e.g. %s" % (len(extra), extra[:6]))
        if balanced:
            sizes = df.groupby(by).size()
            detail["group_sizes"] = {str(k): int(v) for k, v in sizes.items()}
            if sizes.nunique() > 1:
                problems.append("groups are unbalanced: sizes %s" % sorted(set(map(int, sizes))))
    if value_col is not None:
        if value_col not in df.columns:
            cg_fail("reconcile", "value_col %r not in df" % value_col)
        bad = df[df[value_col].isna()]
        if len(bad):
            where = bad[by].head(5).values.tolist() if by else list(bad.index[:5])
            problems.append("%d row(s) have NaN in %r (non-computable; report as undefined), e.g. %s" % (len(bad), value_col, where))
    ok = not problems
    msg = ("table reconciles (%d rows)" % len(df)) if ok else "; ".join(problems)
    if strict and not ok:
        cg_fail("reconcile", msg)
    return {"check": "reconcile_table", "ok": ok, "problems": problems, "detail": detail, "summary": msg, "sources": []}


# ---------------------------------------------------------------- 4. scope
def cg_scope(claimed_items, verified_items, universe=None, noun="items", strict=True):
    """Fail when a claim covers items that were not verified; return the scoped phrasing.

    claimed_items may be "all" (with universe=...) to mean the whole universe.
    """
    def norm(x):
        return [x] if isinstance(x, str) else list(x)

    if isinstance(claimed_items, str) and claimed_items.strip().lower() in ("all", "every", "everything"):
        if universe is None:
            cg_fail("scope", "claim says 'all' but no universe=[...] was given to define what 'all' is")
        claimed = norm(universe)
    elif claimed_items is None:
        if universe is None:
            cg_fail("scope", "claimed_items is None and no universe given")
        claimed = norm(universe)
    else:
        claimed = norm(claimed_items)
    verified = norm(verified_items)
    uni = norm(universe) if universe is not None else None
    if not claimed:
        cg_fail("scope", "empty claim")
    unknown = [c for c in claimed if uni is not None and c not in uni]
    if unknown:
        cg_fail("scope", "claimed %s not in the universe (typo or wrong set): %s" % (noun, unknown))
    vset = set(verified)
    ok_items = [c for c in claimed if c in vset]
    missing = [c for c in claimed if c not in vset]
    denom = len(uni) if uni is not None else len(claimed)
    if not ok_items:
        phrase = "Not verified on any of the %d claimed %s (%s). Make no claim." % (len(claimed), noun, ", ".join(map(str, claimed)))
    elif missing:
        phrase = "Verified on %d of %d %s (%s); not checked: %s. Do not generalize beyond these." % (
            len(ok_items), denom, noun, ", ".join(map(str, ok_items)), ", ".join(map(str, missing)))
    elif uni is not None and len(ok_items) == len(uni):
        phrase = "Verified on all %d %s (%s)." % (len(uni), noun, ", ".join(map(str, uni)))
    else:
        phrase = "Verified on %d of %d %s (%s)." % (len(ok_items), denom, noun, ", ".join(map(str, ok_items)))
    ok = not missing
    if strict and not ok:
        cg_fail("scope", "claim exceeds verified scope. Use this phrasing instead: " + phrase)
    return {"check": "scope", "ok": ok, "verified": ok_items, "unverified": missing, "scoped_phrase": phrase,
            "summary": phrase, "sources": []}


# ---------------------------------------------------------------- 5. render
def cg_render(template_text, values, allow_unused=False, strict_literals=False, allow_literals=None, strict=True):
    """Fill {{key}} / {{key:.3f}} placeholders from a results dict; never hand-type numbers.

    Dotted keys walk nested dicts/lists. A leaf that is a cg_readback result is used by its
    ["value"] and its source path+sha256 lands in the provenance map. Fails on: unresolved
    placeholder, dict leaf never used (unless allow_unused), None/NaN/inf/non-scalar value,
    float without a format spec, and (strict_literals) numeric literals typed in the template.
    """
    leaves = {}

    def flat(prefix, v):
        if isinstance(v, dict) and not ("value" in v and "sha256" in v):
            for k, x in v.items():
                flat(prefix + "." + str(k) if prefix else str(k), x)
        elif isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                flat(prefix + "." + str(i) if prefix else str(i), x)
        else:
            leaves[prefix] = v

    flat("", values)
    used, unresolved, invalid, prov = set(), [], [], {}

    def sub(m):
        key, spec = m.group(1), m.group(2)
        if key not in leaves:
            unresolved.append(key)
            return m.group(0)
        used.add(key)
        leaf = leaves[key]
        source = None
        if isinstance(leaf, dict) and "value" in leaf and "sha256" in leaf:
            source = {"path": leaf.get("path"), "sha256": leaf["sha256"]}
            leaf = leaf["value"]
        v = cg_native(leaf)
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            invalid.append("%s is %r (undefined values must be reported as undefined, not rendered)" % (key, v))
            return m.group(0)
        if isinstance(v, (list, dict)):
            invalid.append("%s is non-scalar (%s); reference a scalar leaf" % (key, type(v).__name__))
            return m.group(0)
        if isinstance(v, float) and not spec:
            invalid.append("%s is a float with no format spec; write {{%s:.3f}} (format only at render time, from the raw value)" % (key, key))
            return m.group(0)
        try:
            out = format(v, spec or "")
        except (ValueError, TypeError) as e:
            invalid.append("%s: bad format spec %r (%s)" % (key, spec, e))
            return m.group(0)
        p = prov.setdefault(key, {"value": v, "rendered": out, "source": source, "count": 0})
        p["count"] += 1
        return out

    text = re.sub(CG_PLACEHOLDER, sub, template_text)
    unused = sorted(set(leaves) - used)
    masked = re.sub(CG_PLACEHOLDER, "\x00", template_text)
    allow = set(str(a) for a in (allow_literals or []))
    literals, fence = [], False
    for ln, line in enumerate(masked.splitlines(), 1):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        s = re.sub(r"`[^`]*`|https?://\S+|" + CG_DATE, "", line)
        s = re.sub(r"^\s*(#+\s*)?\d+[.)]\s", "", s)
        s = re.sub(r"(?i)\b(table|tab\.|fig\.?|figure|section|sec\.|eq\.?|equation|chapter|appendix|panel|supplementary|step|v|nb-)\s?[A-Za-z]?\d+(?:\.\d+)*", "", s)
        for m in re.finditer(CG_NUMBER, s):
            tok = m.group(1)
            if re.fullmatch(CG_YEAR, tok) or tok in allow:
                continue
            literals.append({"line": ln, "token": tok + m.group(2)})
    problems = []
    if unresolved:
        problems.append("unresolved placeholders: %s (not in values dict)" % sorted(set(unresolved)))
    problems += invalid
    if unused and not allow_unused:
        problems.append("values never used in the template (template and results out of sync?): %s" % unused)
    if literals and strict_literals:
        problems.append("numeric literals typed into the template (use placeholders): %s" % literals[:10])
    ok = not problems
    if strict and not ok:
        cg_fail("render", "; ".join(problems))
    srcs = [p["source"] for p in prov.values() if p["source"]]
    return {"check": "render", "ok": ok, "text": text if ok else None, "provenance": prov, "unused": unused,
            "unresolved": sorted(set(unresolved)), "literal_numbers": literals, "problems": problems,
            "summary": "rendered %d placeholder(s) from results dict; %d literal number(s) left in template" % (len(prov), len(literals)),
            "sources": srcs}


# ---------------------------------------------------------------- 6. stale
def cg_stale(outputs, inputs_or_code=None, since_commit=None, repo=".", slack_s=1.0, strict=True):
    """Fail if any output is older than an input/code file, or older than a commit.

    Directories are expanded: an output dir is as old as its OLDEST file, an input dir as new
    as its NEWEST file. since_commit is any git rev in `repo` (e.g. the remediation commit).
    """
    outs = [outputs] if isinstance(outputs, str) else list(outputs)
    ins = [inputs_or_code] if isinstance(inputs_or_code, str) else list(inputs_or_code or [])
    if not outs:
        cg_fail("stale", "no outputs given")
    if not ins and since_commit is None:
        cg_fail("stale", "give inputs_or_code and/or since_commit to compare against")

    def mtimes(p):
        if not os.path.exists(p):
            cg_fail("stale", "path does not exist: %s" % p)
        if os.path.isfile(p):
            return [os.path.getmtime(p)]
        ts = []
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d != ".git"]
            ts += [os.path.getmtime(os.path.join(root, f)) for f in files]
        if not ts:
            cg_fail("stale", "directory has no files: %s" % p)
        return ts

    def iso(t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    dep_t, dep_name = None, None
    for p in ins:
        t = max(mtimes(p))
        if dep_t is None or t > dep_t:
            dep_t, dep_name = t, p
    commit_t = None
    if since_commit is not None:
        try:
            r = subprocess.run(["git", "-C", repo, "show", "-s", "--format=%ct", str(since_commit)],
                               capture_output=True, text=True, check=True)
            commit_t = float(r.stdout.strip().splitlines()[0])
        except (subprocess.CalledProcessError, FileNotFoundError, IndexError, ValueError) as e:
            cg_fail("stale", "cannot resolve commit %r in repo %r: %s" % (since_commit, repo, getattr(e, "stderr", e)))
    rows, problems = [], []
    for o in outs:
        ts = mtimes(o)
        t = min(ts)
        if os.path.isfile(o) and os.path.getsize(o) == 0:
            problems.append("%s is empty" % o)
        row = {"output": o, "mtime_utc": iso(t)}
        if dep_t is not None:
            row["margin_vs_inputs_s"] = round(t - dep_t, 2)
            if t + slack_s < dep_t:
                problems.append("%s (%s) predates %s (%s): stale, regenerate before reporting" % (o, iso(t), dep_name, iso(dep_t)))
        if commit_t is not None:
            row["margin_vs_commit_s"] = round(t - commit_t, 2)
            if t + slack_s < commit_t:
                problems.append("%s (%s) predates commit %s (%s): stale, regenerate before reporting" % (o, iso(t), since_commit, iso(commit_t)))
        rows.append(row)
    ok = not problems
    msg = ("%d output(s) newer than their inputs/commit" % len(outs)) if ok else "; ".join(problems)
    if strict and not ok:
        cg_fail("stale", msg)
    srcs = [cg_source(o) for o in outs if os.path.isfile(o)]
    return {"check": "stale", "ok": ok, "rows": rows, "problems": problems, "summary": msg, "sources": srcs}


# ---------------------------------------------------------------- 7. contradictions
def cg_contradictions(summary_text, table_df, tol=0.0, ignore_numbers=None, derived=True, ignore_years=True,
                      abs_ok=False, scope_by_labels=True, fail_on_quantifiers=False, strict=True):
    """Check numbers in prose against the table the prose describes.

    Each number must match a table cell (to the precision it is written at) or, if derived, a
    column mean/median/min/max/sum or the row count. When a sentence names row labels / column
    names, the number must match inside that scope, else it is `inconsistent`. Ranges
    ("0.44-0.57") are checked for containment. Quantifier sentences (all/every/only/top-3 ...)
    and "k of n" counts are returned for explicit recomputation (fail_on_quantifiers makes the
    former fatal). Returns matched/unmatched/inconsistent/sign_mismatch/range_violations.
    """
    import numpy as np
    import pandas as pd

    if not isinstance(table_df, pd.DataFrame) or len(table_df) == 0:
        cg_fail("contradictions", "table_df must be a non-empty DataFrame")
    if not str(summary_text).strip():
        cg_fail("contradictions", "empty summary_text")
    num_df = table_df.select_dtypes(include="number")
    if num_df.shape[1] == 0:
        cg_fail("contradictions", "table has no numeric columns to check against")
    arr = num_df.to_numpy(dtype=float)
    ncols = list(num_df.columns)
    scols = [c for c in table_df.columns if c not in num_df.columns]
    ign = [float(x) for x in (ignore_numbers or [])]
    text = str(summary_text).replace("\u2212", "-")
    text = re.sub(CG_DATE, " ", text)

    def cands(mask, cidx):
        out = []
        sub = arr[mask][:, cidx]
        for j, ci in enumerate(cidx):
            s = sub[:, j]
            s = s[~np.isnan(s)]
            out += [(float(v), "cell:%s" % ncols[ci]) for v in s]
            if derived and len(s):
                for nm, v in (("mean", s.mean()), ("median", np.median(s)),
                              ("min", s.min()), ("max", s.max()), ("sum", s.sum())):
                    out.append((float(v), "%s(%s)" % (nm, ncols[ci])))
        if derived:
            out.append((float(mask.sum()), "n_rows"))
        return out

    def find(x, dec, exp, pct, is_int, cs, sign=True):
        half = 0.5 * 10 ** (-dec) * 10 ** exp
        for v, src in cs:
            for cv in ((v * 100, v) if pct else (v,)):
                if not sign:
                    cv, xx = abs(cv), abs(x)
                else:
                    xx = x
                h = half if not is_int else (0.5 if src.startswith(("mean", "median")) else 1e-9)
                if abs(cv - xx) <= h * (1 + 1e-9) + tol * abs(xx) + 1e-12:
                    return src
        return None

    def mention(name, sent):
        return re.search(r"(?<!\w)" + re.escape(str(name)) + r"(?!\w)", sent, re.I) is not None

    def scope_of(sent):
        mask = np.ones(len(table_df), dtype=bool)
        cidx = list(range(len(ncols)))
        info, labs = [], []
        if scope_by_labels and scols:
            ms = []
            for c in scols:
                vals = []
                for v in table_df[c].dropna().astype(str).unique():
                    if len(v) >= 2 and mention(v, sent):
                        vals.append(v)
                if vals:
                    ms.append(table_df[c].astype(str).isin(vals).to_numpy())
                    info += vals
                    labs += vals
            if ms:
                am, om = np.logical_and.reduce(ms), np.logical_or.reduce(ms)
                mask = am if am.any() else om
            cm = [i for i, c in enumerate(ncols) if len(str(c)) >= 3 and mention(c, sent)]
            if cm:
                cidx = cm
                info += [str(ncols[i]) for i in cm]
        return mask, cidx, info, labs

    sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(\"'])|\n+", text) if s.strip()]
    matched, unmatched, inconsistent, sign_mm, range_viol, quant, count_claims = [], [], [], [], [], [], []
    allm = np.ones(len(table_df), dtype=bool)
    allc = list(range(len(ncols)))
    gcands = cands(allm, allc)
    n_numbers = 0
    for sent in sentences:
        sent = re.sub(r"^\s*(?:[-*]\s+)?\d+[.)]\s", "", sent)
        if re.search(CG_QUANT, sent, re.I):
            quant.append(sent.strip()[:160])
        for m in re.finditer(r"(\d+)\s+of\s+(\d+)", sent):
            count_claims.append(m.group(0))
        mask, cidx, info, labs = scope_of(sent)
        for lab in sorted(labs, key=len, reverse=True):
            sent = re.sub(r"(?<!\w)" + re.escape(lab) + r"(?!\w)", " ", sent, flags=re.I)
        scoped = cands(mask, cidx)
        restricted = bool(info)
        rng_spans = []
        for rm in re.finditer(CG_RANGE, sent):
            rng_spans.append((rm.start(), rm.end(), bool(rm.group(3))))
        for m in re.finditer(CG_NUMBER, sent):
            tok = m.group(1).replace(",", "")
            if re.fullmatch(CG_YEAR, tok) and ignore_years:
                continue
            x = float(tok)
            if x in ign:
                continue
            mant = re.split(r"[eE]", tok)[0]
            dec = len(mant.split(".")[1]) if "." in mant else 0
            exp = int(re.split(r"[eE]", tok)[1]) if re.search(r"[eE]", tok) else 0
            is_int = ("." not in tok) and (not m.group(2)) and exp == 0
            pct = bool(m.group(2)) or any(a <= m.start() < b and p for a, b, p in rng_spans)
            n_numbers += 1
            rec = {"number": m.group(0), "sentence": sent.strip()[:140]}
            hit = find(x, dec, exp, pct, is_int, scoped)
            if hit:
                matched.append(dict(rec, source=hit))
                continue
            g = find(x, dec, exp, pct, is_int, gcands) if restricted else None
            if g:
                inconsistent.append(dict(rec, scope=info, found_elsewhere=g,
                                         why="number exists in the table but not within the rows/columns this sentence names"))
                continue
            if not abs_ok and find(x, dec, exp, pct, is_int, gcands, sign=False):
                sign_mm.append(dict(rec, why="matches a table value only in absolute value: check the sign/direction"))
                continue
            unmatched.append(dict(rec, scope=info, why="no table value, aggregate or row count matches at this precision"))
        for rm in re.finditer(CG_RANGE, sent):
            a, b = float(rm.group(1)), float(rm.group(2))
            lo, hi = min(a, b), max(a, b)
            pct = bool(rm.group(3))
            da = len(rm.group(1).split(".")[1]) if "." in rm.group(1) else 0
            db = len(rm.group(2).split(".")[1]) if "." in rm.group(2) else 0
            ha, hb = 0.5 * 10 ** (-da), 0.5 * 10 ** (-db)
            for ci in cidx:
                col = arr[:, ci] * (100 if pct else 1)
                if not (np.any(np.abs(col - a) <= ha) or np.any(np.abs(col - b) <= hb)):
                    continue
                sel = col[mask]
                bad = [(i, float(v)) for i, v in zip(np.where(mask)[0], sel)
                       if not np.isnan(v) and (v < lo - max(ha, hb) or v > hi + max(ha, hb))]
                if bad:
                    lab = [(str(table_df.iloc[i][scols[0]]) if scols else int(i), round(v, 6)) for i, v in bad[:6]]
                    range_viol.append({"range": rm.group(0), "column": str(ncols[ci]), "outside": lab,
                                       "sentence": sent.strip()[:140]})
    problems = []
    if unmatched:
        problems.append("%d number(s) in the prose match nothing in the table: %s" % (len(unmatched), [u["number"] for u in unmatched][:8]))
    if inconsistent:
        problems.append("%d number(s) exist in the table but not where the sentence points: %s" % (len(inconsistent), [u["number"] for u in inconsistent][:8]))
    if sign_mm:
        problems.append("%d number(s) match only in absolute value: %s" % (len(sign_mm), [u["number"] for u in sign_mm][:8]))
    if range_viol:
        problems.append("%d range claim(s) contradicted by table values: %s" % (len(range_viol), [(r["range"], r["column"], r["outside"][:3]) for r in range_viol][:4]))
    if fail_on_quantifiers and quant:
        problems.append("%d sentence(s) make universal/superlative claims that need explicit recomputation: %s" % (len(quant), [q[:60] for q in quant][:4]))
    ok = not problems
    msg = ("%d number(s) in prose all consistent with the table" % n_numbers) if ok else "; ".join(problems)
    if strict and not ok:
        cg_fail("contradictions", msg)
    return {"check": "contradictions", "ok": ok, "n_numbers": n_numbers, "matched": matched, "unmatched": unmatched,
            "inconsistent": inconsistent, "sign_mismatch": sign_mm, "range_violations": range_viol,
            "quantifier_claims": quant, "count_claims": count_claims, "problems": problems, "summary": msg, "sources": []}


# ---------------------------------------------------------------- receipt
def cg_receipt(claim, evidence, not_checked=None):
    """Assemble the claim receipt from helper results. Refuses a failed or empty evidence list."""
    ev = [evidence] if isinstance(evidence, dict) else list(evidence or [])
    if not str(claim).strip():
        cg_fail("receipt", "empty claim")
    if not ev:
        cg_fail("receipt", "no evidence: a receipt without a check is just an assertion. Run a cg_* helper first.")
    for e in ev:
        if not isinstance(e, dict) or "check" not in e or "ok" not in e:
            cg_fail("receipt", "evidence entries must be results returned by cg_* helpers; got %r" % (e,))
        if not e["ok"]:
            cg_fail("receipt", "evidence %r FAILED: %s. Fix or narrow the claim; do not attach a failing check." % (e["check"], e.get("summary")))
    lines = ["CLAIM RECEIPT", "claim: %s" % str(claim).strip()]
    for e in ev:
        srcs = e.get("sources") or []
        where = "; ".join("%s sha256:%s @%s" % (s["path"], s["sha256"][:12], s["mtime_utc"]) for s in srcs) or "in-memory"
        lines.append("- %s: %s | %s" % (e["check"], e.get("summary"), where))
    nc = [nc for nc in (not_checked or [])]
    lines.append("not checked: %s" % ("; ".join(map(str, nc)) if nc else "nothing outside the claim"))
    return {"check": "receipt", "ok": True, "text": "\n".join(lines), "summary": "receipt with %d check(s)" % len(ev), "sources": []}
