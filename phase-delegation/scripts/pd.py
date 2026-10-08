"""phase-delegation helpers (stdlib only). Load in the repl tool, where host.delegate lives:
    exec(open(PD).read())      # PD = path of this file inside the published skill
Builds phase briefs, the structured-output schema, delegate requests, and audits results."""
import re

PD_PHASES = {
    "run": "launch the job(s), wait for them, and harvest the outputs",
    "validate": "check finished outputs against the manifest and acceptance checks",
    "triage": "find why a run failed and propose or apply the infrastructure fix",
    "analyze": "compute the stated result from finished, validated outputs",
}
PD_DEFAULT_PROFILE = {"run": "CLUSTER_OPS", "triage": "CLUSTER_OPS", "validate": None, "analyze": None}
PD_LIVE = ("queued", "pending", "running", "submitted", "configuring", "completing")

PD_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["done", "failed", "needs_human", "partial"]},
        "summary": {"type": "string", "maxLength": 1200},
        "jobs": {"type": "array", "items": {"type": "object", "properties": {
            "ledger_id": {"type": "string"}, "host": {"type": "string"}, "scheduler_id": {"type": "string"},
            "state": {"type": "string"}, "watcher_state_dir": {"type": "string"}, "report_line": {"type": "string"}},
            "required": ["ledger_id", "host", "state"]}},
        "artifacts": {"type": "array", "items": {"type": "object", "properties": {
            "role": {"type": "string"}, "filename": {"type": "string"}, "version_id": {"type": "string"}},
            "required": ["role", "version_id"]}},
        "checks": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "ok": {"type": "boolean"}, "evidence": {"type": "string"}},
            "required": ["name", "ok", "evidence"]}},
        "next_action": {"type": "string"},
    },
    "required": ["status", "summary", "jobs", "artifacts", "checks"],
}

_FALLBACK = re.compile(r"(if|when|otherwise|fallback|else)\b[^.\n]{0,80}\b(simulat|synthetic|fake|fabricat|placeholder data|made[- ]up)", re.I)


def pd_marker(version_id):
    """Artifact marker built by concatenation, so no code cell pre-resolves it to a local path."""
    return "{" + "{artifact:" + str(version_id) + "}" + "}"


def pd_brief(phase, objective, inputs, outputs, acceptance, requirements=(), host=None, notes=""):
    """Task text for one phase sub-agent.

    inputs: {role: version_id}; outputs: [role, ...] the child must save and return as artifacts;
    acceptance: [check, ...] each one concrete (command, file, threshold); requirements: hard
    requirements (full data, named reference, settings). Raises ValueError on an unknown phase,
    empty acceptance, an input without a version id, or text that pre-authorizes fabricated data.
    """
    if phase not in PD_PHASES:
        raise ValueError("phase must be one of %s" % sorted(PD_PHASES))
    if not acceptance:
        raise ValueError("acceptance is empty: a phase without a checkable acceptance cannot be audited")
    for role, vid in (inputs or {}).items():
        if not vid or not re.match(r"^[0-9a-f-]{8,}$", str(vid)):
            raise ValueError("input %r has no artifact version id (got %r): save_artifacts it first" % (role, vid))
    if phase == "run" and not host:
        raise ValueError("a run phase needs the compute target (host)")
    text_all = " ".join([objective, notes] + list(requirements) + list(acceptance))
    if _FALLBACK.search(text_all):
        raise ValueError("brief pre-authorizes simulated/synthetic fallback data; an approval-gated or failed "
                         "input must be surfaced, not replaced")
    L = ["PHASE: %s (%s)." % (phase, PD_PHASES[phase]), "", "OBJECTIVE: " + objective.strip(), ""]
    if inputs:
        L += ["INPUTS (artifact markers resolve to readable paths in your kernel):"]
        L += ["- %s: %s" % (r, pd_marker(v)) for r, v in inputs.items()] + [""]
    if host:
        L += ["COMPUTE TARGET: %s" % host, ""]
    if requirements:
        L += ["HARD REQUIREMENTS (your result is checked against these):"] + ["- " + r for r in requirements] + [""]
    L += ["ACCEPTANCE CHECKS (report each one in `checks` with the evidence you saw):"]
    L += ["- " + a for a in acceptance] + [""]
    L += ["OUTPUTS: save each with save_artifacts and return it in `artifacts` with its role: " + ", ".join(outputs), ""]
    L += ["RULES:",
          "- Read the compute target's notes (compute_details) before any submit; load the cluster-rules skill for cluster work.",
          "- Run any job longer than ~10 min under job-watch inside the submitted job, so it ends with one report line.",
          "- A job's compute_done reaches only you, the frame that submitted it. Do not return while a job you "
          "submitted is still live: park on wait_for_notification (timeout_seconds=1800) until it is terminal, then harvest.",
          "- Touch only job ids you submitted; never account-wide scheduler commands.",
          "- After two failed submits of the same job, stop and return status needs_human with what you saw.",
          "- An approval denied or a resource blocked is reported as needs_human; never substitute other data.",
          "- `deviations`: list every place you processed less than, or other than, what this brief names; [\"none\"] otherwise.",
          "- Return via host.submit_output matching the schema; keep `summary` to the result and its evidence."]
    if notes:
        L += ["", "NOTES: " + notes.strip()]
    return "\n".join(L)


