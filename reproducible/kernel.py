"""Helpers for the `reproducible` skill: turn Claude Science lineage into
human-auditable provenance records, seed scans, and a traceability matrix.

All functions read from `host.lineage` / the artifact store — they surface
recorded provenance, they do not reconstruct it. See SKILL.md ## Audit workflow.
"""
import os
import re
import csv
import json

SEED_PATTERNS = (
    r"random_state\s*=\s*\d+",
    r"np\.random\.seed\s*\(\s*\d+\s*\)",
    r"random\.seed\s*\(\s*\d+\s*\)",
    r"torch\.manual_seed\s*\(\s*\d+\s*\)",
    r"tf\.random\.set_seed\s*\(\s*\d+\s*\)",
    r"set_seed\s*\(\s*\d+\s*\)",
    r"\bseed\s*=\s*\d+",
    r"default_rng\s*\(\s*\d+\s*\)",
)

TRACE_COLUMNS = ("claim", "value", "artifact_version_id", "source", "verify")


def scan_seeds(code):
    """Return RNG-seed / random_state lines found in `code`.

    A stochastic result that pins no seed is an audit red flag: it cannot be
    re-derived exactly. Use this to decide whether a provenance record should
    warn the reviewer.

    Parameters
    ----------
    code : str
        Source code (e.g. an artifact's reproduction code from lineage).

    Returns
    -------
    list of str
        Distinct stripped source lines that set an RNG seed. Empty if none.

    Example
    -------
    >>> scan_seeds("model = RF(random_state=0)\\nx = 1")
    ['model = RF(random_state=0)']
    >>> scan_seeds("x = compute(data)")
    []
    """
    if not code:
        return []
    combined = re.compile("|".join(SEED_PATTERNS))
    hits = []
    for line in code.splitlines():
        if combined.search(line):
            s = line.strip()
            if s not in hits:
                hits.append(s)
    return hits


def provenance_record(version_id, host_obj=None):
    """Build a human-readable provenance dict for one artifact version.

    Pulls the recorded lineage (reproduction code, environment, resolved
    inputs, producing frame/cell) and derives a seed scan, so a reviewer can
    see exactly what went into a result and rebuild it.

    Parameters
    ----------
    version_id : str
        Full artifact version UUID (from ``host.artifacts()``).
    host_obj : object, optional
        The ``host`` singleton. Defaults to the ``host`` already present in the
        kernel namespace; pass explicitly only in unusual embeddings.

    Returns
    -------
    dict
        Keys: ``version_id``, ``artifact_id``, ``filename``, ``frame_id``,
        ``producing_cell_id``, ``environment``, ``inputs`` (list of
        ``{version_id, filename}``), ``seeds`` (list of str), ``code`` (str),
        and ``seed_warning`` (bool — True when code looks stochastic but pins
        no seed).

    Example
    -------
    >>> rec = provenance_record(vid)                      # doctest: +SKIP
    >>> rec["filename"], bool(rec["seeds"])               # doctest: +SKIP
    ('corr.csv', True)
    """
    if host_obj is None:
        host_obj = globals().get("host")
    if host_obj is None:
        raise RuntimeError("host singleton not found in kernel; pass host_obj=")
    lin = host_obj.lineage[version_id]
    code = lin.get("code") or ""
    env = lin.get("env") or {}
    env_name = env.get("environment_name") if isinstance(env, dict) else None
    inputs = []
    for i in (lin.get("inputs") or []):
        inputs.append({"version_id": i.get("version_id"),
                       "filename": i.get("filename")})
    seeds = scan_seeds(code)
    stochastic = bool(re.search(r"random|sample|shuffle|rng|stochastic|bootstrap|permut",
                                code, re.I))
    return {
        "version_id": version_id,
        "artifact_id": lin.get("artifact_id"),
        "filename": lin.get("filename"),
        "frame_id": lin.get("frame_id"),
        "producing_cell_id": lin.get("producing_cell_id"),
        "environment": env_name,
        "inputs": inputs,
        "seeds": seeds,
        "code": code,
        "seed_warning": stochastic and not seeds,
    }


