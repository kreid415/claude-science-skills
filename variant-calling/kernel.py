# variant-calling helpers. Command builders RETURN shell strings; run_cmd() is the only executor.
import os
import shlex

GATK_SNP_HARD_FILTERS = (
    ("QD2", "QD < 2.0"), ("QUAL30", "QUAL < 30.0"), ("SOR3", "SOR > 3.0"), ("FS60", "FS > 60.0"),
    ("MQ40", "MQ < 40.0"), ("MQRankSum-12.5", "MQRankSum < -12.5"), ("ReadPosRankSum-8", "ReadPosRankSum < -8.0"),
)
GATK_INDEL_HARD_FILTERS = (
    ("QD2", "QD < 2.0"), ("QUAL30", "QUAL < 30.0"), ("FS200", "FS > 200.0"), ("ReadPosRankSum-20", "ReadPosRankSum < -20.0"),
)


def run_cmd(cmd, check=True, cwd=None, timeout=None):
    """Run a shell command under bash with pipefail; return dict(cmd, returncode, stdout, stderr).
    Raises RuntimeError with the stderr tail when check=True and the exit code is non-zero. pipefail makes a failing
    first stage of `a | b` fail the call; consequently `| head` ends with exit 141 (SIGPIPE) - use check=False there."""
    import subprocess
    p = subprocess.run(["bash", "-o", "pipefail", "-c", cmd], capture_output=True, text=True, cwd=cwd, timeout=timeout)
    res = {"cmd": cmd, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    if check and p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"command failed (exit {p.returncode}): {cmd}\n--- stderr tail ---\n{tail}")
    return res


def check_bam_inputs(bam, ref, nreads=200000):
    """Pre-flight checks for a BAM/CRAM + reference FASTA. Returns dict(ok, errors, warnings, info).
    Checks: coordinate-sorted, index, read groups (header + reads), .fai/.dict, contig names/lengths/order vs
    reference, duplicate flags (first `nreads` records)."""
    import pysam
    errors, warnings, info = [], [], {}
    is_cram = bam.endswith(".cram")
    fai = ref + ".fai"
    if not os.path.exists(ref):
        return {"ok": False, "errors": [f"reference not found: {ref}"], "warnings": [], "info": {}}
    if not os.path.exists(fai):
        errors.append(f"missing {fai} (samtools faidx)")
    stem = ref.rsplit(".", 1)[0]
    if not (os.path.exists(stem + ".dict") or os.path.exists(ref + ".dict")):
        errors.append(f"missing sequence dictionary {stem}.dict (gatk CreateSequenceDictionary; required by GATK)")
    af = pysam.AlignmentFile(bam, "rc" if is_cram else "rb", reference_filename=ref if is_cram else None)
    hd = af.header.to_dict()
    so = hd.get("HD", {}).get("SO")
    info["sort_order"] = so
    if so != "coordinate":
        errors.append(f"@HD SO is {so!r}, expected 'coordinate' (samtools sort)")
    if not af.has_index():
        errors.append("no index (.bai/.csi/.crai); run samtools index")
    rgs = hd.get("RG", [])
    info["read_groups"] = {r["ID"]: {k: r.get(k) for k in ("SM", "PL", "LB")} for r in rgs}
    if not rgs:
        errors.append("no @RG lines in header (GATK requires RG with SM; add with bwa -R or samtools addreplacerg)")
    for r in rgs:
        if "SM" not in r:
            errors.append(f"@RG {r['ID']} lacks SM")
        if "PL" not in r:
            warnings.append(f"@RG {r['ID']} lacks PL (GATK BQSR/Mutect2 expect it)")
    if os.path.exists(fai):
        ref_len = {l.split("\t")[0]: int(l.split("\t")[1]) for l in open(fai)}
        bam_len = dict(zip(af.references, af.lengths))
        missing = [c for c in bam_len if c not in ref_len]
        badlen = [c for c in bam_len if c in ref_len and ref_len[c] != bam_len[c]]
        extra = [c for c in ref_len if c not in bam_len]
        if missing:
            errors.append(f"{len(missing)} BAM contigs absent from reference (first: {missing[:3]}); wrong build/naming?")
        if badlen:
            errors.append(f"contig length mismatch for {badlen[:3]}")
        if [c for c in bam_len if c in ref_len] != [c for c in ref_len if c in bam_len]:
            errors.append("common contigs are in a different order in BAM vs reference (GATK requires same order)")
        if extra:
            warnings.append(f"{len(extra)} reference contigs not in BAM header (first: {extra[:3]}); fine for bcftools, GATK needs a compatible dictionary")
        info["n_contigs_bam"], info["n_contigs_ref"] = len(bam_len), len(ref_len)
    n = ndup = nmiss_rg = nunmapped = 0
    rg_ids = {r["ID"] for r in rgs}
    for read in af.fetch(until_eof=True):  # works without an index
        n += 1
        ndup += read.is_duplicate
        nunmapped += read.is_unmapped
        tag = read.get_tag("RG") if read.has_tag("RG") else None
        if tag not in rg_ids:
            nmiss_rg += 1
        if n >= nreads:
            break
    info.update(reads_scanned=n, duplicate_flagged=ndup, unmapped=nunmapped, reads_without_valid_RG=nmiss_rg)
    if nmiss_rg:
        errors.append(f"{nmiss_rg}/{n} scanned reads lack an RG tag that matches the header")
    pg = " ".join(p.get("ID", "") + " " + p.get("PN", "") + " " + p.get("CL", "") for p in hd.get("PG", [])).lower()
    info["markdup_in_PG"] = "markdup" in pg or "markduplicates" in pg
    if ndup == 0 and not info["markdup_in_PG"]:
        warnings.append("no duplicate-flagged reads and no markdup PG record: run samtools markdup / GATK MarkDuplicates (skip for amplicon data)")
    af.close()
    return {"ok": not errors, "errors": errors, "warnings": warnings, "info": info}


