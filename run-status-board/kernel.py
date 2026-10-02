import re
import os
import json
import glob
import random
import datetime

RS_SCHEMA = "run-status-board/1"
RS_STATES = ("done_valid", "done_invalid", "running", "pending", "failed", "missing")
RS_RUNNING = ("RUNNING", "COMPLETING", "SUSPENDED", "RESIZING", "SIGNALING", "STAGE_OUT", "CONFIGURING", "STOPPED")
RS_PENDING = ("PENDING", "REQUEUED", "REQUEUE_HOLD", "REQUEUE_FED", "RESV_DEL_HOLD")
RS_FAILED = ("FAILED", "OUT_OF_MEMORY", "TIMEOUT", "CANCELLED", "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "PREEMPTED", "REVOKED", "SPECIAL_EXIT")
RS_BENIGN_REASONS = ("", "None", "Resources", "Priority", "BeginTime")
RS_SQUEUE_CODES = {"PD": "PENDING", "R": "RUNNING", "CG": "COMPLETING", "CD": "COMPLETED", "CA": "CANCELLED", "F": "FAILED", "TO": "TIMEOUT", "NF": "NODE_FAIL", "OOM": "OUT_OF_MEMORY", "PR": "PREEMPTED", "S": "SUSPENDED", "CF": "CONFIGURING", "RQ": "REQUEUED", "RH": "REQUEUE_HOLD", "DL": "DEADLINE", "BF": "BOOT_FAIL", "RV": "REVOKED", "SE": "SPECIAL_EXIT", "ST": "STOPPED"}
RS_COLS = {"jobid": "job_id", "jobidraw": "job_id_raw", "jobname": "job_name", "name": "job_name", "state": "state_raw", "st": "state_raw", "exitcode": "exit_raw", "elapsed": "elapsed", "time": "elapsed", "start": "start", "start_time": "start", "end": "end", "nodelist": "nodelist", "reqmem": "req_mem", "maxrss": "max_rss", "timelimit": "timelimit", "time_limit": "timelimit", "submit": "submit", "submit_time": "submit", "reason": "reason", "nodelist(reason)": "nodelist_reason", "partition": "partition", "alloccpus": "alloc_cpus"}


def rs_tz(tz=None):
    """Resolve tz: 'UTC', '+HH:MM'/'-HHMM', an IANA name, or a tzinfo. None raises."""
    if tz is None:
        raise ValueError("RS_TZ_MISSING: timezone required. Cluster timestamps carry no offset; capture the cluster clock with "
                         "`date +%Y-%m-%dT%H:%M:%S%z` (see SKILL.md) or pass tz='+HH:MM'. Never assume the sandbox clock.")
    if isinstance(tz, datetime.tzinfo):
        return tz
    s = str(tz).strip()
    if s.upper() in ("UTC", "Z", "GMT"):
        return datetime.timezone.utc
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", s)
    if m:
        off = datetime.timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
        return datetime.timezone(-off if m.group(1) == "-" else off)
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(s)
    except Exception as e:
        raise ValueError("RS_TZ_BAD: cannot interpret tz=%r (use 'UTC', '+HH:MM' or an IANA name)" % (tz,)) from e


def rs_ts(text, tz=None):
    """Parse a timestamp to an aware datetime. Naive text needs tz. 'Unknown'/'None'/'' -> None."""
    if isinstance(text, datetime.datetime):
        if text.tzinfo is None:
            return text.replace(tzinfo=rs_tz(tz))
        return text
    s = "" if text is None else str(text).strip()
    if s in ("", "Unknown", "None", "N/A", "n/a", "NONE"):
        return None
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s) if re.search(r"T\S*[+-]\d{4}$", s) else s
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError("RS_TS_BAD: cannot parse timestamp %r (expected ISO 8601, e.g. 2026-10-02T09:15:03)" % (text,)) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=rs_tz(tz))
    return dt


def rs_dur(text):
    """Slurm duration ([D-]HH:MM:SS[.fff], MM:SS, or bare minutes for limits) -> seconds or None."""
    s = "" if text is None else str(text).strip()
    if s in ("", "UNLIMITED", "Partition_Limit", "INVALID", "N/A", "None", "Unknown"):
        return None
    m = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)(?:\.(\d+))?", s)
    if m:
        d, h, mi, sec, frac = m.groups()
        if h is None and d is None:
            return int(mi) * 60 + int(sec) + (float("0." + frac) if frac else 0.0)
        return (int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi) * 60 + int(sec) + (float("0." + frac) if frac else 0.0))
    if re.fullmatch(r"\d+", s):
        return int(s) * 60
    raise ValueError("RS_DUR_BAD: cannot parse duration %r" % (text,))


def rs_mem_mb(text):
    """'64Gn' / '4000Mc' / '123456K' / '2.5G' -> (MB or None, scope 'n'|'c'|None)."""
    s = "" if text is None else str(text).strip()
    if s == "":
        return None, None
    m = re.fullmatch(r"([\d.]+)\s*([KMGTP]?)([nc]?)", s, flags=re.I)
    if not m:
        raise ValueError("RS_MEM_BAD: cannot parse memory %r" % (text,))
    mult = {"": 1.0 / 1024 / 1024, "K": 1.0 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0 ** 2, "P": 1024.0 ** 3}
    return float(m.group(1)) * mult[m.group(2).upper()], (m.group(3).lower() or None)


