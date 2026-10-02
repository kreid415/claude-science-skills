"""gwas-analysis helpers: plink2 QC/glm/clump + regenie command builders, sumstats loading, lambda_GC, plots."""
import os


def run_cmd(cmd, cwd=None, env=None, check=True, timeout=None):
    """Run a shell command string (bash). Raises RuntimeError with the output tail on non-zero exit."""
    import subprocess
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(cmd, shell=True, cwd=cwd, env=e, capture_output=True, text=True,
                       timeout=timeout, executable="/bin/bash")
    if check and r.returncode != 0:
        raise RuntimeError("exit %d: %s\n%s" % (r.returncode, cmd, (r.stdout + r.stderr)[-1500:]))
    return r


def plink2_qc_cmds(src, out, src_type="vcf", sex_file=None, pheno=None, cc_col=None, mind=0.02, geno=0.02,
                   maf=0.01, hwe=1e-6, king=0.0884, ld="500kb 0.2", npcs=10, pca_approx=False,
                   check_sex=True, plink2="plink2", threads=4):
    """Ordered dict name->plink2 command. Run in order; call sample_outliers(out) after 'king' (run_qc does).
    src_type: vcf|bed|pgen (prefix for bed/pgen). cc_col: binary phenotype column in `pheno` (1=ctrl,2=case)
    -> HWE is computed in controls only. Files: {out}.raw -> .qc1 -> .final, .prune.prune.in, .pca.eigenvec."""
    p = "%s --threads %d" % (plink2, threads)
    imp = {"vcf": "--vcf %s --double-id" % src, "bed": "--bfile %s" % src, "pgen": "--pfile %s" % src}[src_type]
    c = {}
    c["import"] = ("%s %s --max-alleles 2 --set-all-var-ids '@:#:$r:$a' --rm-dup exclude-mismatch %s"
                   "--split-par b38 --make-pgen --out %s.raw") % (
        p, imp, ("--update-sex %s col-num=3 " % sex_file) if sex_file else "", out)
    flt = "--mind %g --geno %g --maf %g" % (mind, geno, maf)
    if cc_col:
        c["hwe_controls"] = ("%s --pfile %s.raw --pheno %s --pheno-name %s --keep-if %s == control "
                             "--hwe %g midp --write-snplist --out %s.hwe") % (p, out, pheno, cc_col, cc_col, hwe, out)
        c["filter"] = "%s --pfile %s.raw --extract %s.hwe.snplist %s --make-pgen --out %s.qc1" % (p, out, out, flt, out)
    else:
        c["filter"] = "%s --pfile %s.raw %s --hwe %g midp --make-pgen --out %s.qc1" % (p, out, flt, hwe, out)
    c["prune"] = "%s --pfile %s.qc1 --indep-pairwise %s --out %s.prune" % (p, out, ld, out)
    ex = "--pfile %s.qc1 --extract %s.prune.prune.in" % (out, out)
    c["het"] = "%s %s --het --out %s.het" % (p, ex, out)
    if check_sex:
        c["sexcheck"] = "%s %s --check-sex max-female-xf=0.2 min-male-xf=0.8 --out %s.sex" % (p, ex, out)
    c["king"] = "%s %s --king-cutoff %g --out %s.king" % (p, ex, king, out)
    c["final"] = "%s --pfile %s.qc1 --remove %s.remove.txt --make-pgen --out %s.final" % (p, out, out, out)
    c["pca"] = "%s --pfile %s.final --extract %s.prune.prune.in --pca %d%s --out %s.pca" % (
        p, out, out, npcs, " approx" if pca_approx else "", out)
    return c


def sample_outliers(out, het_nsd=3.0):
    """Union of KING-cutoff exclusions, |F|>het_nsd SD heterozygosity outliers and hard sex mismatches
    (PEDSEX and SNPSEX both called and different; ambiguous SNPSEX=NA is only reported). Writes {out}.remove.txt."""
    import pandas as pd
    rm, info = {}, {}
    kf = out + ".king.king.cutoff.out.id"
    if os.path.exists(kf):
        k = pd.read_csv(kf, sep="\t")
        k.columns = ["FID", "IID"]
        rm.update({(a, b): "king" for a, b in zip(k.FID, k.IID)})
    h = pd.read_csv(out + ".het.het", sep="\t").rename(columns={"#FID": "FID"})
    bad = h[(h.F - h.F.mean()).abs() > het_nsd * h.F.std()]
    rm.update({(a, b): "het" for a, b in zip(bad.FID, bad.IID)})
    sf = out + ".sex.sexcheck"
    if os.path.exists(sf):
        s = pd.read_csv(sf, sep="\t").rename(columns={"#FID": "FID"})
        hard = s[(s.STATUS == "PROBLEM") & s.SNPSEX.isin([1, 2]) & (s.SNPSEX != s.PEDSEX)]
        rm.update({(a, b): "sex" for a, b in zip(hard.FID, hard.IID)})
        info["sex_ambiguous"] = int(((s.STATUS == "PROBLEM") & ~s.SNPSEX.isin([1, 2])).sum())
    with open(out + ".remove.txt", "w") as fh:
        fh.writelines("%s\t%s\n" % k for k in rm)
    info.update({r: sum(v == r for v in rm.values()) for r in ("king", "het", "sex")})
    info["total_removed"] = len(rm)
    return info