def bcftools_call_cmd(bams, ref, out_vcf, regions=None, min_mq=20, min_bq=20, max_depth=250, ploidy=None,
                      annotate="FORMAT/AD,FORMAT/DP", call_annotate="GQ", variants_only=True, threads=2,
                      bam_list=None, ploidy_file=None, samples_file=None):
    """Return the shell string `bcftools mpileup | bcftools call -m` (multisample, gzipped VCF + .tbi index).
    bams: list of paths (ignored if bam_list file given). ploidy: built-in assembly ('GRCh38'/'GRCh37') or ploidy_file; both need samples_file for sample sex."""
    mp = ["bcftools", "mpileup", "-Ou", "-f", shlex.quote(ref), "-a", annotate, "-q", str(min_mq), "-Q", str(min_bq),
          "-d", str(max_depth), "--threads", str(threads)]
    if regions:
        mp += ["-r", shlex.quote(regions)]
    mp += ["-b", shlex.quote(bam_list)] if bam_list else [shlex.quote(b) for b in bams]
    call = ["bcftools", "call", "-m", "-Oz", "-o", shlex.quote(out_vcf), "--write-index=tbi", "--threads", str(threads)]
    if call_annotate:
        call += ["-a", call_annotate]  # GQ is not emitted by default
    if variants_only:
        call.append("-v")
    if ploidy:
        call += ["--ploidy", shlex.quote(ploidy)]
    if ploidy_file:  # CHROM FROM TO SEX PLOIDY; pair with samples_file (sample<TAB>M|F) for sex-aware X/Y
        call += ["--ploidy-file", shlex.quote(ploidy_file)]
    if samples_file:
        call += ["-S", shlex.quote(samples_file)]
    return " ".join(mp) + " | " + " ".join(call)


