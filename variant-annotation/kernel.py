import re
import time

ENSEMBL_REST = "https://rest.ensembl.org"
ENSEMBL_REST_GRCH37 = "https://grch37.rest.ensembl.org"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
GNOMAD_API = "https://gnomad.broadinstitute.org/api"
GNOMAD_DATASETS = {"GRCh38": "gnomad_r4", "GRCh37": "gnomad_r2_1"}
# Table 1 of Bergquist et al. 2025 Genet Med 27(6):101402: (min score, ACMG points); +=PP3, -=BP4
PP3_BP4_CUTS = {
    "REVEL": [(0, -4), (0.017, -3), (0.053, -2), (0.184, -1), (0.291, 0), (0.644, 1), (0.773, 2), (0.879, 3), (0.932, 4)],
    "AlphaMissense": [(0, -3), (0.071, -2), (0.100, -1), (0.170, 0), (0.792, 1), (0.906, 2), (0.972, 3), (0.990, 4)],
}
VEP_DEFAULT_PARAMS = {"canonical": 1, "hgvs": 1, "vcf_string": 1, "variant_class": 1, "AlphaMissense": 1, "REVEL": 1}

def api_call(method, url, retries=6, wait=10, timeout=600, **kw):
    """requests call with retry on 429/5xx; honours Retry-After. Returns Response."""
    import requests
    last = None
    for i in range(retries):
        try:
            r = requests.request(method, url, timeout=timeout, **kw)
        except requests.exceptions.RequestException as e:
            last = str(e)[:200]; time.sleep(wait * (i + 1)); continue
        if r.status_code == 429 or r.status_code >= 500:
            last = "HTTP %s" % r.status_code
            time.sleep(float(r.headers.get("Retry-After", wait * (i + 1)))); continue
        return r
    raise RuntimeError("api_call failed after %d tries: %s %s (%s)" % (retries, method, url, last))

def parse_variant(s):
    """'17-43057062-T-TG' | 'chr17:43057062:T:TG' | '17 43057062 T TG' -> (chrom,int pos,ref,alt) or None."""
    m = re.match(r"^(?:chr)?([0-9]{1,2}|X|Y|MT?)[-:\s_]+(\d+)[-:\s_]+([ACGTN]+)[-:\s_>]+([ACGTN]+)$", str(s).strip(), re.I)
    if not m:
        return None
    c = m.group(1).upper()
    return ("MT" if c == "M" else c, int(m.group(2)), m.group(3).upper(), m.group(4).upper())

def ensembl_base(assembly):
    return ENSEMBL_REST_GRCH37 if assembly == "GRCh37" else ENSEMBL_REST

def left_align(chrom, pos, ref, alt, assembly="GRCh38"):
    """Trim + left-align one biallelic allele using Ensembl /sequence/region (no FASTA needed).
    Returns (chrom,pos,ref,alt,ref_matches_genome). For VCF files use bcftools norm -f instead."""
    hdr = {"Accept": "application/json"}
    base = ensembl_base(assembly)
    def seq(a, b):
        r = api_call("GET", "%s/sequence/region/human/%s:%d..%d:1" % (base, chrom, a, b), headers=hdr,
                     params={"coord_system_version": assembly})
        r.raise_for_status(); return r.json()["seq"].upper()
    ref_ok = seq(pos, pos + len(ref) - 1) == ref
    win, wstart = "", pos
    while True:
        while ref and alt and ref[-1] == alt[-1]:
            ref, alt = ref[:-1], alt[:-1]
        if ref and alt:
            break
        if pos - 1 < wstart - len(win) or not win:   # need more upstream sequence
            a = max(1, pos - 100); win = seq(a, pos - 1) + win if win else seq(a, pos - 1); wstart = pos
        b = win[-(wstart - pos + 1)]
        ref, alt, pos = b + ref, b + alt, pos - 1
    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt, pos = ref[1:], alt[1:], pos + 1
    return chrom, pos, ref, alt, ref_ok

