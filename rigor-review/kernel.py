import os
import re
import json
import time
import datetime

SEVERITIES = ("fatal", "major", "minor")
STATUSES = ("open", "resolved", "disputed", "wontfix")
CATEGORIES = ("design", "baselines", "leakage", "statistics", "metrics", "data", "claims", "reporting", "reproducibility", "novelty")


def rigor_now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def rigor_ledger_new(project):
    return {"schema": 1, "project": project, "created": rigor_now(), "updated": rigor_now(),
            "reviews": [], "findings": [], "method_cards": []}


def rigor_ledger_load(path=None):
    """Return (ledger, artifact_id). Looks up the newest rigor_ledger.json artifact in this project when path is None."""
    aid = None
    if path is None:
        hits = host.artifacts(filename="rigor_ledger.json", exact=True)["artifacts"]
        if not hits:
            return None, None
        aid = hits[0]["id"]
        path = host.artifact_path(hits[0]["latest_version_id"])
    with open(path) as fh:
        return json.load(fh), aid


def rigor_log_review(ledger, output, version_id=None, scope="", notes=""):
    rid = f"V-{len(ledger['reviews']) + 1:03d}"
    ledger["reviews"].append({"id": rid, "date": rigor_now(), "output": output, "version_id": version_id,
                              "scope": scope, "notes": notes})
    return rid


def rigor_add_finding(ledger, review_id, category, severity, issue, evidence, pushback, recommendation,
                      literature=None, confidence="verified"):
    """confidence: 'verified' (seen in output/code) or 'question' (cannot be determined from what was available)."""
    assert severity in SEVERITIES, severity
    assert category in CATEGORIES, category
    fid = f"R-{len(ledger['findings']) + 1:03d}"
    ledger["findings"].append({"id": fid, "review": review_id, "date": rigor_now(), "category": category,
                               "severity": severity, "confidence": confidence, "issue": issue,
                               "evidence": evidence, "pushback": pushback, "recommendation": recommendation,
                               "literature": literature or [], "status": "open", "history": []})
    return fid


def rigor_set_status(ledger, fid, status, note, review_id=None):
    assert status in STATUSES, status
    f = [x for x in ledger["findings"] if x["id"] == fid][0]
    f["history"].append({"date": rigor_now(), "from": f["status"], "to": status, "note": note, "review": review_id})
    f["status"] = status
    return f


def rigor_upsert_method(ledger, name, card, search=None):
    """card: dict(problem, approach, components, data, evaluation, claimed_contribution). search: dict(date, tier, sources, queries, n_screened, closest, verdict, coverage)."""
    m = [x for x in ledger["method_cards"] if x["name"] == name]
    if m:
        m = m[0]
        m["card"].update(card)
    else:
        m = {"name": name, "card": card, "searches": [], "verdict": "unchecked"}
        ledger["method_cards"].append(m)
    if search:
        search.setdefault("date", rigor_now())
        m["searches"].append(search)
        m["verdict"] = search.get("verdict", m["verdict"])
    return m


def rigor_cell(s):
    return str(s).replace("|", "/").replace("\n", " ")


def rigor_save(ledger, json_path="rigor_ledger.json", md_path="rigor_ledger.md"):
    ledger["updated"] = rigor_now()
    with open(json_path, "w") as fh:
        json.dump(ledger, fh, indent=1)
    order = {s: i for i, s in enumerate(SEVERITIES)}
    fs = sorted(ledger["findings"], key=lambda f: (f["status"] != "open", order[f["severity"]], f["id"]))
    n_open = {s: sum(1 for f in fs if f["status"] == "open" and f["severity"] == s) for s in SEVERITIES}
    L = [f"# Rigor ledger: {ledger['project']}", "", f"Updated {ledger['updated']}. Open: "
         + ", ".join(f"{v} {k}" for k, v in n_open.items()) + f". Reviews logged: {len(ledger['reviews'])}.", "",
         "## Findings", "", "| ID | Sev | Cat | Status | Issue | Recommendation | Lit |", "|---|---|---|---|---|---|---|"]
    for f in fs:
        lit = "; ".join(x.get("doi") or x.get("url") or x.get("title", "") for x in f["literature"])
        q = " (question)" if f["confidence"] == "question" else ""
        L.append(f"| {f['id']} | {f['severity']} | {f['category']} | {f['status']} | {rigor_cell(f['issue'])}{q} | "
                 f"{rigor_cell(f['recommendation'])} | {rigor_cell(lit)} |")
    L += ["", "## Prior-work checks", "", "| Method | Verdict | Last search | Tier | Screened | Closest |", "|---|---|---|---|---|---|"]
    for m in ledger["method_cards"]:
        s = m["searches"][-1] if m["searches"] else {}
        close = "; ".join(c.get("doi") or c.get("title", "") for c in s.get("closest", [])[:3])
        L.append(f"| {m['name']} | {m['verdict']} | {s.get('date', '-')} | {s.get('tier', '-')} | {s.get('n_screened', '-')} | {rigor_cell(close)} |")
    L += ["", "## Reviews", ""] + [f"- {r['id']} {r['date']}: {r['output']} ({r.get('version_id') or 'no vid'}) {r['scope']}"
                                   for r in ledger["reviews"]]
    with open(md_path, "w") as fh:
        fh.write("\n".join(L) + "\n")
    return json_path, md_path