def haplotypecaller_gvcf_cmds(samples, ref, outdir, intervals, java_mem="4g", combine="genomicsdb", cohort="cohort",
                              dbsnp=None, extra_hc=""):
    """Return an ordered list of shell strings: per-sample HaplotypeCaller -ERC GVCF, then joint genotyping.
    samples: {sample_name: bam}. intervals: list like ['chr1','chr2'] (required: GenomicsDBImport errors without -L).
    combine: 'genomicsdb' (GenomicsDBImport -> GenotypeGVCFs gendb://) or 'combinegvcfs' (small cohorts).
    Final joint VCF: {outdir}/{cohort}.vcf.gz. The GenomicsDB workspace dir must not already exist."""
    if combine not in ("genomicsdb", "combinegvcfs"):
        raise ValueError("combine must be 'genomicsdb' or 'combinegvcfs'")
    if not intervals:
        raise ValueError("intervals is required (list of contigs/regions)")
    q, g = shlex.quote, f"gatk --java-options {shlex.quote('-Xmx' + java_mem)}"
    L = " ".join(f"-L {q(i)}" for i in intervals)
    cmds, gvcfs = [], {}
    for s, bam in samples.items():
        gvcfs[s] = f"{outdir}/{s}.g.vcf.gz"
        cmds.append(f"{g} HaplotypeCaller -R {q(ref)} -I {q(bam)} -O {q(gvcfs[s])} -ERC GVCF {L} {extra_hc}".strip())
    out = f"{outdir}/{cohort}.vcf.gz"
    ds = f"--dbsnp {q(dbsnp)} " if dbsnp else ""
    if combine == "genomicsdb":
        db = f"{outdir}/{cohort}_genomicsdb"
        cmds.append(f"{g} GenomicsDBImport -R {q(ref)} --genomicsdb-workspace-path {q(db)} {L} " + " ".join(f"-V {q(v)}" for v in gvcfs.values()))
        cmds.append(f"{g} GenotypeGVCFs -R {q(ref)} -V {q('gendb://' + db)} {ds}-O {q(out)}")
    else:
        comb = f"{outdir}/{cohort}.g.vcf.gz"
        cmds.append(f"{g} CombineGVCFs -R {q(ref)} " + " ".join(f"-V {q(v)}" for v in gvcfs.values()) + f" -O {q(comb)}")
        cmds.append(f"{g} GenotypeGVCFs -R {q(ref)} -V {q(comb)} {ds}-O {q(out)}")
    return cmds


def gatk_hard_filter_cmds(vcf, ref, out_prefix, java_mem="4g"):
    """Return shell strings: split SNP/INDEL (SelectVariants), VariantFiltration with the GATK generic hard-filter
    thresholds (see SKILL.md; lenient starting points, not tuned), MergeVcfs -> {out_prefix}.filtered.vcf.gz.
    Records failing a filter keep FILTER=<names>; PASS records are the retained set."""
    q, g = shlex.quote, f"gatk --java-options {shlex.quote('-Xmx' + java_mem)}"
    def vf(src, dst, filters):
        fl = " ".join(f"--filter-expression {q(e)} --filter-name {q(n)}" for n, e in filters)
        return f"{g} VariantFiltration -R {q(ref)} -V {q(src)} {fl} -O {q(dst)}"
    cmds = []
    for typ, filters in (("SNP", GATK_SNP_HARD_FILTERS), ("INDEL", GATK_INDEL_HARD_FILTERS)):
        raw, flt = f"{out_prefix}.{typ.lower()}.vcf.gz", f"{out_prefix}.{typ.lower()}.flt.vcf.gz"
        cmds.append(f"{g} SelectVariants -R {q(ref)} -V {q(vcf)} --select-type-to-include {typ} -O {q(raw)}")
        cmds.append(vf(raw, flt, filters))
    cmds.append(f"{g} MergeVcfs -I {q(out_prefix + '.snp.flt.vcf.gz')} -I {q(out_prefix + '.indel.flt.vcf.gz')} -O {q(out_prefix + '.filtered.vcf.gz')}")
    return cmds