def normalize_variant_ids(ids, assembly="GRCh38", left_normalize=True):
    """chr-pos-ref-alt / rsID / HGVS (g., c. with NM_/ENST accession) -> DataFrame of chr-pos-ref-alt keys.
    One row per input allele; unresolved inputs keep a row with note='unresolved'."""
    import pandas as pd
    base = ensembl_base(assembly) + "/variant_recoder/homo_sapiens"
    rows, rs, hg = [], [], []
    for x in ids:
        v = parse_variant(x)
        if v:
            rows.append(dict(input=x, kind="vcf", chrom=v[0], pos=v[1], ref=v[2], alt=v[3]))
        elif re.match(r"^rs\d+$", str(x).strip(), re.I):
            rs.append(str(x).strip())
        else:
            hg.append(str(x).strip())
    def recode(chunk):
        r = api_call("POST", base, headers={"Content-Type": "application/json", "Accept": "application/json"},
                     json={"ids": chunk}, params={"fields": "hgvsg", "vcf_string": 1})
        if r.status_code == 400 and len(chunk) > 1:     # one unparsable id fails the whole batch: retry singly
            return [x for c in chunk for x in recode([c])]
        return r.json() if r.ok else []
    for kind, group in (("rsid", rs), ("hgvs", hg)):   # never mix kinds in one recoder request
        for i in range(0, len(group), 200):
            chunk, seen = group[i:i + 200], set()
            for rec in recode(chunk):
                for d in rec.values():
                    p = next((parse_variant(vs) for vs in d.get("vcf_string", []) if parse_variant(vs)), None) if isinstance(d, dict) else None
                    if p:
                        seen.add(d.get("input")); rows.append(dict(input=d.get("input"), kind=kind, chrom=p[0], pos=p[1], ref=p[2], alt=p[3]))
            rows += [dict(input=c, kind=kind, note="unresolved") for c in chunk if c not in seen]
    out = []
    for r_ in rows:
        if left_normalize and "chrom" in r_ and r_.get("note") is None:
            c, p, rf, al, ok = left_align(r_["chrom"], r_["pos"], r_["ref"], r_["alt"], assembly)
            r_.update(chrom=c, pos=p, ref=rf, alt=al, note=None if ok else "ref allele does not match %s" % assembly)
        if "chrom" in r_:
            r_["variant_id"] = "%s-%d-%s-%s" % (r_["chrom"], r_["pos"], r_["ref"], r_["alt"])
        out.append(r_)
    df = pd.DataFrame(out)
    df.attrs["provenance"] = dict(endpoint=base, assembly=assembly, timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return df

def vep_annotate(variants, assembly="GRCh38", params=None, batch=200):
    """Ensembl VEP REST (POST vep/homo_sapiens/region). variants: 'chr-pos-ref-alt' strings. One row per variant
    (MANE Select transcript if flagged, else canonical, else first)."""
    import pandas as pd
    base = ensembl_base(assembly)
    p = dict(VEP_DEFAULT_PARAMS); p.update(params or {})
    if assembly == "GRCh38":
        p.setdefault("mane", 1)
    rows = []
    for i in range(0, len(variants), batch):
        chunk = variants[i:i + batch]
        regions = ["%s %d . %s %s . . ." % parse_variant(v) for v in chunk]
        r = api_call("POST", base + "/vep/homo_sapiens/region", headers={"Content-Type": "application/json", "Accept": "application/json"},
                     json={"variants": regions}, params=p)
        r.raise_for_status()
        byin = {x["input"]: x for x in r.json()}
        for v, reg in zip(chunk, regions):
            x = byin.get(reg)
            if x is None:
                rows.append(dict(variant_id=v, note="no VEP result")); continue
            tcs = x.get("transcript_consequences", [])
            tc = next((t for t in tcs if t.get("mane_select")), None) or next((t for t in tcs if t.get("canonical")), None) or (tcs[0] if tcs else {})
            am = tc.get("alphamissense") or {}
            rows.append(dict(variant_id=v, most_severe_consequence=x.get("most_severe_consequence"), variant_class=x.get("variant_class"),
                gene_symbol=tc.get("gene_symbol"), transcript_id=tc.get("transcript_id"), mane_select=tc.get("mane_select"),
                consequence=",".join(tc.get("consequence_terms", [])), impact=tc.get("impact"), hgvsc=tc.get("hgvsc"), hgvsp=tc.get("hgvsp"),
                amino_acids=tc.get("amino_acids"), am_pathogenicity=am.get("am_pathogenicity"), am_class={"pathogenic": "likely_pathogenic", "benign": "likely_benign"}.get(am.get("am_class"), am.get("am_class")), revel=tc.get("revel"),
                colocated_rsids=[c["id"] for c in x.get("colocated_variants", []) if str(c.get("id", "")).startswith("rs")], n_transcripts=len(tcs)))
    df = pd.DataFrame(rows)
    hdr = {"Accept": "application/json"}
    rel = api_call("GET", base + "/info/data", headers=hdr).json().get("releases")
    ver = api_call("GET", base + "/info/rest", headers=hdr).json().get("release")
    df.attrs["provenance"] = dict(endpoint=base + "/vep/homo_sapiens/region", assembly=assembly, ensembl_release=rel, rest_version=ver,
                                  params=p, timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return df

def clinvar_lookup(rsids=None, variants=None, api_key=None):
    """ClinVar via E-utilities (db=clinvar, esearch + esummary v2.0 JSON). rsids: ['rs80357906']; variants: SNV
    'chr-pos-ref-alt' strings (matched on position + canonical SPDI alleles). Rate: 3 req/s, 10/s with api_key."""
    import pandas as pd
    gap = 0.11 if api_key else 0.4
    key = {"api_key": api_key} if api_key else {}
    def eutil(name, **q):
        time.sleep(gap)
        r = api_call("GET", EUTILS_BASE + name + ".fcgi", params=dict(db="clinvar", retmode="json", **key, **q), timeout=60)
        r.raise_for_status(); return r.json()
    def summarize(uids):
        res = eutil("esummary", id=",".join(uids), version="2.0")["result"]
        return [res[u] for u in res["uids"]]
    queries = [("rsid", x, "%s[RSID]" % x) for x in (rsids or [])]
    for v in variants or []:
        c, pos, ref, alt = parse_variant(v)
        queries.append(("variant", v, "%s[chr] AND %d[chrpos38]" % (c, pos)))
    rows = []
    for kind, q, term in queries:
        ids = eutil("esearch", term=term, retmax=100)["esearchresult"]["idlist"]
        for rec in (summarize(ids) if ids else []):
            vs = rec["variation_set"][0]
            dbsnp = [x["db_id"] for x in vs["variation_xrefs"] if x["db_source"] == "dbSNP"]
            spdi = vs.get("canonical_spdi", "")
            if kind == "rsid" and q.lower().lstrip("rs") not in dbsnp:
                continue          # esearch is text-based: drop records that only mention the rsID
            if kind == "variant":
                c, pos, ref, alt = parse_variant(q)
                if not (spdi.endswith(":%s:%s" % (ref, alt)) and any(int(l["start"]) == pos for l in vs["variation_loc"])):
                    continue
            g = rec["germline_classification"]
            rows.append(dict(query=q, variation_id=rec["uid"], accession=rec["accession"], title=rec["title"], obj_type=rec["obj_type"],
                classification=g["description"], review_status=g["review_status"], last_evaluated=g["last_evaluated"][:10],
                conditions="; ".join(t["trait_name"] for t in g["trait_set"]), gene=(rec.get("genes") or [{}])[0].get("symbol"),
                canonical_spdi=spdi, dbsnp=",".join("rs" + d for d in dbsnp)))
    df = pd.DataFrame(rows)
    info = eutil("einfo", version="2.0")["einforesult"]["dbinfo"]
    info = info[0] if isinstance(info, list) else info
    df.attrs["provenance"] = dict(endpoint=EUTILS_BASE + "esearch/esummary db=clinvar", clinvar_build=info.get("dbbuild"),
                                  clinvar_lastupdate=info.get("lastupdate"), timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return df

def gnomad_frequencies(variant_ids, assembly="GRCh38", dataset=None, min_interval=2.0):
    """gnomAD GraphQL variant() query; variant_ids 'chr-pos-ref-alt' in the dataset's build (r4=GRCh38, r2_1=GRCh37).
    Variants absent from gnomAD get status='absent' (not an error). The API returns 429 on bursts: spaced + retried."""
    import pandas as pd
    ds = dataset or GNOMAD_DATASETS[assembly]
    pop = "{ac an homozygote_count hemizygote_count faf95{popmax popmax_population}}"
    q = "query($id:String!,$ds:DatasetId!){variant(variantId:$id,dataset:$ds){variant_id rsids exome%s genome%s %s}}" % (
        pop, pop, "joint%s" % pop if ds == "gnomad_r4" else "")
    rows = []
    for i, vid in enumerate(variant_ids):
        if i:
            time.sleep(min_interval)
        r = api_call("POST", GNOMAD_API, json={"query": q, "variables": {"id": vid, "ds": ds}}, wait=15, timeout=120)
        j = r.json(); v = (j.get("data") or {}).get("variant")
        if not v:
            msg = "; ".join(e["message"] for e in j.get("errors", []))
            rows.append(dict(variant_id=vid, dataset=ds, status="absent" if "not found" in msg.lower() else "error: " + msg)); continue
        parts = [v[k] for k in ("exome", "genome") if v.get(k)]
        t = v.get("joint") or {"ac": sum(x["ac"] for x in parts), "an": sum(x["an"] for x in parts),
                               "homozygote_count": sum(x["homozygote_count"] for x in parts), "faf95": None}
        fa = (v.get("joint") or {}).get("faf95") or max((x["faf95"] for x in parts if x.get("faf95")), key=lambda f: f["popmax"] or 0, default={}) or {}
        rows.append(dict(variant_id=vid, dataset=ds, status="found", rsids=v.get("rsids"), ac=t["ac"], an=t["an"], af=t["ac"] / t["an"] if t["an"] else None,
                         hom=t["homozygote_count"], faf95_popmax=fa.get("popmax"), faf95_popmax_pop=fa.get("popmax_population"),
                         exome_ac=(v.get("exome") or {}).get("ac"), genome_ac=(v.get("genome") or {}).get("ac")))
    df = pd.DataFrame(rows)
    df.attrs["provenance"] = dict(endpoint=GNOMAD_API, dataset=ds, assembly=assembly, timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return df

def pp3_bp4_points(tool, score):
    """Bergquist 2025 / Pejaver 2022 calibrated ACMG points for REVEL or AlphaMissense (+ = PP3, - = BP4, 0 = indeterminate)."""
    if score is None or score != score:
        return None
    pts = None
    for lo, p in PP3_BP4_CUTS[tool]:
        if round(float(score), 3) >= lo:
            pts = p
    return pts

def acmg_evidence_hints(row):
    """Mechanical, research-only hints from one annotate_variants() row. NOT a classification."""
    h = []
    fa, af = row.get("faf95_popmax"), row.get("af")
    if row.get("gnomad_status") == "absent":
        h.append("PM2_Supporting: absent from gnomAD (ClinGen SVI 2020 strength)")
    elif af is not None and af == af and af > 0.05:
        h.append("BA1: gnomAD AF %.3g > 5%% (Richards 2015; check gene-specific VCEP threshold)" % af)
    names = {1: "Supporting", 2: "Moderate", 3: "Strong(3pt)", 4: "Strong"}
    for tool, col in (("REVEL", "revel"), ("AlphaMissense", "am_pathogenicity")):
        p = pp3_bp4_points(tool, row.get(col))
        if p:
            h.append("%s_%s via %s=%s" % ("PP3" if p > 0 else "BP4", names[abs(p)], tool, row.get(col)))
    return h

def annotate_variants(variants, assembly="GRCh38", gnomad=True, clinvar=True, ncbi_api_key=None):
    """normalize -> VEP -> ClinVar (by VEP rsID, else SNV coordinates) -> gnomAD; merged DataFrame.
    df.attrs['provenance'] = {source: provenance dict}."""
    import pandas as pd
    norm = normalize_variant_ids(variants, assembly)
    ok = norm[norm["variant_id"].notna()].drop_duplicates("variant_id")
    df = vep_annotate(list(ok["variant_id"]), assembly)
    prov = {"normalize": norm.attrs["provenance"], "vep": df.attrs["provenance"]}
    df = norm[["input", "variant_id", "note"]].merge(df, on="variant_id", how="left", suffixes=("", "_vep"))
    if clinvar:
        cv = []
        for vid, rs in df.drop_duplicates("variant_id")[["variant_id", "colocated_rsids"]].itertuples(index=False):
            rs = rs if isinstance(rs, list) else []
            _, _, rf, al = parse_variant(vid)
            got = clinvar_lookup(rsids=rs[:3], api_key=ncbi_api_key) if rs else None
            if (got is None or got.empty) and len(rf) == 1 and len(al) == 1:
                got = clinvar_lookup(variants=[vid], api_key=ncbi_api_key)   # SNV coordinate fallback
            if got is None:
                continue
            if not got.empty:
                cv.append(dict(got.iloc[0].drop("query"), variant_id=vid)); prov["clinvar"] = got.attrs["provenance"]
        if cv:
            df = df.merge(pd.DataFrame(cv).rename(columns=lambda c: c if c == "variant_id" else "clinvar_" + c), on="variant_id", how="left")
    if gnomad:
        gn = gnomad_frequencies(list(ok["variant_id"]), assembly)
        prov["gnomad"] = gn.attrs["provenance"]
        df = df.merge(gn.drop(columns=["dataset"]).rename(columns={"status": "gnomad_status", "rsids": "gnomad_rsids"}), on="variant_id", how="left")
    df["acmg_hints"] = [acmg_evidence_hints(r) for r in df.to_dict("records")]
    df.attrs["provenance"] = prov
    return df