def rigor_get(url, params, tries=3):
    import requests
    r = None
    for a in range(tries):
        r = requests.get(url, params=params, timeout=40)
        if r.status_code == 200:
            return r
        time.sleep(2 * (a + 1))
    r.raise_for_status()
    return r


def rigor_openalex_rows(data):
    rows = []
    for w in data.get("results", []):
        inv = w.get("abstract_inverted_index") or {}
        pos = sorted((p, t) for t, ps in inv.items() for p in ps)
        rows.append({"source": "openalex", "title": w.get("display_name") or "", "year": w.get("publication_year"),
                     "doi": (w.get("doi") or "").replace("https://doi.org/", "").lower() or None,
                     "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                     "cited_by": w.get("cited_by_count"), "abstract": " ".join(t for _, t in pos),
                     "url": w.get("id"), "openalex_id": (w.get("id") or "").rsplit("/", 1)[-1]})
    return rows


def novelty_search(queries, since_year=None, per_query=15, sources=None):
    """Search OpenAlex (needs OPENALEX_API_KEY: declare the OpenAlex credential on the cell), arXiv, and Europe PMC
    (PubMed + bioRxiv/medRxiv preprints). Rows are ordered by per-query relevance rank (each source's own ranking), so
    head(n) interleaves the best hits of every query and source. arXiv uses AND over the first four words longer than three
    characters, so pass short keyword queries. Returns a deduplicated DataFrame; df.attrs['coverage'] records per-source/query
    counts, skipped sources, and errors for the coverage statement."""
    import pandas as pd
    import xml.etree.ElementTree as ET
    if sources is None:
        sources = ("openalex", "arxiv", "europepmc")
    rows = []
    cov = {"queries": list(queries), "since_year": since_year, "counts": {}, "skipped": {}, "errors": {}}
    key = os.environ.get("OPENALEX_API_KEY")
    for q in queries:
        if "openalex" in sources:
            if not key:
                cov["skipped"]["openalex"] = "OPENALEX_API_KEY not in env (declare the OpenAlex credential on the cell)"
            else:
                try:
                    p = {"search": q, "per-page": per_query, "api_key": key}
                    if since_year:
                        p["filter"] = f"from_publication_date:{since_year}-01-01"
                    rs = rigor_openalex_rows(rigor_get("https://api.openalex.org/works", p).json())
                    rows += [dict(r, rank=i) for i, r in enumerate(rs)]
                    cov["counts"][f"openalex|{q}"] = len(rs)
                except Exception as e:
                    cov["errors"][f"openalex|{q}"] = str(e)[:200]
        if "arxiv" in sources:
            try:
                t = rigor_get("http://export.arxiv.org/api/query", {"search_query": "all:" + " AND all:".join([w for w in q.split() if len(w) > 3][:4]),
                              "max_results": per_query, "sortBy": "relevance"}).text
                ns = {"a": "http://www.w3.org/2005/Atom"}
                ents = ET.fromstring(t).findall("a:entry", ns)
                for i, e in enumerate(ents):
                    yr = int(e.findtext("a:published", "0000", ns)[:4])
                    if since_year and yr < since_year:
                        continue
                    rows.append({"source": "arxiv", "title": " ".join(e.findtext("a:title", "", ns).split()), "year": yr,
                                 "doi": None, "venue": "arXiv", "cited_by": None,
                                 "abstract": " ".join(e.findtext("a:summary", "", ns).split()),
                                 "url": e.findtext("a:id", "", ns), "openalex_id": None, "rank": i})
                cov["counts"][f"arxiv|{q}"] = len(ents)
            except Exception as e:
                cov["errors"][f"arxiv|{q}"] = str(e)[:200]
            time.sleep(3)
        if "europepmc" in sources:
            try:
                qq = q + (f" AND PUB_YEAR:[{since_year} TO 3000]" if since_year else "")
                res = rigor_get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                {"query": qq, "format": "json", "pageSize": per_query, "resultType": "core"}).json()
                rs = res.get("resultList", {}).get("result", [])
                for i, w in enumerate(rs):
                    rows.append({"rank": i, "source": "europepmc:" + w.get("source", ""), "title": w.get("title", ""),
                                 "year": int(w["pubYear"]) if w.get("pubYear") else None,
                                 "doi": (w.get("doi") or "").lower() or None,
                                 "venue": ((w.get("journalInfo") or {}).get("journal") or {}).get("title") or w.get("source"),
                                 "cited_by": w.get("citedByCount"),
                                 "abstract": re.sub("<[^>]+>", "", w.get("abstractText", "")),
                                 "url": f"https://europepmc.org/article/{w.get('source')}/{w.get('id')}", "openalex_id": None})
                cov["counts"][f"europepmc|{q}"] = len(rs)
            except Exception as e:
                cov["errors"][f"europepmc|{q}"] = str(e)[:200]
    df = pd.DataFrame(rows)
    if len(df):
        df["tkey"] = df["title"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True).str[:80]
        df["dkey"] = df["doi"].fillna(df["tkey"])
        df = df.sort_values(["rank", "cited_by"], ascending=[True, False], na_position="last")
        df = df.drop_duplicates("dkey").drop_duplicates("tkey").drop(columns=["tkey", "dkey"]).reset_index(drop=True)
    df.attrs["coverage"] = cov
    return df