def run_qc(cmds, out, cwd=None):
    """Run plink2_qc_cmds in order, calling sample_outliers before 'final'. Returns outlier summary."""
    info = {}
    for name, cmd in cmds.items():
        if name == "final":
            info = sample_outliers(out)
        run_cmd(cmd, cwd=cwd)
    return info


def make_covar_file(pheno_path, eigenvec_path, out_path, npcs=10):
    """Merge a FID/IID phenotype+covariate table with the first npcs PCs (.eigenvec) -> out_path (tab, FID IID header)."""
    import pandas as pd
    ph = pd.read_csv(pheno_path, sep=None, engine="python")
    ev = pd.read_csv(eigenvec_path, sep="\t").rename(columns={"#FID": "FID"})
    ev = ev[["FID", "IID"] + ["PC%d" % i for i in range(1, npcs + 1)]]
    m = ph.merge(ev, on=["FID", "IID"], how="left")
    m.to_csv(out_path, sep="\t", index=False, na_rep="NA")
    return m


def plink2_glm_cmd(pfile, pheno_file, pheno, covar_file, covars, out, binary=False, mac=20, no_x_sex=True,
                   plink2="plink2", threads=4):
    """--glm with variance-standardized covariates; omit-ref makes A1 = ALT (comparable to regenie ALLELE1).
    covars e.g. 'SEX AGE PC1-PC10'. no_x_sex=True is REQUIRED when SEX is also in covars and chrX is present."""
    mods = "hide-covar omit-ref cols=+a1freq" + (" no-x-sex" if no_x_sex else "") + (" firth-fallback" if binary else "")
    return ("%s --threads %d --pfile %s --pheno %s --pheno-name %s --covar %s --covar-name %s "
            "--covar-variance-standardize --mac %d --glm %s --out %s") % (
        plink2, threads, pfile, pheno_file, pheno, covar_file, covars, mac, mods, out)


def plink2_clump_cmd(pfile, glm_file, out, p1=5e-8, p2=1e-4, r2=0.1, kb=1000, plink2="plink2"):
    """--clump of a plink2 .glm file (ADD rows only by default). LD is taken from `pfile`."""
    return ("%s --pfile %s --clump %s --clump-id-field ID --clump-p-field P --clump-a1-field A1 "
            "--clump-p1 %g --clump-p2 %g --clump-r2 %g --clump-kb %d --out %s") % (
        plink2, pfile, glm_file, p1, p2, r2, kb, out)


def regenie_cmds(pfile, pheno_file, pheno, covar_file, covar_cols, out, binary=False, cc12=True,
                 extract_step1=None, bsize1=1000, bsize2=200, regenie_bin="regenie", threads=4, fmt="pgen",
                 omp_workaround=True):
    """Dict step1/step2 -> shell command. covar_cols: comma list 'SEX,AGE,PC1,...'. binary: --bt (+--cc12 if
    phenotype coded 1/2) and step-2 --firth --approx. Step-2 results: {out}_s2_{pheno}.regenie."""
    pre = "KMP_AFFINITY=disabled " if omp_workaround else ""
    base = "%s%s --%s %s --phenoFile %s --phenoColList %s --covarFile %s --covarColList %s --threads %d" % (
        pre, regenie_bin, "bed" if fmt == "bed" else fmt, pfile, pheno_file, pheno, covar_file, covar_cols, threads)
    tr = (" --bt" + (" --cc12" if cc12 else "")) if binary else " --qt"
    s1 = "%s --step 1%s --bsize %d%s --out %s_s1" % (
        base, tr, bsize1, (" --extract %s" % extract_step1) if extract_step1 else "", out)
    s2 = "%s --step 2%s --bsize %d --pred %s_s1_pred.list%s --out %s_s2" % (
        base, tr, bsize2, out, " --firth --approx" if binary else "", out)
    return {"step1": s1, "step2": s2}


