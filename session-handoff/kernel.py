"""Helpers for the session-handoff skill (python kernel).

The size self-check lives in the repl tool (host.query is only there); these
helpers run in the python/R analysis kernel, where host.artifacts() is
available, and do the mechanical part of drafting the handoff: listing this
session's artifacts with resolvable version ids, and saving the document.
"""

HANDOFF_SECTIONS = ("Objective", "State", "Artifacts", "Kernel state (will be lost)",
                    "Decisions", "Dead ends", "Next steps",
                    "Open questions for the user")


def handoff_artifact_lines(frame_id=None, limit=200):
    """Markdown bullets for this session's artifacts, with resolvable ids.

    Pass the session's frame id to scope to work done here; omit it for the
    whole project. Each line is ready to paste under '## Artifacts' — fill in
    the trailing description, which is the part only you can write.
    """
    kwargs = {"limit": limit}
    if frame_id:
        kwargs["frame_id"] = frame_id
    res = host.artifacts(**kwargs)
    lines = []
    for a in res.get("artifacts", []):
        if a.get("is_ephemeral"):
            continue
        size = a.get("size_bytes") or 0
        vid = a["latest_version_id"]
        try:
            marker = host.artifact_marker(vid)
        except Exception:
            marker = "{{" + "artifact:" + vid + "}}"
        lines.append(
            "- [{fn}]({mk}) — <what it is, why it matters>"
            "  <!-- {ct}, {kb} KB -->".format(
                fn=a["filename"], mk=marker,
                ct=a.get("content_type", "?"), kb=max(1, size // 1024)))
    return "\n".join(lines) if lines else "- <no artifacts saved yet>"


def handoff_kernel_inventory(names=None, namespace=None):
    """Table rows for the 'Kernel state' section, from live variables.

    Call with globals(): handoff_kernel_inventory(namespace=globals()). Sizes
    come from the objects themselves so 'expensive to rebuild' is a judgement
    you make against real numbers rather than a guess. Rebuild cost and reload
    path are yours to fill — nothing can infer them.
    """
    import sys
    if namespace is None:
        raise ValueError("pass namespace=globals() from the kernel you are inventorying")
    if names is None:
        skip = ("host", "operon", "In", "Out", "exit", "quit", "get_ipython")
        names = [k for k in namespace if not k.startswith("_") and k not in skip]
    rows = ["| in memory | size | rebuild cost | how to restore |",
            "|---|---|---|---|"]
    for name in sorted(names):
        obj = namespace.get(name)
        if obj is None or callable(obj) or isinstance(obj, type):
            continue
        if type(obj).__module__ == "builtins" and not isinstance(obj, (list, dict, set)):
            continue
        tmod = (type(obj).__module__ or "").split(".")[0]
        if tmod.startswith("_") or tmod in ("importlib", "types", "typing", "operon"):
            continue
        if type(obj).__name__ in ("module", "ModuleSpec", "SourceFileLoader"):
            continue
        shape = getattr(obj, "shape", None)
        if shape is not None:
            desc = "shape {}".format(shape)
        elif isinstance(obj, (list, dict, set, tuple)):
            desc = "{} items".format(len(obj))
        else:
            desc = "{} MB".format(round(sys.getsizeof(obj) / 1e6, 1))
        rows.append("| `{}` ({}) | {} | <cheap/expensive> | <reload path> |".format(
            name, type(obj).__name__, desc))
    if len(rows) == 2:
        rows.append("| <nothing expensive in memory> | | | |")
    return "\n".join(rows)


def handoff_write(topic, body, outdir=None):
    """Write HANDOFF-<topic>.md to the workspace; returns the path.

    Saving it as an artifact is a separate deliberate step — call
    save_artifacts() on the returned path so the next session can find it.
    """
    import os
    import re
    if outdir is None:
        outdir = "."
    slug = re.sub(r"[^a-z0-9]+", "-", str(topic).lower()).strip("-") or "session"
    path = os.path.join(outdir, "HANDOFF-{}.md".format(slug))
    with open(path, "w") as fh:
        fh.write(body if body.endswith("\n") else body + "\n")
    return path


def handoff_check(text):
    """Flag the failure modes that strand a receiving session.

    Returns a list of problems: missing sections, bare filenames that will not
    resolve from a new session, and placeholders left unfilled.
    """
    import re
    problems = []
    for sec in HANDOFF_SECTIONS:
        if "## " + sec not in text:
            problems.append("missing section: {}".format(sec))
    for m in re.finditer(r"\[([^\]]+\.\w{1,6})\]\(([^)]+)\)", text):
        target = m.group(2)
        if "artifact:" in target:
            continue
        if "/artifacts/" in target or target.startswith("/"):
            # A literal {{artifact:...}} marker written inside a submitted code
            # cell is rewritten to a local path BEFORE the cell runs, so it
            # reaches the file already resolved and will not resolve for anyone
            # else. Build markers with host.artifact_marker(vid) instead.
            problems.append(
                "pre-resolved marker: [{}] points at a local path — rebuild it "
                "with host.artifact_marker(version_id)".format(m.group(1)))
        else:
            problems.append("unresolvable link: [{}]({}) — use an artifact id"
                            .format(m.group(1), target))
    left = re.findall(r"<[a-z][^>]{2,60}>", text)
    if left:
        problems.append("{} unfilled placeholder(s), first: {}".format(len(left), left[0]))
    return problems