def citation_neighbors(openalex_id, n=25):
    """Backward (referenced) and forward (citing) works of one OpenAlex work, for escalation-tier graph expansion."""
    import pandas as pd
    key = os.environ.get("OPENALEX_API_KEY")
    assert key, "declare the OpenAlex credential on this cell"
    w = rigor_get(f"https://api.openalex.org/works/{openalex_id}", {"api_key": key}).json()
    refs = [r.rsplit("/", 1)[-1] for r in w.get("referenced_works", [])][:50]
    rows = []
    if refs:
        rows += [dict(r, relation="referenced_by_seed") for r in rigor_openalex_rows(rigor_get(
            "https://api.openalex.org/works", {"filter": "openalex_id:" + "|".join(refs), "per-page": 50, "api_key": key}).json())]
    rows += [dict(r, relation="cites_seed") for r in rigor_openalex_rows(rigor_get(
        "https://api.openalex.org/works", {"filter": f"cites:{openalex_id}", "per-page": n,
                                           "sort": "cited_by_count:desc", "api_key": key}).json())]
    return pd.DataFrame(rows)


def rate_overlap(method_card, hits, top_n=40, model=None):
    """LLM screen of title+abstract against the method card. Adds columns problem/approach/eval (0-3), overall
    (none|partial|substantial), note. A screen only: escalate partial/substantial hits to full-text reading."""
    if model is None:
        model = host.reasoning_model()
    sub = hits.head(top_n).copy()
    sysm = ("You compare a research method description with one paper's title and abstract and judge prior-work overlap. "
            "Reply with only a JSON object with keys problem, approach, eval (integers 0-3: 0 unrelated, 1 same area, "
            "2 closely similar, 3 essentially the same), overall (none, partial, or substantial), note (one sentence naming "
            "the specific shared or differing element). Judge only from the text given; if the abstract is missing say so in note.")
    card = json.dumps(method_card, indent=1)
    reqs = [{"prompt": f"METHOD CARD:\n{card}\n\nPAPER ({r.year}): {r.title}\nABSTRACT: {(r.abstract or 'missing')[:3000]}",
             "system": sysm, "model": model, "max_tokens": 300} for r in sub.itertuples()]
    outs = host.llm(reqs, max_concurrency=8)
    parsed = []
    for o in outs:
        try:
            parsed.append(json.loads(re.search(r"\{.*\}", o.get("text", ""), re.S).group(0)))
        except Exception:
            parsed.append({"problem": None, "approach": None, "eval": None, "overall": "unparsed", "note": str(o)[:200]})
    for k in ("problem", "approach", "eval", "overall", "note"):
        sub[k] = [p.get(k) for p in parsed]
    rank = {"substantial": 0, "partial": 1, "none": 2, "unparsed": 3}
    sub["rank_"] = sub["overall"].map(rank).fillna(3)
    return sub.sort_values(["rank_", "approach"], ascending=[True, False]).drop(columns="rank_").reset_index(drop=True)