def vcf_qc_summary(vcf, region=None, pass_only=True, x_region=None):
    """Per-sample QC DataFrame from a (multi)sample VCF/BCF via cyvcf2: n_het, n_hom_alt (SNP+indel), n_het_snp, n_hom_alt_snp, het_hom_ratio_snp, n_snp,
    n_indel, ti, tv, titv (biallelic SNPs where the sample carries the alt allele), missing_rate (over records),
    mean_dp, mean_gq. x_region (e.g. 'chrX:2781480-155701381' = GRCh38 non-PAR) adds x_n_snps and x_het_frac
    (het / alt-carrying biallelic SNPs; near 0 for haploid male X). pass_only keeps FILTER in (PASS, .)."""
    import numpy as np
    import pandas as pd
    from cyvcf2 import VCF
    TI = {("A", "G"), ("G", "A"), ("C", "T"), ("T", "C")}
    names = list(VCF(vcf).samples)
    k = len(names)
    c = {key: np.zeros(k) for key in ("het", "hom", "hetsnp", "homsnp", "snp", "indel", "ti", "tv", "miss", "dp", "dpn", "gq", "gqn", "xhet", "xalt")}
    n_rec = 0

    def records(reg):
        for v in (VCF(vcf)(reg) if reg else VCF(vcf)):
            if not pass_only or v.FILTER in (None, "PASS"):
                yield v

    for v in records(region):
        gt = v.gt_types  # 0 HOM_REF, 1 HET, 2 UNKNOWN, 3 HOM_ALT
        alt = (gt == 1) | (gt == 3)
        n_rec += 1
        c["miss"] += gt == 2
        c["het"] += gt == 1
        c["hom"] += gt == 3
        if v.is_snp and len(v.ALT) == 1:
            c["snp"] += alt
            c["hetsnp"] += gt == 1
            c["homsnp"] += gt == 3
            c["ti" if (v.REF, v.ALT[0]) in TI else "tv"] += alt
        elif v.is_indel:
            c["indel"] += alt
        for tag, s_key, n_key in (("DP", "dp", "dpn"), ("GQ", "gq", "gqn")):
            if v.FORMAT and tag in v.FORMAT:
                a = np.asarray(v.format(tag), dtype=float).reshape(k, -1)[:, 0]
                ok = (a >= 0) & (gt != 2)
                c[n_key] += ok
                c[s_key] += np.where(ok, a, 0)
    df = pd.DataFrame({"sample": names, "n_records": n_rec, "n_het": c["het"].astype(int), "n_hom_alt": c["hom"].astype(int),
                       "n_snp": c["snp"].astype(int), "n_indel": c["indel"].astype(int),
                       "ti": c["ti"].astype(int), "tv": c["tv"].astype(int)})
    df["n_het_snp"], df["n_hom_alt_snp"] = c["hetsnp"].astype(int), c["homsnp"].astype(int)
    df["het_hom_ratio_snp"] = df.n_het_snp / df.n_hom_alt_snp.replace(0, np.nan)  # SNP-only, as in bcftools stats PSC
    df["titv"] = df.ti / df.tv.replace(0, np.nan)
    df["missing_rate"] = c["miss"] / max(n_rec, 1)
    df["mean_dp"] = c["dp"] / np.where(c["dpn"] > 0, c["dpn"], np.nan)  # NaN if tag absent
    df["mean_gq"] = c["gq"] / np.where(c["gqn"] > 0, c["gqn"], np.nan)
    if x_region:
        for v in records(x_region):
            if v.is_snp and len(v.ALT) == 1:
                gt = v.gt_types
                c["xhet"] += gt == 1
                c["xalt"] += (gt == 1) | (gt == 3)
        df["x_n_snps"] = c["xalt"].astype(int)
        df["x_het_frac"] = c["xhet"] / np.maximum(c["xalt"], 1)
    return df


def bcftools_stats_psc(vcf, region=None):
    """Cross-check: run `bcftools stats -s -` and return the PSC (per-sample counts) table as a DataFrame.
    Columns follow the PSC header (nRefHom, nNonRefHom, nHets are SNP-only; nIndels, nTransitions, ... see manual)."""
    import io
    import pandas as pd
    out = run_cmd(f"bcftools stats -s - {'-r ' + shlex.quote(region) if region else ''} {shlex.quote(vcf)}")["stdout"].splitlines()
    hdr = [l for l in out if l.startswith("# PSC\t")][0].split("\t")[1:]
    cols = [h.split("]")[-1] for h in hdr]
    return pd.read_csv(io.StringIO("\n".join(l for l in out if l.startswith("PSC\t"))), sep="\t", header=None, names=["PSC"] + cols).drop(columns="PSC")