def load_sumstats(path, tool="plink2"):
    """Standardized DataFrame: CHR (int; X=23), POS, ID, A1 (effect allele), BETA, SE, P, LOG10P (+OR for
    logistic, A1_FREQ if present). plink2 .glm.* keeps TEST==ADD rows with empty ERRCODE; regenie .regenie[.gz]."""
    import numpy as np
    import pandas as pd
    codes = {"X": 23, "Y": 24, "XY": 25, "PAR1": 25, "PAR2": 25, "MT": 26, "M": 26}
    if tool == "plink2":
        d = pd.read_csv(path, sep="\t", dtype={"#CHROM": str})
        d = d[(d["TEST"] == "ADD") & (d["ERRCODE"].astype(str).isin([".", "nan"]))] if "TEST" in d else d
        d = d.rename(columns={"#CHROM": "CHR", "LOG(OR)_SE": "SE"})
        if "OR" in d:
            d["BETA"] = np.log(d["OR"])
        pcol = "P"
        if "LOG10_P" in d:
            d["LOG10P"] = d["LOG10_P"]
        else:
            d["LOG10P"] = -np.log10(d["P"].astype(float))
    elif tool == "regenie":
        d = pd.read_csv(path, sep=r"\s+", dtype={"CHROM": str})
        d = d.rename(columns={"CHROM": "CHR", "GENPOS": "POS", "ALLELE1": "A1"})
        d["P"] = np.power(10.0, -d["LOG10P"])
    else:
        raise ValueError("tool must be 'plink2' or 'regenie'")
    d["CHR"] = d["CHR"].astype(str).str.replace("chr", "").map(lambda c: codes.get(c, c)).astype(int)
    keep = [c for c in ["CHR", "POS", "ID", "A1", "A1_FREQ", "A1FREQ", "OR", "BETA", "SE", "P", "LOG10P"] if c in d]
    return d[keep].reset_index(drop=True)


def genomic_inflation(p):
    """lambda_GC = median(chi2_1df from p) / 0.4549364 (median of chi2 with 1 df)."""
    import numpy as np
    from scipy.stats import chi2
    p = np.asarray(p, dtype=float)
    p = p[np.isfinite(p) & (p > 0) & (p <= 1)]
    return float(np.median(chi2.isf(p, 1)) / chi2.ppf(0.5, 1))


def manhattan_plot(df, threshold=5e-8, suggestive=1e-5, highlight=None, title=None, figsize=(11, 4)):
    """Manhattan plot from standardized sumstats (CHR, POS, P). highlight: iterable of variant IDs to circle."""
    import numpy as np
    import matplotlib.pyplot as plt
    d = df.dropna(subset=["P"]).sort_values(["CHR", "POS"]).copy()
    d["y"] = -np.log10(d["P"].clip(lower=1e-300))
    off, ticks, lab, acc = {}, [], [], 0
    for c, g in d.groupby("CHR"):
        off[c] = acc - g.POS.min()
        ticks.append(acc + (g.POS.max() - g.POS.min()) / 2); lab.append("X" if c == 23 else str(c))
        acc += g.POS.max() - g.POS.min() + 1e6
    d["x"] = d.POS + d.CHR.map(off)
    fig, ax = plt.subplots(figsize=figsize)
    for i, (c, g) in enumerate(d.groupby("CHR")):
        ax.scatter(g.x, g.y, s=5, color=["#1f4e79", "#7f9fbf"][i % 2], linewidths=0, rasterized=True)
    ax.axhline(-np.log10(threshold), color="red", lw=0.8, ls="--")
    ax.axhline(-np.log10(suggestive), color="grey", lw=0.8, ls=":")
    if highlight is not None:
        h = d[d["ID"].isin(set(highlight))]
        ax.scatter(h.x, h.y, s=45, facecolors="none", edgecolors="orange", linewidths=1.2)
    ax.set_xticks(ticks); ax.set_xticklabels(lab, fontsize=7)
    ax.set_xlabel("Chromosome"); ax.set_ylabel(r"$-\log_{10}(P)$"); ax.set_title(title or "")
    fig.tight_layout()
    return fig


def qq_plot(p, title=None, figsize=(4, 4)):
    """QQ plot of observed vs expected -log10(P) with 95% CI band; annotates lambda_GC."""
    import numpy as np
    import matplotlib.pyplot as plt
    from scipy.stats import beta
    p = np.sort(np.asarray(p, dtype=float)); p = p[np.isfinite(p) & (p > 0)]
    n = len(p); i = np.arange(1, n + 1)
    exp = -np.log10(i / (n + 1))
    lo, hi = -np.log10(beta.ppf(0.975, i, n - i + 1)), -np.log10(beta.ppf(0.025, i, n - i + 1))
    fig, ax = plt.subplots(figsize=figsize)
    ax.fill_between(exp, lo, hi, color="lightgrey", lw=0)
    ax.scatter(exp, -np.log10(p), s=6, color="#1f4e79", linewidths=0)
    ax.plot([0, exp.max()], [0, exp.max()], color="red", lw=0.8)
    ax.text(0.05, 0.92, r"$\lambda_{GC}$ = %.3f" % genomic_inflation(p), transform=ax.transAxes)
    ax.set_xlabel(r"Expected $-\log_{10}(P)$"); ax.set_ylabel(r"Observed $-\log_{10}(P)$"); ax.set_title(title or "")
    fig.tight_layout()
    return fig