def render_provenance_md(records):
    """Render one provenance record (or a list) as reviewer-facing markdown.

    Each result becomes a section: filename + version_id, producing
    frame/cell, environment, a table of inputs, the seeds (or a warning when a
    stochastic result pins none), and the reproduction code in a collapsible
    block.

    Parameters
    ----------
    records : dict or list of dict
        Output(s) of ``provenance_record``.

    Returns
    -------
    str
        Markdown text ready to write to ``audit/provenance/records.md``.

    Example
    -------
    >>> md = render_provenance_md({"filename": "corr.csv", "version_id": "v1",
    ...     "frame_id": "f", "producing_cell_id": "c", "environment": "python",
    ...     "inputs": [], "seeds": [], "code": "x=1", "seed_warning": False})
    >>> "## corr.csv" in md
    True
    """
    if isinstance(records, dict):
        records = [records]
    out = ["# Provenance records\n",
           "_Each result below links to the code, environment, inputs, and "
           "seeds that produced it. Generated from recorded lineage._\n"]
    for r in records:
        out.append(f"## {r.get('filename') or r.get('version_id')}\n")
        out.append(f"- **version_id:** `{r.get('version_id')}`")
        if r.get("artifact_id"):
            out.append(f"- **artifact_id:** `{r.get('artifact_id')}`")
        out.append(f"- **produced by:** frame `{r.get('frame_id')}` "
                   f"cell `{r.get('producing_cell_id')}`")
        out.append(f"- **environment:** `{r.get('environment') or 'unknown'}`")
        inputs = r.get("inputs") or []
        if inputs:
            out.append("- **inputs:**")
            for i in inputs:
                out.append(f"    - `{i.get('filename')}` "
                           f"(`{i.get('version_id')}`)")
        else:
            out.append("- **inputs:** none recorded")
        if r.get("seeds"):
            out.append("- **seeds:** " + ", ".join(f"`{s}`" for s in r["seeds"]))
        elif r.get("seed_warning"):
            out.append("- **seeds:** ⚠️ none pinned, but the code looks "
                       "stochastic — this result may not re-derive exactly.")
        else:
            out.append("- **seeds:** none (result appears deterministic)")
        code = r.get("code") or ""
        out.append("\n<details><summary>reproduction code</summary>\n")
        out.append("```python\n" + code.rstrip() + "\n```\n</details>\n")
    return "\n".join(out) + "\n"


def write_traceability(rows, path="audit/traceability.csv"):
    """Write a claim->artifact traceability matrix to CSV.

    Canonical columns: claim, value, artifact_version_id, source, verify.
    Extra keys in a row are appended as additional columns. Creates parent
    directories as needed.

    Parameters
    ----------
    rows : list of dict
        One dict per load-bearing claim. ``value`` should be read FROM the
        artifact, never retyped from prose.
    path : str
        Output CSV path. Defaults to ``audit/traceability.csv``.

    Returns
    -------
    str
        The path written.

    Example
    -------
    >>> import tempfile, os, csv
    >>> p = os.path.join(tempfile.mkdtemp(), "t.csv")
    >>> write_traceability([{"claim": "r", "value": "0.83",
    ...     "artifact_version_id": "v1", "source": "frame f cell c",
    ...     "verify": "python audit/verify.py --check corr"}], p)  # doctest: +ELLIPSIS
    '...t.csv'
    >>> next(csv.reader(open(p)))
    ['claim', 'value', 'artifact_version_id', 'source', 'verify']
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    extra = []
    for r in rows:
        for k in r:
            if k not in TRACE_COLUMNS and k not in extra:
                extra.append(k)
    header = list(TRACE_COLUMNS) + extra
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})
    return path

def write_manifest(root=".", path=None):
    """Write a ``MANIFEST.sha256`` of every file under ``root``.

    Lets a reviewer confirm nothing drifted since the audit: each line is
    ``<sha256>  <relpath>``, sorted by path, standard ``sha256sum -c`` format.
    Skips the manifest itself, VCS internals, and Python/build junk.

    Parameters
    ----------
    root : str
        Directory to walk. Defaults to the current directory.
    path : str, optional
        Output path. Defaults to ``<root>/MANIFEST.sha256``.

    Returns
    -------
    str
        The path written.

    Example
    -------
    >>> import tempfile, os
    >>> d = tempfile.mkdtemp()
    >>> _ = open(os.path.join(d, "a.txt"), "w").write("hello")
    >>> p = write_manifest(d)
    >>> line = open(p).read().strip()
    >>> line.split("  ")[1]
    'a.txt'
    >>> len(line.split("  ")[0])
    64
    """
    import hashlib
    if path is None:
        path = os.path.join(root, "MANIFEST.sha256")
    skip_dirs = {".git", "__pycache__", ".pytest_cache", ".ipynb_checkpoints",
                 ".mypy_cache", ".ruff_cache"}
    manifest_abs = os.path.abspath(path)
    lines = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs
                       and not d.endswith(".egg-info")]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            if os.path.abspath(fp) == manifest_abs:
                continue
            if fn.endswith((".pyc", ".pyo")):
                continue
            h = hashlib.sha256()
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            rel = os.path.relpath(fp, root)
            lines.append(f"{h.hexdigest()}  {rel}")
    lines.sort(key=lambda s: s.split("  ", 1)[1])
    with open(path, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return path

