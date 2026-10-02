"""Simulate a small GWAS dataset (VCF + pheno/covar) with planted causal SNPs. Fixed seed.
Usage: python simulate_gwas.py OUTDIR [seed] [n_samples] [n_snps]
Writes sim.vcf (GRCh38-style 'chr' names, ~0.5% missing calls), pheno_covar.tsv
(FID IID SEX AGE QT BT; SEX 1=male 2=female; BT 1=control 2=case), truth.json.
Autosomes chr1-10 in LD blocks of 10 SNPs (latent AR(1) rho=0.8), 200 chrX SNPs
(males homozygous), 4 duplicate samples, 3 planted causal SNPs."""
import sys, os, json
import numpy as np
from scipy.stats import norm


def simulate(outdir, seed=42, n=1000, m=5000, n_x=200, n_dup=4):
    rng = np.random.default_rng(seed)
    os.makedirs(outdir, exist_ok=True)
    block = 10
    G, chrs, pos, maf = [], [], [], []
    for c in range(1, 11):
        p0 = 100_000
        for b in range((m // 10) // block):
            f = rng.uniform(0.05, 0.5, block)
            z = np.empty((n, block))
            z[:, 0] = rng.standard_normal(n)
            for j in range(1, block):
                z[:, j] = 0.8 * z[:, j - 1] + 0.6 * rng.standard_normal(n)
            for j in range(block):
                u = norm.cdf(z[:, j])   # thresholds give HWE genotype frequencies for alt freq f
                G.append((u > (1 - f[j]) ** 2).astype(int) + (u > 1 - f[j] ** 2).astype(int))
                chrs.append(c); pos.append(p0); maf.append(f[j]); p0 += int(rng.integers(500, 3000))
    G = np.array(G).T
    m = G.shape[1]
    sex = rng.permutation(np.r_[np.ones(n // 2, int), 2 * np.ones(n - n // 2, int)])
    X = []
    for k in range(n_x):
        f = rng.uniform(0.1, 0.5)
        gm = 2 * (rng.random(n) < f)
        gf = rng.binomial(2, f, n)
        X.append(np.where(sex == 1, gm, gf)); chrs.append(23); pos.append(5_000_000 + 2000 * k); maf.append(f)
    G = np.hstack([G, np.array(X).T])
    chrs, pos, maf = np.array(chrs), np.array(pos), np.array(maf)
    dup_src = rng.choice(n - n_dup, n_dup, replace=False)
    G[n - n_dup:] = G[dup_src]; sex[n - n_dup:] = sex[dup_src]
    causal = [int(i) for i in rng.choice(np.arange(m)[maf[:m] > 0.2], 3, replace=False)]
    h2 = [0.06, 0.05, 0.04]
    gz = lambda i: (G[:, i] - G[:, i].mean()) / G[:, i].std()
    age = rng.normal(50, 10, n)
    qt = sum(np.sqrt(h) * gz(i) for h, i in zip(h2, causal)) + np.sqrt(1 - sum(h2)) * rng.standard_normal(n)
    qt = qt + 0.3 * (sex == 2) + 0.02 * (age - 50)
    liab = sum(0.45 * gz(i) for i in causal) + rng.standard_normal(n) + 0.01 * (age - 50)
    bt = np.where(liab > np.quantile(liab, 0.75), 2, 1)
    ids = [f"snp{i + 1}" for i in range(G.shape[1])]
    ref = rng.choice(list("ACGT"), G.shape[1])
    alt = np.array([rng.choice([b for b in "ACGT" if b != r]) for r in ref])
    iids = [f"S{i + 1:04d}" for i in range(n)]
    gt = {0: "0/0", 1: "0/1", 2: "1/1"}
    with open(os.path.join(outdir, "sim.vcf"), "w") as fh:
        fh.write("##fileformat=VCFv4.2\n##reference=GRCh38\n")
        fh.write('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n')
        fh.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(iids) + "\n")
        for j in range(G.shape[1]):
            cn = "chrX" if chrs[j] == 23 else f"chr{chrs[j]}"
            miss = rng.random(n) < 0.005
            row = ["./." if mm else gt[v] for v, mm in zip(G[:, j], miss)]
            fh.write(f"{cn}\t{pos[j]}\t{ids[j]}\t{ref[j]}\t{alt[j]}\t.\tPASS\t.\tGT\t" + "\t".join(row) + "\n")
    with open(os.path.join(outdir, "pheno_covar.tsv"), "w") as fh:
        fh.write("FID\tIID\tSEX\tAGE\tQT\tBT\n")
        for i in range(n):
            fh.write(f"{iids[i]}\t{iids[i]}\t{sex[i]}\t{age[i]:.2f}\t{qt[i]:.5f}\t{bt[i]}\n")
    truth = {"seed": seed, "causal_ids": [ids[i] for i in causal], "h2": h2, "n": n, "n_snps_autosomal": m,
             "n_chrX": n_x, "duplicate_pairs": [[iids[a], iids[n - n_dup + k]] for k, a in enumerate(dup_src)],
             "case_fraction": float((bt == 2).mean())}
    json.dump(truth, open(os.path.join(outdir, "truth.json"), "w"), indent=1)
    return truth


if __name__ == "__main__":
    a = sys.argv
    print(simulate(a[1], int(a[2]) if len(a) > 2 else 42, int(a[3]) if len(a) > 3 else 1000,
                   int(a[4]) if len(a) > 4 else 5000))
