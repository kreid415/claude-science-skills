"""Helpers for the session-handoff skill (python kernel).

The size check itself runs in the repl tool (host.query is only there). It inlines
handoff_trigger below, and a test keeps the two copies identical. These helpers run
in the python/R analysis kernel, where host.artifacts() is available, and do the
mechanical parts: the automatic size trigger, listing this session's artifacts with
resolvable version ids, building and checking the start message for the next chat,
and writing the handoff document.
"""

HANDOFF_SECTIONS = ("Start message for the next chat", "Objective", "State", "Artifacts",
                    "Kernel state (will be lost)", "Decisions", "Dead ends", "Next steps",
                    "Open questions for the user")


def handoff_trigger(msgs, ctx_used, folds, last_msgs=0, last_folds=0):
    """Automatic size trigger: any of 300 messages, 280k tokens of latest context, 1 fold.

    Returns {'fire': bool, 'reasons': [...], 'urgent': bool}. After a handoff, pass the
    (messages, folds) recorded then; the trigger re-arms only after 150 more messages
    or a new fold. 'urgent' means 2000+ messages: hand off now, even with jobs in flight.
    """
    msgs = msgs or 0
    ctx_used = ctx_used or 0
    folds = folds or 0
    reasons = []
    if msgs >= 300:
        reasons.append("{} messages (trigger 300)".format(msgs))
    if ctx_used >= 280000:
        reasons.append("{}k tokens of latest context (trigger 280k)".format(ctx_used // 1000))
    if folds >= 1:
        reasons.append("{} fold(s) so far".format(folds))
    armed = (not last_msgs) or (msgs - last_msgs >= 150) or (folds > last_folds)
    return {"fire": bool(reasons) and armed, "reasons": reasons, "urgent": msgs >= 2000}


def handoff_start_message(topic, objective, next_action, handoff_artifact_id, prev_frame_id,
                          open_first=None, lost_state=None, pending=None, do_not_redo=None,
                          max_words=150):
    """Ready-to-paste first message for the next chat (plain text, no artifact markers).

    Names the handoff file and its full artifact id so the new chat can find it by id even
    without an @-mention, and the previous chat's frame id so its archive can be searched.
    Raises ValueError when over max_words: shorten the fields, the detail belongs in the
    handoff file.
    """
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", str(topic).lower()).strip("-") or "session"
    lines = [
        "Continue from HANDOFF-{}.md (artifact id {}) in this project; read it before doing "
        "anything else.".format(slug, handoff_artifact_id),
        "Objective: {}".format(objective),
        "Next action: {}".format(next_action),
    ]
    if open_first:
        lines.append("Open first: " + "; ".join(open_first[:5]))
    if lost_state:
        lines.append("Lost with the old kernel: " + lost_state)
    if pending:
        lines.append("Pending: " + "; ".join(pending))
    if do_not_redo:
        lines.append("Do not redo: " + "; ".join(do_not_redo[:3]))
    lines.append("Before reusing any identifier, number or quote from the previous chat "
                 "(frame {}), search its archive.".format(prev_frame_id))
    text = "\n".join(lines)
    n = len(text.split())
    if n > max_words:
        raise ValueError("start message is {} words (cap {}); shorten the fields".format(n, max_words))
    return text


def handoff_check_start(text, max_words=170):
    """Problems that would strand the next chat: missing file name or ids, markers, length."""
    import re
    problems = []
    if not re.search(r"HANDOFF-[a-z0-9-]+\.md", text):
        problems.append("does not name the HANDOFF-<topic>.md file")
    ids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text))
    if len(ids) < 2:
        problems.append("needs the handoff artifact id and the previous chat's frame id in full "
                        "(found {} distinct id(s))".format(len(ids)))
    if "{{" in text or "/home/" in text or "/tmp/" in text:
        problems.append("contains an artifact marker or local path; use plain ids")
    n = len(text.split())
    if n > max_words:
        problems.append("{} words (cap {})".format(n, max_words))
    return problems


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

    Returns a list of problems: missing sections, a start message that would not work
    from a fresh chat, bare filenames that will not resolve from a new session, and
    placeholders left unfilled.
    """
    import re
    problems = []
    for sec in HANDOFF_SECTIONS:
        if "## " + sec not in text:
            problems.append("missing section: {}".format(sec))
    start = re.search(r"## Start message for the next chat\s*```[a-z]*\n(.*?)```", text, re.S)
    if start:
        problems += ["start message: " + p for p in handoff_check_start(start.group(1))]
    elif "## Start message for the next chat" in text:
        problems.append("start message section has no fenced block")
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


# ---- run state and the wave boundary (added 2026-10-08) -------------------------------
# The per-turn repl check inlines handoff_quiet and handoff_wave_offer; a test keeps the copies
# identical. handoff_run_state_rows runs in the python kernel when a handoff is written.

HANDOFF_JOB_TERMINAL = ("done", "succeeded", "failed", "cancelled", "timed_out")


def handoff_quiet(jobs, children=0, boards=(), watchers=()):
    """Wave boundary: True when nothing is live that a new chat would lose.

    jobs: compute-ledger rows of this chat, dicts with 'job_id' and 'state' (any state not in
    HANDOFF_JOB_TERMINAL counts as live). children: number of running sub-agents. boards:
    status_board.json contents ({'board': {...}} or the board itself); units 'running' or
    'pending' are live. watchers: job-watch watchers outside any ledger job, dicts with 'id' and
    optional 'status' (state.json 'status'); live unless status is known and not 'running'.
    Returns {'quiet', 'live_jobs', 'live_units', 'live_watchers', 'children', 'reasons'}.
    """
    live_jobs = [j["job_id"] for j in jobs if str(j.get("state", "")).lower() not in HANDOFF_JOB_TERMINAL]
    live_units = 0
    for b in boards:
        t = (b.get("board", b) or {}).get("totals", {})
        live_units += int(t.get("running", 0)) + int(t.get("pending", 0))
    live_w = [w["id"] for w in watchers if w.get("status") in (None, "running")]
    children = int(children or 0)
    reasons = []
    if live_jobs:
        reasons.append("{} live ledger job(s)".format(len(live_jobs)))
    if children:
        reasons.append("{} running sub-agent(s)".format(children))
    if live_units:
        reasons.append("{} board unit(s) running or pending".format(live_units))
    if live_w:
        reasons.append("{} job-watch watcher(s) outside the ledger".format(len(live_w)))
    return {"quiet": not reasons, "live_jobs": live_jobs, "live_units": live_units,
            "live_watchers": live_w, "children": children, "reasons": reasons}


def handoff_wave_offer(quiet, msgs, last_end_ms, offered_ms=0, min_msgs=150):
    """Offer a handoff once per finished wave.

    True when nothing is live (quiet), a job of this chat ended after the last offer
    (last_end_ms > offered_ms, epoch ms, from compute_usage.ended_at) and the chat has at least
    min_msgs messages. This only offers; the size trigger is what fires automatically.
    """
    return bool(quiet and (msgs or 0) >= min_msgs and last_end_ms and last_end_ms > (offered_ms or 0))


def handoff_check_cmd(job):
    import json as _json
    h = job.get("remote_handle")
    try:
        h = _json.loads(h) if isinstance(h, str) else (h or {})
    except ValueError:
        h = {}
    wd = job.get("remote_workdir") or h.get("workdir") or "?"
    if h.get("kind") == "slurm" and h.get("jobId"):
        return "sacct -X -j {} -o JobID,State,Elapsed,Timelimit; ls {}".format(h["jobId"], wd)
    if h.get("pgid"):
        return "ps -o pid,stat,etime,cmd -g {}; ls {}".format(h["pgid"], wd)
    return "ls -lt {} | head".format(wd)


def handoff_run_state_rows(jobs, watchers=(), boards=(), children=()):
    """Markdown table of everything live, for the handoff's State section.

    A new chat cannot attach_job a job owned by this chat, and completion notices land here, so
    each row names the host and a read-only command any chat can run with call_command on it.
    jobs: ledger rows (job_id, provider, state, remote_workdir, remote_handle). watchers: dicts
    with 'id', 'host', 'state_dir', optional 'status'. boards: (path, status_board.json dict)
    pairs. children: dicts with 'frame_id' and 'name'. Returns '' when nothing is live.
    """
    rows = []
    for j in jobs:
        if str(j.get("state", "")).lower() in HANDOFF_JOB_TERMINAL:
            continue
        rows.append("| job | `{}` | {} | {} | `{}` |".format(j["job_id"], j.get("provider", "?"), j.get("state", "?"),
                                                           handoff_check_cmd(j)))
    for w in watchers:
        if w.get("status") not in (None, "running"):
            continue
        rows.append("| job-watch | `{}` | {} | {} | `python3 ~/job-watch/jw.py status --state {}` (resume: `watch --state {}`) |".format(
            w["id"], w.get("host", "?"), w.get("status") or "unknown", w["state_dir"], w["state_dir"]))
    for path, b in boards:
        bd = b.get("board", b)
        t = bd.get("totals", {})
        live = int(t.get("running", 0)) + int(t.get("pending", 0))
        if live:
            rows.append("| status board | `{}` | - | {} of {} running/pending (generated {}) | rebuild the board from its manifest |".format(
                path, live, bd.get("n_units", "?"), bd.get("generated_at", "?")))
    for c in children:
        rows.append("| sub-agent | `{}` | - | running | its result returns to the old chat only; collect it there or re-run |".format(
            c.get("frame_id") or c.get("name")))
    if not rows:
        return ""
    return "\n".join(["| kind | id | host | state | check from any chat |", "|---|---|---|---|---|"] + rows)