def rs_expand_range(token):
    """'[32-43%4]' or '[1-3,7]' -> [32..43] / [1,2,3,7]; the %N throttle is dropped."""
    body = token.strip("[]").split("%")[0]
    out = []
    for part in body.split(","):
        part = part.strip()
        if re.fullmatch(r"\d+", part):
            out.append(int(part))
        elif re.fullmatch(r"\d+-\d+", part):
            a, b = part.split("-")
            if int(b) - int(a) > 100000:
                raise ValueError("RS_RANGE_BIG: array range %r too large to expand" % token)
            out.extend(range(int(a), int(b) + 1))
        else:
            raise ValueError("RS_RANGE_BAD: cannot parse array range %r" % token)
    return out


def rs_jobid(text):
    """Split a JobID: '123', '123_4', '123_[32-43%4]', '123_4.batch', '123.0', '123+1' -> dict or None."""
    m = re.fullmatch(r"(\d+)(?:_(\d+|\[[^\]]+\]))?(\+\d+)?(?:\.(\w+))?", str(text).strip())
    if not m:
        return None
    arr, het, step = m.group(2), m.group(3), m.group(4)
    return {"array_job_id": m.group(1), "array_token": arr, "het": het, "step": step,
            "base": m.group(1) + (("_" + arr) if arr else "") + (het or "")}


def rs_state(raw, codes=False):
    """'CANCELLED by 1234' -> ('CANCELLED', '1234'); 'CANCELLED+' -> ('CANCELLED', None)."""
    s = ("" if raw is None else str(raw)).strip()
    by = None
    m = re.match(r"^(\w+)\s+by\s+(\S+)", s)
    if m:
        s, by = m.group(1), m.group(2)
    s = s.rstrip("+").upper()
    if codes and s in RS_SQUEUE_CODES:
        s = RS_SQUEUE_CODES[s]
    return s, by


def rs_state_category(state):
    """running | pending | success | failed | unknown."""
    if state in RS_RUNNING:
        return "running"
    if state in RS_PENDING:
        return "pending"
    if state == "COMPLETED":
        return "success"
    if state in RS_FAILED:
        return "failed"
    return "unknown"


