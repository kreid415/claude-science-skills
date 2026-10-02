"""Simulate a tiny 3-sample paired-end Illumina-like dataset with known truth (fixed seed).
Usage: python simulate_test_data.py OUTDIR [SEED]
Writes ref.fa, truth.vcf and S{1,2,3}_R{1,2}.fastq.gz in OUTDIR.
S1 female, S2 male (haploid chrX), S3 female; ~30x; 8% PCR duplicate pairs; 0.3% base error."""
import gzip, os, random, sys

outdir = sys.argv[1]
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
rng = random.Random(seed)
os.makedirs(outdir, exist_ok=True)
CONTIGS = {"chr1": 60000, "chr2": 40000, "chrX": 40000}
SAMPLES = {"S1": "F", "S2": "M", "S3": "F"}
RL, COV = 100, 15  # COV is per haplotype copy -> ~30x diploid
TI = {"A": "G", "G": "A", "C": "T", "T": "C"}
COMP = str.maketrans("ACGT", "TGCA")

ref = {c: "".join(rng.choices("ACGT", weights=[3, 2, 2, 3], k=n)) for c, n in CONTIGS.items()}

def transversion(b):
    return rng.choice([x for x in "ACGT" if x != b and x != TI[b]])

variants = {c: [] for c in CONTIGS}  # (pos0, ref, alt)
for c, s in ref.items():
    pos = 50
    while True:
        pos += rng.randint(60, 540)
        if pos >= len(s) - 50:
            break
        if rng.random() < 0.9:  # SNP; P(transition)=0.667 -> expected Ti/Tv ~2.0
            b = s[pos]
            alt = TI[b] if rng.random() < 0.667 else transversion(b)
            variants[c].append((pos, b, alt))
        else:  # 1-3 bp indel
            L = rng.randint(1, 3)
            if rng.random() < 0.5:
                variants[c].append((pos, s[pos:pos + L + 1], s[pos]))
            else:
                variants[c].append((pos, s[pos], s[pos] + "".join(rng.choices("ACGT", k=L))))

def genotypes(sex, c):
    if c == "chrX" and sex == "M":  # haploid, non-PAR-like
        return [(int(rng.random() < 0.4),) for _ in variants[c]]
    return [rng.choice([(0, 1), (0, 1), (1, 1), (0, 0)]) for _ in variants[c]]

truth = {s: {c: genotypes(sex, c) for c in CONTIGS} for s, sex in SAMPLES.items()}

def build_hap(c, gts, h):
    seq, out, last = ref[c], [], 0
    for (pos, r, a), gt in zip(variants[c], gts):
        if h < len(gt) and gt[h] == 1:
            out += [seq[last:pos], a]
            last = pos + len(r)
    out.append(seq[last:])
    return "".join(out)

with open(f"{outdir}/ref.fa", "w") as f:
    for c, s in ref.items():
        f.write(f">{c}\n")
        f.writelines(s[i:i + 60] + "\n" for i in range(0, len(s), 60))
with open(f"{outdir}/truth.vcf", "w") as f:
    f.write("##fileformat=VCFv4.2\n")
    f.writelines(f"##contig=<ID={c},length={n}>\n" for c, n in CONTIGS.items())
    f.write('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n')
    f.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(SAMPLES) + "\n")
    for c in CONTIGS:
        for i, (pos, r, a) in enumerate(variants[c]):
            g = ["/".join(map(str, truth[s][c][i])) for s in SAMPLES]
            f.write(f"{c}\t{pos + 1}\t.\t{r}\t{a}\t.\t.\t.\tGT\t" + "\t".join(g) + "\n")

def add_errors(s, err=0.003):
    s = list(s)
    for i in range(len(s)):
        if rng.random() < err:
            s[i] = rng.choice([x for x in "ACGT" if x != s[i]])
    return "".join(s)

for s, sex in SAMPLES.items():
    haps = []
    for c in CONTIGS:
        for h in range(1 if (c == "chrX" and sex == "M") else 2):
            haps.append(build_hap(c, truth[s][c], h))
    total = sum(len(h) for h in haps)
    pairs = []
    for _ in range(int(total * COV / (2 * RL))):
        h = rng.choices(haps, weights=[len(x) for x in haps])[0]
        ins = max(RL + 20, int(rng.gauss(300, 30)))
        st = rng.randint(0, len(h) - ins)
        frag = h[st:st + ins]
        r1, r2 = frag[:RL], frag[-RL:].translate(COMP)[::-1]
        pairs.append((r1, r2) if rng.random() < 0.5 else (r2, r1))
    pairs += [rng.choice(pairs) for _ in range(int(0.08 * len(pairs)))]
    rng.shuffle(pairs)
    with gzip.open(f"{outdir}/{s}_R1.fastq.gz", "wt") as f1, gzip.open(f"{outdir}/{s}_R2.fastq.gz", "wt") as f2:
        for i, (a, b) in enumerate(pairs):
            for f, x, n in ((f1, a, 1), (f2, b, 2)):
                x = add_errors(x)
                f.write(f"@{s}_{i}/{n}\n{x}\n+\n{'F' * len(x)}\n")
    print(s, sex, "pairs", len(pairs))
print({c: len(v) for c, v in variants.items()})