def pd_request(name, brief, phase, profile="default", context_summary=None, model=None):
    """host.delegate request dict with PD_SCHEMA; profile 'default' picks PD_DEFAULT_PROFILE[phase]."""
    p = PD_DEFAULT_PROFILE.get(phase) if profile == "default" else profile
    r = {"task": brief, "name": name, "output_schema": PD_SCHEMA}
    if p:
        r["profile"] = p
    if context_summary:
        r["context_summary"] = context_summary
    if model:
        r["model"] = model
    return r


def pd_audit(result, outputs=(), acceptance_n=None):
    """Audit one collected result before anything from it is reported.

    Returns {'ok', 'status', 'problems', 'limits', 'artifacts', 'live_jobs'}. ok requires: the
    child completed, returned structured output, status done, every check ok, every expected
    output role present with a version id, no job left live, and deviations declared. Declared
    deviations other than ["none"] are returned as limits (they travel with the numbers).
    """
    probs, limits = [], []
    if result.get("error") or result.get("status") != "completed":
        probs.append("child %s: %s" % (result.get("status"), result.get("error") or "not completed"))
    so = result.get("structured_output")
    if not so or result.get("structured_output_unsatisfied"):
        probs.append("no structured output (child answered in prose)")
        return {"ok": False, "status": None, "problems": probs, "limits": limits, "artifacts": [], "live_jobs": []}
    dev = result.get("deviations") or ["UNDECLARED (missing)"]
    if str(dev[0]).startswith("UNDECLARED"):
        probs.append("deviations undeclared: coverage unknown")
    elif dev != ["none"]:
        limits = list(dev)
    if so.get("status") != "done":
        probs.append("phase status %s: %s" % (so.get("status"), so.get("next_action") or so.get("summary", "")[:200]))
    bad = [c["name"] for c in so.get("checks", []) if not c.get("ok")]
    if bad:
        probs.append("failed checks: " + ", ".join(bad))
    if acceptance_n is not None and len(so.get("checks", [])) < acceptance_n:
        probs.append("%d of %d acceptance checks reported" % (len(so.get("checks", [])), acceptance_n))
    roles = {a.get("role"): a.get("version_id") for a in so.get("artifacts", [])}
    miss = [o for o in outputs if not roles.get(o)]
    if miss:
        probs.append("missing outputs: " + ", ".join(miss))
    live = [j["ledger_id"] for j in so.get("jobs", []) if str(j.get("state", "")).lower() in PD_LIVE]
    if live:
        probs.append("jobs still live after the child returned (their notices are lost): " + ", ".join(live))
    return {"ok": not probs, "status": so.get("status"), "problems": probs, "limits": limits,
            "artifacts": so.get("artifacts", []), "live_jobs": live}