def rs_split_table(text, kind="scheduler"):
    """Split pipe-delimited scheduler text into (header, [(lineno, line)], captured_stamp)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("RS_EMPTY: %s text is empty. The remote query returned nothing or the handoff file was not written; "
                         "re-run the query and check exit_code/stderr." % kind)
    cap, header, data = None, None, []
    for i, ln in enumerate(text.splitlines(), 1):
        s = ln.rstrip("\r")
        if not s.strip():
            continue
        if s.startswith("#CAPTURED"):
            parts = s.split(None, 1)
            cap = parts[1].strip() if len(parts) > 1 else None
            continue
        if s.startswith("#"):
            continue
        if header is None:
            header = [h.strip() for h in s.split("|")]
            if header and header[-1] == "":
                header = header[:-1]
            continue
        data.append((i, s))
    if header is None:
        raise ValueError("RS_NO_HEADER: no header line found in %s text; query without -n/-h so the header is present." % kind)
    return header, data, cap


def rs_clock(cap, tz, strict=True):
    """Resolve (tzinfo, captured_at datetime|None) from a '#CAPTURED <iso with offset>' stamp and/or tz."""
    captured = None
    if cap:
        if not re.search(r"(Z|[+-]\d{2}:?\d{2})$", cap):
            raise ValueError("RS_CAPTURE_NO_OFFSET: #CAPTURED stamp %r has no UTC offset; use `date +%%Y-%%m-%%dT%%H:%%M:%%S%%z`." % cap)
        captured = rs_ts(cap)
        if tz is not None:
            want = rs_tz(tz).utcoffset(captured)
            if want != captured.utcoffset():
                raise ValueError("RS_TZ_CONFLICT: tz=%r (offset %s) disagrees with the cluster clock stamp offset %s. "
                                 "Trust the stamp; drop tz or fix it." % (tz, want, captured.utcoffset()))
        return captured.tzinfo, captured
    if strict:
        raise ValueError("RS_NO_CAPTURE_STAMP: text has no '#CAPTURED <date>' line, so snapshot age and cluster timezone are unknown. "
                         "Prepend `echo \"#CAPTURED $(date +%Y-%m-%dT%H:%M:%S%z)\"` to the remote command (SKILL.md step 2).")
    return rs_tz(tz), None


def rs_parse_sacct(text, tz=None, strict=True):
    """Parse `sacct -P --format=JobID,JobName,State,ExitCode,Elapsed,Start,End,NodeList,ReqMem,MaxRSS[,Timelimit,Submit,Reason,AllocCPUS]`.
    Returns {kind, captured_at, tz, rows, warnings, state_counts}. One row per job/array task; .batch/.extern/.N steps
    fold into their parent (max MaxRSS; a failed/OOM batch step overrides a COMPLETED parent). Array ranges (123_[32-43]) expand
    to one PENDING row per index. strict=True raises on malformed lines, unknown states, missing clock stamp."""
    header, data, cap = rs_split_table(text, "sacct")
    tzinfo, captured = rs_clock(cap, tz, strict)
    keys = [RS_COLS.get(h.lower(), "x_" + h.lower()) for h in header]
    if "job_id" not in keys or "state_raw" not in keys:
        raise ValueError("RS_SACCT_COLS: header %r lacks JobID and/or State. Use the --format in SKILL.md." % header)
    warnings, parents, steps, order = [], {}, [], []
    for ln, s in data:
        f = s.split("|")
        if len(f) == len(keys) + 1 and f[-1] == "":
            f = f[:-1]
        if len(f) != len(keys):
            msg = "line %d has %d fields, header has %d: %r" % (ln, len(f), len(keys), s[:120])
            if strict:
                raise ValueError("RS_SACCT_LINE: " + msg + " (a pipe in a job name? re-run with a simpler --name)")
            warnings.append(msg)
            continue
        rec = dict(zip(keys, f))
        ident = rs_jobid(rec["job_id"])
        if ident is None:
            msg = "line %d unparseable JobID %r" % (ln, rec["job_id"])
            if strict:
                raise ValueError("RS_SACCT_JOBID: " + msg)
            warnings.append(msg)
            continue
        state, by = rs_state(rec["state_raw"])
        rec.update(ident=ident, state=state, cancelled_by=by)
        if ident["step"] is not None:
            steps.append(rec)
            continue
        key = ident["base"]
        if key in parents:
            warnings.append("duplicate row for %s; kept the later start" % key)
            old = parents[key]
            if (rs_ts(old.get("start"), tzinfo) or datetime.datetime.min.replace(tzinfo=tzinfo)) >= (rs_ts(rec.get("start"), tzinfo) or datetime.datetime.min.replace(tzinfo=tzinfo)):
                continue
        else:
            order.append(key)
        parents[key] = rec
    for st in steps:
        par = parents.get(st["ident"]["base"])
        if par is None:
            warnings.append("step %s has no parent row" % st["job_id"])
            continue
        par.setdefault("steps", []).append(st)
    rows = []
    for key in order:
        p = parents[key]
        ident = p["ident"]
        state, notes = p["state"], []
        for st in p.get("steps", []):
            if st["ident"]["step"] == "extern":
                continue
            if st["state"] == "OUT_OF_MEMORY" and state != "OUT_OF_MEMORY":
                notes.append("oom_in_step: parent was %s but step %s is OUT_OF_MEMORY (cgroup OOM kill; output of this job is suspect even with exit 0)" % (state, st["job_id"]))
                state = "OUT_OF_MEMORY"
            elif rs_state_category(st["state"]) == "failed" and rs_state_category(state) == "success":
                notes.append("step_failed: parent COMPLETED but step %s is %s" % (st["job_id"], st["state"]))
                state = st["state"]
        cat = rs_state_category(state)
        if cat == "unknown":
            if strict:
                raise ValueError("RS_STATE_UNKNOWN: scheduler state %r for job %s is not recognised; add it to RS_RUNNING/RS_PENDING/RS_FAILED "
                                 "or inspect `sacct -j %s` by hand before reporting status." % (p["state_raw"], p["job_id"], ident["array_job_id"]))
            notes.append("unknown_state:" + state)
        code = sig = None
        m = re.fullmatch(r"(\d+):(\d+)", (p.get("exit_raw") or "").strip())
        if m:
            code, sig = int(m.group(1)), int(m.group(2))
        if state == "COMPLETED" and (code or sig):
            notes.append("completed_nonzero_exit %s" % p["exit_raw"])
            state, cat = "FAILED", "failed"
        elapsed, limit = rs_dur(p.get("elapsed")), rs_dur(p.get("timelimit"))
        if state == "TIMEOUT":
            if limit is not None and elapsed is not None and elapsed < 0.9 * limit:
                notes.append("timeout_suspect: Elapsed %.0fs is %.0f%% of Timelimit %.0fs; not a wall-time kill. Check for an account/association "
                             "CPU-minute cap or preemption (job reason / scontrol show assoc) before raising walltime." % (elapsed, 100.0 * elapsed / limit, limit))
            elif limit is None:
                notes.append("timeout_unverified: no Timelimit column; cannot tell wall-time from cap kill")
        rss = max([rs_mem_mb(x.get("max_rss"))[0] or 0.0 for x in [p] + p.get("steps", [])] or [0.0])
        req, scope = rs_mem_mb(p.get("req_mem"))
        if req is not None and scope == "c" and (p.get("alloc_cpus") or "").isdigit():
            req, scope = req * int(p["alloc_cpus"]), "n"
        if state != "OUT_OF_MEMORY" and req and scope == "n" and rss >= 0.95 * req and (cat == "failed" or sig in (9, 125, 137)):
            notes.append("oom_suspect: MaxRSS %.0fMB >= 95%% of ReqMem %.0fMB" % (rss, req))
        base = {"job_id": p["job_id"], "array_job_id": ident["array_job_id"], "job_name": p.get("job_name", ""),
                "state_raw": p["state_raw"], "state": state, "category": cat, "cancelled_by": p["cancelled_by"],
                "exit_code": code, "signal": sig, "elapsed_s": elapsed, "timelimit_s": limit,
                "start": (rs_ts(p.get("start"), tzinfo).isoformat() if rs_ts(p.get("start"), tzinfo) else None),
                "end": (rs_ts(p.get("end"), tzinfo).isoformat() if rs_ts(p.get("end"), tzinfo) else None),
                "nodelist": p.get("nodelist", ""), "req_mem_mb": req, "max_rss_mb": rss or None,
                "reason": (p.get("reason") or "").strip(), "notes": notes, "source": "sacct"}
        tok = ident["array_token"]
        if tok and tok.startswith("["):
            for idx in rs_expand_range(tok):
                r = dict(base)
                r.update(job_id="%s_%d" % (ident["array_job_id"], idx), array_index=idx, from_range=True)
                rows.append(r)
        else:
            base.update(array_index=int(tok) if tok else None, from_range=False)
            rows.append(base)
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {"kind": "sacct", "captured_at": captured.isoformat() if captured else None, "tz": str(tzinfo),
            "rows": rows, "warnings": warnings, "state_counts": counts}


def rs_parse_squeue(text, tz=None, strict=True):
    """Parse `squeue -o '%i|%j|%T|%M|%l|%V|%S|%R'` (keep the header; do not pass -h). Returns the same shape as rs_parse_sacct.
    Pending rows keep the Slurm reason; reasons outside RS_BENIGN_REASONS set reason_blocking=True. Array ranges expand."""
    header, data, cap = rs_split_table(text, "squeue")
    tzinfo, captured = rs_clock(cap, tz, strict)
    keys = [RS_COLS.get(h.lower(), "x_" + h.lower()) for h in header]
    if "job_id" not in keys or "state_raw" not in keys:
        raise ValueError("RS_SQUEUE_COLS: header %r lacks JOBID/STATE. Use -o '%%i|%%j|%%T|%%M|%%l|%%V|%%S|%%R'." % header)
    rows, warnings = [], []
    for ln, s in data:
        f = s.split("|")
        if len(f) != len(keys):
            msg = "line %d has %d fields, header has %d: %r" % (ln, len(f), len(keys), s[:120])
            if strict:
                raise ValueError("RS_SQUEUE_LINE: " + msg)
            warnings.append(msg)
            continue
        rec = dict(zip(keys, f))
        ident = rs_jobid(rec["job_id"])
        if ident is None:
            raise ValueError("RS_SQUEUE_JOBID: line %d unparseable JobID %r" % (ln, rec["job_id"]))
        state, by = rs_state(rec["state_raw"], codes=True)
        cat = rs_state_category(state)
        if cat == "unknown" and strict:
            raise ValueError("RS_STATE_UNKNOWN: squeue state %r for job %s not recognised" % (rec["state_raw"], rec["job_id"]))
        reason = (rec.get("reason") or rec.get("nodelist_reason") or "").strip().strip("()")
        notes = []
        blocking = cat == "pending" and reason not in RS_BENIGN_REASONS
        if blocking:
            notes.append("pending_blocking_reason: %s (will not start until cleared; not a failure and not a timeout)" % reason)
        base = {"job_id": rec["job_id"], "array_job_id": ident["array_job_id"], "job_name": rec.get("job_name", ""),
                "state_raw": rec["state_raw"], "state": state, "category": cat, "cancelled_by": by,
                "exit_code": None, "signal": None, "elapsed_s": rs_dur(rec.get("elapsed")), "timelimit_s": rs_dur(rec.get("timelimit")),
                "start": None, "end": None, "expected_start": (rs_ts(rec.get("start"), tzinfo).isoformat() if rs_ts(rec.get("start"), tzinfo) else None),
                "nodelist": "" if cat == "pending" else reason, "req_mem_mb": None, "max_rss_mb": None,
                "reason": reason if cat == "pending" else "", "reason_blocking": blocking, "notes": notes, "source": "squeue"}
        tok = ident["array_token"]
        if tok and tok.startswith("["):
            for idx in rs_expand_range(tok):
                r = dict(base)
                r.update(job_id="%s_%d" % (ident["array_job_id"], idx), array_index=idx, from_range=True)
                rows.append(r)
        else:
            base.update(array_index=int(tok) if tok else None, from_range=False)
            rows.append(base)
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {"kind": "squeue", "captured_at": captured.isoformat() if captured else None, "tz": str(tzinfo),
            "rows": rows, "warnings": warnings, "state_counts": counts}


def rs_since(ts_text, now, tz=None, strict=True):
    """Seconds between a log/file timestamp and `now` (both resolved with an explicit tz). A negative age means a clock/tz
    mix-up: strict raises. Use this instead of eyeballing 'last write 08:52' against another clock."""
    t, n = rs_ts(ts_text, tz), rs_ts(now, tz)
    if t is None or n is None:
        raise ValueError("RS_SINCE_NONE: timestamp %r or now %r is empty" % (ts_text, now))
    age = (n - t).total_seconds()
    if age < -60 and strict:
        raise ValueError("RS_SINCE_FUTURE: timestamp %s is %.0fs in the future of %s; timezone/offset mismatch between the log and the clock." % (t.isoformat(), -age, n.isoformat()))
    return {"age_s": age, "timestamp": t.isoformat(), "now": n.isoformat()}


def rs_norm_check(res, unit):
    """Normalise an output-check return value to {present, valid, reason, detail}.
    None -> nothing written; True / (True, note[, detail]) -> valid; False / (False, reason[, detail]) -> present but invalid;
    dict with 'valid' (+ optional present, reason, found, expected, finished_at) also accepted."""
    if res is None:
        return {"present": False, "valid": False, "reason": "no output", "detail": {}}
    if isinstance(res, bool):
        return {"present": True, "valid": res, "reason": "" if res else "check returned False", "detail": {}}
    if isinstance(res, tuple) and res and isinstance(res[0], bool):
        det = res[2] if len(res) > 2 and isinstance(res[2], dict) else {}
        return {"present": True, "valid": res[0], "reason": str(res[1]) if len(res) > 1 else "", "detail": det}
    if isinstance(res, dict) and "valid" in res:
        det = {k: res[k] for k in ("found", "expected", "finished_at") if k in res}
        return {"present": bool(res.get("present", True)), "valid": bool(res["valid"]) and bool(res.get("present", True)),
                "reason": str(res.get("reason", "")), "detail": det}
    raise TypeError("RS_CHECK_RETURN: output check for unit %r returned %r; return None (absent), True/False, (valid, reason[, detail]) or a dict with 'valid'." % (unit, res))


def rs_norm_sched(sched_rows):
    """Accept a parse dict, a list of parse dicts, or a flat list of rows -> (rows, oldest captured_at|None, warnings)."""
    if sched_rows is None:
        return None, None, []
    parsed = [sched_rows] if isinstance(sched_rows, dict) else list(sched_rows)
    rows, caps, warns = [], [], []
    for p in parsed:
        if isinstance(p, dict) and "rows" in p:
            rows.extend(p["rows"])
            warns.extend(p.get("warnings", []))
            if p.get("captured_at"):
                caps.append(rs_ts(p["captured_at"]))
            else:
                warns.append("a %s snapshot has no capture time" % p.get("kind", "scheduler"))
        elif isinstance(p, dict) and "job_id" in p:
            rows.append(p)
        else:
            raise TypeError("RS_SCHED_TYPE: sched_rows entries must be rs_parse_* results or parsed rows, got %r" % type(p).__name__)
    return rows, (min(caps) if caps else None), warns


def rs_pick_sched(cands):
    live = [r for r in cands if r["category"] in ("running", "pending")]
    if live:
        run = [r for r in live if r["category"] == "running"]
        return (run or live)[0]
    floor = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    return sorted(cands, key=lambda r: (rs_ts(r.get("end") or r.get("start")) or floor))[-1]


def rs_board(manifest_rows, unit_key, output_check, sched_rows=None, started_at=None, sched_key=None, now=None,
             max_sched_age_s=900, allow_presence_only=False, strict=True):
    """Per-unit status from (a) the expected manifest, (b) outputs actually present and valid, (c) scheduler rows.
    manifest_rows: list of dicts or DataFrame (the EXPECTED units; never derive it from files found).
    unit_key: manifest column name or callable(row)->key.
    output_check: callable(row)-> None | bool | (valid, reason[, detail]) | dict (see rs_norm_check), or a glob template like
      'results/{shard}/*.json' (presence only; needs allow_presence_only=True because presence is not validity).
    sched_rows: rs_parse_sacct/rs_parse_squeue result(s). sched_key: None (job_name == unit key), manifest column holding the
      job id ('123_4') or job name, or callable(sched_row)->unit key.
    States: done_valid, done_invalid, running, pending, failed, missing. strict=True raises on structural problems."""
    if hasattr(manifest_rows, "to_dict"):
        manifest_rows = manifest_rows.to_dict("records")
    manifest_rows = list(manifest_rows or [])
    if not manifest_rows:
        raise ValueError("RS_MANIFEST_EMPTY: the expected-unit manifest is empty; status without a denominator is meaningless.")
    kf = unit_key if callable(unit_key) else (lambda r: r[unit_key])
    keys = []
    for r in manifest_rows:
        try:
            keys.append(str(kf(r)))
        except Exception as e:
            raise ValueError("RS_UNIT_KEY: unit_key failed on manifest row %r: %s" % (r, e)) from e
    dups = sorted({k for k in keys if keys.count(k) > 1})
    if dups:
        raise ValueError("RS_UNIT_DUP: %d duplicate unit keys, e.g. %s. Unit keys must encode every swept parameter (seed, dataset, config)." % (len(dups), dups[:5]))
    basis = "user_check"
    if isinstance(output_check, str):
        if not allow_presence_only:
            raise ValueError("RS_PRESENCE_ONLY: a glob only proves a file exists, not that it is complete or finite. Pass a validity "
                             "callable (e.g. wrapping your completion gate), or allow_presence_only=True and accept the board will say so.")
        tmpl = output_check
        basis = "presence_only"

        def output_check(row):
            hits = [p for p in glob.glob(tmpl.format(**row)) if os.path.isfile(p) and os.path.getsize(p) > 0]
            return (True, "%d file(s) present" % len(hits)) if hits else None
    elif not callable(output_check):
        raise TypeError("RS_CHECK_TYPE: output_check must be a callable or glob template")
    nowdt = rs_ts(now, "UTC") if now is not None else datetime.datetime.now(datetime.timezone.utc)
    start_dt = None
    if started_at is not None:
        start_dt = rs_ts(started_at)
        if start_dt.tzinfo is None:
            raise ValueError("RS_STARTED_NAIVE: started_at needs an explicit offset")
    rows, cap, warns = rs_norm_sched(sched_rows)
    warnings = list(warns)
    smap, unmapped, age = {}, 0, None
    if rows is not None:
        if not rows:
            msg = "RS_SCHED_EMPTY: scheduler text parsed to zero jobs; pass sched_rows=None if nothing was submitted, otherwise the query/job ids are wrong."
            if strict:
                raise ValueError(msg)
            warnings.append(msg)
        idx = {}
        if callable(sched_key):
            pass
        elif sched_key is None:
            idx = {k: k for k in keys}
        else:
            for r, k in zip(manifest_rows, keys):
                tok = str(r.get(sched_key, "")).strip()
                if tok:
                    if tok in idx and idx[tok] != k:
                        raise ValueError("RS_SCHED_KEY_AMBIGUOUS: manifest column %r value %r maps to several units; use job ids like '123_4'." % (sched_key, tok))
                    idx[tok] = k
        for sr in rows:
            if callable(sched_key):
                u = sched_key(sr)
                u = None if u is None else str(u)
            else:
                u = idx.get(sr["job_id"]) or idx.get(sr.get("job_name", ""))
            if u is not None and u in keys:
                smap.setdefault(u, []).append(sr)
            else:
                unmapped += 1
        if rows and not smap:
            msg = ("RS_SCHED_UNMAPPED: none of %d scheduler rows matched a manifest unit. sched_key is wrong (default matches job_name == unit key); "
                   "a mapping that silently matches nothing makes every unit look 'missing'." % len(rows))
            if strict:
                raise ValueError(msg)
            warnings.append(msg)
        if cap is None:
            msg = "scheduler snapshot has no capture time; status may be stale"
            if strict and rows:
                raise ValueError("RS_SCHED_UNDATED: " + msg)
            warnings.append(msg)
        else:
            age = (nowdt - cap).total_seconds()
            if age > max_sched_age_s:
                msg = "scheduler snapshot is %.0fs old (limit %ds): re-query before answering a status question" % (age, max_sched_age_s)
                if strict:
                    raise RuntimeError("RS_SCHED_STALE: " + msg)
                warnings.append(msg)
    units, anomalies = [], []
    sub_found = sub_expected = 0
    for r, k in zip(manifest_rows, keys):
        try:
            raw = output_check(r)
        except Exception as e:
            if strict:
                raise RuntimeError("RS_CHECK_ERROR: output check raised on unit %s: %r. A check must return None for 'absent', not raise." % (k, e)) from e
            raw = (False, "check raised %r" % (e,))
        out = rs_norm_check(raw, k)
        det = out["detail"]
        if det.get("finished_at") is not None and rs_ts(det["finished_at"]).tzinfo is None:
            raise ValueError("RS_FINISHED_NAIVE: unit %s finished_at needs an explicit offset" % k)
        if isinstance(det.get("found"), int) and isinstance(det.get("expected"), int):
            sub_found += det["found"]
            sub_expected += det["expected"]
        cands = smap.get(k, [])
        sr = rs_pick_sched(cands) if cands else None
        cat = sr["category"] if sr else None
        notes = list(sr["notes"]) if sr else []
        if out["present"] and out["valid"]:
            state, reason = "done_valid", out["reason"]
            if cat == "failed":
                notes.append("conflict: output valid but latest scheduler state is %s" % sr["state"])
            if cat in ("running", "pending"):
                notes.append("output valid while a job is %s (rerun/overwrite in progress?)" % sr["state"].lower())
        elif out["present"]:
            if cat == "running":
                state, reason = "running", "partial output: " + out["reason"]
            elif cat == "pending":
                state, reason = "pending", "stale invalid output present: " + out["reason"]
            elif cat == "failed":
                state, reason = "failed", "%s; partial output invalid: %s" % (sr["state"], out["reason"])
            else:
                state, reason = "done_invalid", out["reason"]
                if cat == "success":
                    notes.append("anomaly: scheduler %s (exit %s) but output invalid; marker/exit code cannot be trusted" % (sr["state"], sr["exit_code"]))
        else:
            if cat == "running":
                state, reason = "running", "job %s" % sr["job_id"]
            elif cat == "pending":
                state, reason = "pending", sr["reason"] or "queued"
                if sr.get("reason_blocking"):
                    notes.append("pending_blocking_reason: %s" % sr["reason"])
            elif cat == "failed":
                state, reason = "failed", sr["state"] + (" (exit %s)" % sr["exit_code"] if sr["exit_code"] else "")
            elif cat == "success":
                state, reason = "missing", "scheduler COMPLETED but expected output absent"
                notes.append("anomaly: scheduler COMPLETED, no output")
            else:
                state, reason = "missing", "no output and no scheduler record"
        fin = det.get("finished_at")
        if state == "done_valid" and fin is None and sr and cat == "success":
            fin = sr.get("end")
        if state == "done_valid" and fin is not None:
            fin = rs_ts(fin).isoformat()
        for n in notes:
            if n.startswith("anomaly") or n.startswith("conflict") or n.startswith("oom_") or n.startswith("timeout_suspect"):
                anomalies.append("%s: %s" % (k, n))
        units.append({"unit": k, "state": state, "reason": reason, "sched_state": sr["state"] if sr else None,
                      "sched_job_id": sr["job_id"] if sr else None, "elapsed_s": sr["elapsed_s"] if sr else None,
                      "max_rss_mb": sr["max_rss_mb"] if sr else None, "attempts_seen": len(cands),
                      "output_present": out["present"], "output_valid": out["valid"], "finished_at": fin if state == "done_valid" else None,
                      "notes": notes})
    totals = {s: sum(1 for u in units if u["state"] == s) for s in RS_STATES}
    n = len(units)
    fsum = {}
    for u in units:
        if u["state"] in ("failed", "done_invalid", "missing") and (u["sched_state"] or u["state"]):
            key = u["sched_state"] or u["state"]
            fsum[key] = fsum.get(key, 0) + 1
    blocked = sum(1 for u in units if u["state"] == "pending" and any(x.startswith("pending_blocking") for x in u["notes"]))
    head = "%d/%d units done_valid (%.1f%%) as of %s | running %d, pending %d (%d blocked by a limit reason), failed %d, done_invalid %d, missing %d" % (
        totals["done_valid"], n, 100.0 * totals["done_valid"] / n, nowdt.isoformat(timespec="seconds"), totals["running"], totals["pending"],
        blocked, totals["failed"], totals["done_invalid"], totals["missing"])
    return {"schema": RS_SCHEMA, "generated_at": nowdt.isoformat(timespec="seconds"), "started_at": start_dt.isoformat() if start_dt else None,
            "elapsed_s": (nowdt - start_dt).total_seconds() if start_dt else None, "n_units": n, "totals": totals,
            "pct_done_valid": 100.0 * totals["done_valid"] / n, "headline": head, "validity_basis": basis,
            "sched": {"provided": rows is not None, "captured_at": cap.isoformat() if cap else None, "age_s": age,
                      "n_rows": len(rows) if rows is not None else 0, "n_unmapped": unmapped},
            "failure_summary": fsum, "subunits": {"found": sub_found, "expected": sub_expected},
            "needs_action": [u["unit"] for u in units if u["state"] in ("failed", "done_invalid")],
            "anomalies": anomalies, "warnings": warnings, "units": units}


def rs_eta(board, history=None, min_done=5, interval=0.8, seed=0, now=None, strict=True):
    """Throughput ETA for outstanding work (running + pending + missing) with an `interval` (default 80%) band, from a
    bootstrap of inter-completion gaps of done_valid units that have a finish time. Refuses (refused=True; raises if strict)
    when fewer than min_done timestamped completions exist. Units needing resubmission (failed, done_invalid) are excluded and
    reported. history: earlier board dicts or rs_history(root) records (their finished times fill in aged-out jobs)."""
    stamps = {u["unit"]: rs_ts(u["finished_at"]) for u in board["units"] if u["state"] == "done_valid" and u.get("finished_at")}
    for h in history or []:
        for k, v in (h.get("finished") or {u["unit"]: u.get("finished_at") for u in h.get("units", []) if u.get("finished_at")}).items():
            if k not in stamps and v:
                stamps[k] = rs_ts(v)
    t = board["totals"]
    remaining = t["running"] + t["pending"] + t["missing"]
    res = {"refused": False, "reason": "", "n_completed_used": len(stamps), "remaining": remaining,
           "excluded_need_rerun": t["failed"] + t["done_invalid"], "n_unsubmitted": t["missing"], "interval": interval,
           "eta_low": None, "eta_median": None, "eta_high": None, "seconds_low": None, "seconds_median": None, "seconds_high": None,
           "rate_per_hour": None, "warnings": [],
           "method": "bootstrap of inter-completion gaps; assumes concurrency stays as observed and unsubmitted units dispatch at the same rate"}

    def refuse(msg):
        res.update(refused=True, reason=msg)
        if strict:
            raise RuntimeError("RS_ETA_REFUSED: " + msg)
        return res
    if remaining == 0:
        res["reason"] = "nothing outstanding"
        return res
    if len(stamps) < min_done:
        return refuse("only %d completed units have finish times (need >= %d); no ETA. Say 'not enough completions to estimate'." % (len(stamps), min_done))
    times = sorted(stamps.values())
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    if sum(gaps) <= 0:
        return refuse("all completions share one timestamp; throughput not measurable")
    nowdt = rs_ts(now, "UTC") if now is not None else rs_ts(board["generated_at"])
    rng = random.Random(seed)
    sums = sorted(sum(rng.choice(gaps) for _ in range(remaining)) for _ in range(2000))
    q = lambda p: sums[min(len(sums) - 1, int(p * len(sums)))]
    lo, med, hi = q((1 - interval) / 2), q(0.5), q(1 - (1 - interval) / 2)
    since_last = (nowdt - times[-1]).total_seconds()
    med_gap = sorted(gaps)[len(gaps) // 2]
    if med_gap > 0 and since_last > 3 * med_gap:
        res["warnings"].append("no completion for %.0fs vs median gap %.0fs: throughput may have dropped. Inspect running/pending units on the board; this is not a diagnosis." % (since_last, med_gap))
    if t["missing"]:
        res["warnings"].append("%d units not yet submitted; ETA assumes they start without delay" % t["missing"])
    res.update(seconds_low=lo, seconds_median=med, seconds_high=hi,
               eta_low=(nowdt + datetime.timedelta(seconds=lo)).isoformat(timespec="minutes"),
               eta_median=(nowdt + datetime.timedelta(seconds=med)).isoformat(timespec="minutes"),
               eta_high=(nowdt + datetime.timedelta(seconds=hi)).isoformat(timespec="minutes"),
               rate_per_hour=3600.0 * len(gaps) / sum(gaps))
    return res


def rs_render(board, eta=None, max_list=25):
    """Markdown status board. Quote the headline and the generated_at/snapshot times verbatim in any status answer."""
    b, t = board, board["totals"]
    L = ["# Run status board", "", "**%s**" % b["headline"], "",
         "- generated_at: %s | run started: %s | elapsed: %s" % (b["generated_at"], b["started_at"] or "unknown", ("%.1f h" % (b["elapsed_s"] / 3600.0)) if b["elapsed_s"] is not None else "unknown"),
         "- scheduler snapshot: %s (age %s)" % (b["sched"]["captured_at"] or ("none provided" if not b["sched"]["provided"] else "undated"),
                                               ("%.0fs" % b["sched"]["age_s"]) if b["sched"]["age_s"] is not None else "n/a"),
         "- output validity basis: %s" % ("PRESENCE ONLY (files exist; contents not validated)" if b["validity_basis"] == "presence_only" else "user-supplied validity check")]
    if b["subunits"]["expected"]:
        L.append("- sub-units (e.g. seeds) found/expected across checked outputs: %d/%d" % (b["subunits"]["found"], b["subunits"]["expected"]))
    L += ["", "| state | units |", "|---|---|"] + ["| %s | %d |" % (s, t[s]) for s in RS_STATES] + ["| **total expected** | **%d** |" % b["n_units"], ""]
    if eta is not None:
        if eta["refused"]:
            L.append("**ETA: none.** %s" % eta["reason"])
        elif eta["eta_median"]:
            L.append("**ETA (median, %d%% band)** %s (%s to %s), from %d completions; %d units outstanding; %d need resubmission and are excluded."
                     % (round(eta["interval"] * 100), eta["eta_median"], eta["eta_low"], eta["eta_high"], eta["n_completed_used"], eta["remaining"], eta["excluded_need_rerun"]))
        else:
            L.append("ETA: %s" % eta["reason"])
        L += ["- method: " + eta["method"]] + ["- warning: " + w for w in eta["warnings"]] + [""]
    for s in ("failed", "done_invalid", "running", "pending", "missing"):
        us = [u for u in b["units"] if u["state"] == s]
        if not us:
            continue
        L.append("## %s (%d)" % (s, len(us)))
        for u in us[:max_list]:
            L.append("- %s: %s%s" % (u["unit"], u["reason"], (" [job %s]" % u["sched_job_id"]) if u["sched_job_id"] else ""))
        if len(us) > max_list:
            L.append("- ... %d more in status_board.json" % (len(us) - max_list))
        L.append("")
    if b["failure_summary"]:
        L += ["## failure causes (scheduler state or unit state)", ""] + ["- %s: %d" % kv for kv in sorted(b["failure_summary"].items())] + [""]
    if b["anomalies"] or b["warnings"]:
        L.append("## anomalies and warnings")
        L += ["- " + a for a in (b["anomalies"][:max_list] + b["warnings"])] + [""]
    L.append("_Status claims must quote this board. Anything not in done_valid is not done._")
    return "\n".join(L) + "\n"


def rs_save(board, root, eta=None):
    """Write status_board.md + status_board.json under root and append a compact record to status_history.jsonl.
    Then save_artifacts the two files with version_of so the board is one artifact history."""
    os.makedirs(root, exist_ok=True)
    p_md, p_js, p_hi = (os.path.join(root, n) for n in ("status_board.md", "status_board.json", "status_history.jsonl"))
    with open(p_md, "w") as f:
        f.write(rs_render(board, eta))
    with open(p_js, "w") as f:
        json.dump({"board": board, "eta": eta}, f, indent=1)
    rec = {"generated_at": board["generated_at"], "totals": board["totals"],
           "finished": {u["unit"]: u["finished_at"] for u in board["units"] if u.get("finished_at")}}
    with open(p_hi, "a") as f:
        f.write(json.dumps(rec) + "\n")
    for p in (p_md, p_js):
        if os.path.getsize(p) == 0:
            raise RuntimeError("RS_SAVE_EMPTY: %s is empty after write" % p)
    return {"md": p_md, "json": p_js, "history": p_hi}


def rs_history(root):
    """Read status_history.jsonl written by rs_save ([] when absent). Corrupt lines raise."""
    p = os.path.join(root, "status_history.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for i, ln in enumerate(open(p), 1):
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except ValueError as e:
                raise ValueError("RS_HISTORY_CORRUPT: %s line %d: %s" % (p, i, e)) from e
    return out
