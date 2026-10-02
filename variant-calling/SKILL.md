---
name: variant-calling
description: "Call and QC germline or somatic DNA variants (SNVs/indels) from aligned short-read Illumina data: BAM/CRAM pre-flight checks (sorted, indexed, read groups, reference .fai/.dict, contig match, duplicate marking), bcftools mpileup|call multisample calling, GATK HaplotypeCaller -ERC GVCF -> GenomicsDBImport/CombineGVCFs -> GenotypeGVCFs, Mutect2 tumor/normal with panel of normals and FilterMutectCalls, GATK hard filters vs VQSR vs bcftools filter expressions, bcftools norm and tabix/csi indexing, and VCF QC (Ti/Tv, het/hom-alt, missingness, depth, bcftools stats/plot-vcfstats, chrX sex check, haploid male X). Use whenever the user has BAM/CRAM or GVCF/VCF files and says variant calling, call SNPs/indels, genotype, joint calling, gVCF, hard filter, VQSR, Mutect2, somatic mutations, Ti/Tv, vcf QC, sex check, normalize VCF. Not for annotation (variant-annotation), GWAS, or long-read callers."
---

# Variant calling and VCF QC (short-read, DNA)

Helpers are loaded from `kernel.py` when this skill is loaded (python kernel; env needs bcftools, samtools, gatk4, pysam, cyvcf2, pandas, numpy). The command builders RETURN shell strings; only `run_cmd` executes.

## Use / don't use
- Use: BAM/CRAM -> VCF, joint genotyping, filtering, VCF QC, normalization/indexing.
- Full tumor-normal pipelines: `nextflow` (nf-core/sarek); PacBio tumor-normal: `pacsomatic`. This skill covers the building blocks.
- Downstream annotation/consequence/ClinVar/gnomAD: `variant-annotation`. Programmatic BAM/VCF access: `pysam`. GWAS/association: separate GWAS skill.
- Long reads: DeepVariant (also short-read) and Clair3 are the usual callers; not covered here.

## Workflow
1. **Pre-flight** every BAM: `chk = check_bam_inputs(bam, ref)`; stop if `chk["ok"]` is False (errors listed). Checks `@HD SO:coordinate`, index, `@RG` with SM/PL and RG tag on reads, `ref.fa.fai`, `ref.dict`, BAM contigs present in the FASTA with equal length and order, duplicate flags. Warnings (e.g. no duplicate marking) need a decision, not silence.
2. **Duplicates** (skip for amplicon/targeted-PCR data):
   `samtools fixmate -m -u in.bam - | samtools sort -u - | samtools markdup - out.bam && samtools index out.bam` (name-sorted input to fixmate, `-m` adds the mate-score tag markdup needs; `-r` would remove rather than flag), or `gatk MarkDuplicates -I in.bam -O out.bam -M metrics.txt` (coordinate-sorted input). Align with the read group set at alignment time: `bwa mem -R '@RG\tID:S1.L1\tSM:S1\tPL:ILLUMINA\tLB:lib1'`.
3. **Pick a caller**
   - Few samples, speed, or non-human: bcftools. `cmd = bcftools_call_cmd(bams, ref, "cohort.vcf.gz", regions=None, ploidy_file=..., samples_file=...)`; `run_cmd(cmd)`. Multisample calling (`-m`) pools evidence; output is `-v` variant-only and tbi-indexed; `-a GQ` is added because `call` emits no GQ otherwise.
   - GATK best-practice germline: `cmds = haplotypecaller_gvcf_cmds({"S1": bam1, ...}, ref, outdir, intervals=["chr1", ...], combine="genomicsdb"|"combinegvcfs")`, then `for c in cmds: run_cmd(c)`. Per-sample `HaplotypeCaller -ERC GVCF`, then `GenomicsDBImport` (needs `-L`; workspace dir must not exist; the helper passes one `-L` per interval) -> `GenotypeGVCFs -V gendb://...` (works without `-L`), or `CombineGVCFs` -> `GenotypeGVCFs` for small cohorts. Scale: scatter per contig/interval, pass a bigger `java_mem`; new samples later need `--genomicsdb-update-workspace-path` (documented in GenomicsDBImport usage) or a rerun.
   - Somatic: see Mutect2 below.
4. **Filter** (see Filtering). 5. **Normalize + index**. 6. **QC** (see VCF QC). Record provenance.

## Sex and ploidy (do before calling X/Y)
Diploid calling on haploid male non-PAR X creates spurious low-AF hets. In the smoke test (chrX, one male at ~26x), diploid HaplotypeCaller gave 9 false-positive sites versus 0 FP / 0 FN with `HaplotypeCaller -L chrX --sample-ploidy 1`; bcftools went from 4 to 1 total FP with a ploidy file and `-S` sex file (male chrX het fraction 0.05 -> 0.000).
- bcftools: `--ploidy GRCh38|GRCh37` plus `-S samples.txt` (`sample<TAB>M|F`), or `--ploidy-file` (`CHROM FROM TO SEX PLOIDY`, 1-based). Built-in definitions: `bcftools call --ploidy 'GRCh38?'`.
- GATK: run haploid samples/regions with `extra_hc="--sample-ploidy 1"` on the non-PAR interval only. Joint genotyping of mixed-ploidy GVCFs was NOT tested here; genotype haploid-X males separately or follow GATK docs.
- Sex check: `vcf_qc_summary(vcf, x_region="chrX:2781480-155701381")` (GRCh38 non-PAR; GRCh37: `X:2699521-154931043`; both read from bcftools' built-in ploidy tables). Het fraction at X SNPs: females near their autosomal het fraction, males near 0. Call this on diploid-called data (otherwise males are forced haploid and the contrast is gone), and compare with the reported sex. Smoke test: females 0.66/0.71, male 0.05.

## Mutect2 (tumor/normal) - commands verified to run on 4.6.2.0
```
gatk Mutect2 -R ref.fa -I tumor.bam -I normal.bam -normal NORMAL_SM \
  --germline-resource af-only-gnomad.vcf.gz --panel-of-normals pon.vcf.gz \
  --f1r2-tar-gz f1r2.tar.gz -O somatic.vcf.gz          # also writes somatic.vcf.gz.stats
gatk LearnReadOrientationModel -I f1r2.tar.gz -O read-orientation-model.tar.gz
gatk GetPileupSummaries -I tumor.bam -V common_biallelic.vcf.gz -L common_biallelic.vcf.gz -O tumor.pileups.table
gatk GetPileupSummaries -I normal.bam -V common_biallelic.vcf.gz -L common_biallelic.vcf.gz -O normal.pileups.table
gatk CalculateContamination -I tumor.pileups.table -matched normal.pileups.table \
  -O contamination.table --tumor-segmentation segments.table
gatk FilterMutectCalls -R ref.fa -V somatic.vcf.gz --contamination-table contamination.table \
  --tumor-segmentation segments.table --ob-priors read-orientation-model.tar.gz -O somatic.filtered.vcf.gz
```
- Tumor sample name is auto-detected (`--tumor-sample` is deprecated); give `-normal` with the normal's SM. Tumor-only: omit the normal.
- `--germline-resource` (af-only gnomAD) and `--panel-of-normals` are optional but recommended; absent alleles get `--af-of-alleles-not-in-resource` (default adjusts: 5e-8 tumor-only, 1e-6 tumor-normal, 4e-3 mitochondria mode).
- Panel of normals: per normal `gatk Mutect2 -R ref -I normalN.bam -max-mnp-distance 0 -O normalN.vcf.gz`; `GenomicsDBImport -R ref -L intervals --genomicsdb-workspace-path pon_db -V normal1.vcf.gz ...`; `CreateSomaticPanelOfNormals -R ref -V gendb://pon_db -O pon.vcf.gz`.
- FFPE/oxidation artefacts: always collect `--f1r2-tar-gz` and pass `--ob-priors` (flag name verified in FilterMutectCalls --help; long form `--orientation-bias-artifact-priors`). Filter lists in the FILTER column; keep `PASS`.
- Every command above (including PoN creation, GetPileupSummaries/CalculateContamination with a small AF-annotated site VCF, and FilterMutectCalls with contamination, segmentation and orientation priors) was executed on simulated germline samples (S2 as 'tumor', S1 as 'normal') as a syntax/format check only; the calls are not a somatic benchmark.

## Filtering
- **GATK hard filters** (generic, deliberately lenient starting points per GATK; split SNP and INDEL first; `gatk_hard_filter_cmds(vcf, ref, prefix)` builds SelectVariants -> VariantFiltration -> MergeVcfs):
  - SNPs: `QD < 2.0`, `QUAL < 30.0`, `SOR > 3.0`, `FS > 60.0`, `MQ < 40.0`, `MQRankSum < -12.5`, `ReadPosRankSum < -8.0`.
  - Indels: `QD < 2.0`, `QUAL < 30.0`, `FS > 200.0`, `ReadPosRankSum < -20.0`. Earlier GATK-team guidance also lists `SOR > 10.0` for indels; the helper follows the current "How to filter" article (without it); append `--filter-expression "SOR > 10.0" --filter-name SOR10` if wanted.
  - Missing annotation: VariantFiltration treats a missing value as not failing (`--missing-values-evaluate-as-failing` default false). Write thresholds as floats (`3.0`) and parenthesize combined expressions.
  - Thresholds are tuned to human WGS/WES; for other organisms or very small data inspect the QD/FS/MQ distributions before trusting them. Adding `DP`/depth filters is data-specific (maximum DP only makes sense for WGS).
- **VQSR** (VariantRecalibrator + ApplyVQSR): GATK says it works with at least one whole genome or about 30 exomes in humans, needs curated training resources (human bundle), and needs many variant sites; do not run it on one/few exomes, targeted panels or non-model organisms (use hard filters, or pad exomes with matching public data). Run per exome kit, not across kits.
- **bcftools filter** (soft-filter, keeps records; `-i` include, `-e` exclude, `-s` name, `-S .` sets failing genotypes to missing, `-g/-G` SnpGap/IndelGap):
  `bcftools filter -s LowQual -e 'QUAL<30 || INFO/DP<10' -Ou in.vcf.gz | bcftools filter -S . -e 'FMT/DP<8 || FMT/GQ<20' -Oz -o out.vcf.gz --write-index=tbi`
  Tested: 3 sites -> LowQual, 9 genotypes -> `./.` (0.7% missing). mpileup's INFO/DP and per-sample FORMAT/DP must have been requested (`-a FORMAT/DP`; helper does).
- Genotype-level QC matters as much as site filters: VQSR/hard filters do not touch low-GQ genotypes.

## Normalize and index
`bcftools norm -m -any -f ref.fa -c w -Oz -o out.vcf.gz --write-index=tbi in.vcf.gz` splits multiallelics (`-m -any`) and left-aligns indels against the FASTA. `-c` default is `e` (exit on REF mismatch) which usually means wrong build/contig naming; `w` warns, `x` excludes, `s` fixes (does not repair PL). Rejoin with `-m +any`. `--write-index[=tbi|csi]` (default csi) or `bcftools index -t|-c`; `tabix -p vcf` for tbi. Normalize before comparing callsets, merging, or annotating.

## VCF QC
- `qc = vcf_qc_summary(vcf, region=None, pass_only=True, x_region=None)` -> one row per sample: `n_het, n_hom_alt` (SNP+indel), `n_het_snp, n_hom_alt_snp, het_hom_ratio_snp, n_snp, n_indel, ti, tv, titv, missing_rate, mean_dp, mean_gq` (+ `x_n_snps, x_het_frac`). Ti/Tv counts biallelic SNPs where the sample carries the alt allele; verified identical to `bcftools stats -s -` PSC (`bcftools_stats_psc(vcf)` returns that table for cross-checks; mean_dp matched within 0.03). cyvcf2 loops in Python: for tens of millions of records use `bcftools stats` instead.
- Reference ranges (GATK, human, single sample): Ti/Tv 2.0-2.1 for WGS, 3.0-3.3 for WES (exome flanks lower it); roughly 4.4M variants WGS / 41k WES. Lower Ti/Tv signals excess false positives (random errors approach 0.5); compute it on the same region type as the reference range. Small simulated or non-human data will not match.
- Het/hom-alt (SNP): depends on ancestry and cohort; judge samples against each other, not a fixed number. Outlier high het -> contamination/sample mix; low het -> inbreeding/ROH or genotype-calling bias.
- Missingness per sample and depth distribution: `bcftools stats -s - -f PASS,. x.vcf.gz > x.vchk; plot-vcfstats -P -p plots x.vchk` (`-P` skips the PDF/pdflatex step; PNGs: depth, hets_by_sample, tstv_by_sample, substitutions, vaf, singletons).
- Always report whether QC used all records or PASS only, and the region (autosomes vs whole genome).

## Pitfalls
- bcftools mpileup 1.24 defaults (from `--help`): `-q 0` (min MAPQ), `-Q 1` (min base quality), `-d 250` per-file depth cap (raise for deep panels/amplicons), BAQ on. The helper sets `-q 20 -Q 20`: adjust for your data and record it.
- `run_cmd` uses `bash -o pipefail`, so mpileup failures are not masked by `bcftools call`; `| head` returns 141.
- GATK needs `.dict` and `.fai`, same contig order as the BAM header; chr1 vs 1 naming mismatches are the usual cause of empty calls.
- `GenomicsDBImport` fails if the workspace exists; delete it explicitly (ask the user first) or use a new path. `-L` is mandatory there.
- GVCF sample name comes from RG `SM`; one SM per GVCF, unique across a cohort.
- bcftools 1.24 printed `[W::bcf_hdr_check_sanity] MQ should be declared as Type=Float` when reading mpileup/call output here; the calls were unaffected.
- Do not use hard-filtered PASS-only counts to compare to unfiltered reference Ti/Tv without saying so.
- Optional steps not covered/tested here: BQSR (BaseRecalibrator/ApplyBQSR with known sites), GATK CNN/NVScoreVariants, CollectVariantCallingMetrics, trio/Mendelian checks, gVCF merging with `bcftools merge`/GLnexus.

## Provenance (record with every callset)
Reference build (GRCh38 vs GRCh37 vs other) and FASTA name + md5, contig naming (chr-prefixed or not), tool versions (`bcftools --version`, `gatk --version`, `samtools --version`), the exact command strings (the builders return them; save the list), `-q/-Q/-d`, intervals/BED, known-sites and germline-resource/PoN names and release dates, filter thresholds, sample-sex table and ploidy files, QC table. bcftools also stamps `##bcftools_*Command` lines in the VCF header.

## Smoke test (simulated, fixed seed; sanity reference for the helpers, not a performance claim)
`scripts/simulate_test_data.py OUTDIR 42` writes a 140 kb reference (chr1/chr2/chrX), truth VCF and FASTQs for 3 samples (S1 F, S2 M haploid X, S3 F; ~30x; 8% duplicate pairs). Align: `bwa index ref.fa; bwa mem -R ... | samtools fixmate -m -u - - | samtools sort -u - | samtools markdup - S1.bam; samtools index`; `samtools faidx ref.fa`; `gatk CreateSequenceDictionary -R ref.fa`.
Results (460 truth sites carrying an alt allele): bcftools mpileup|call -m: 464 sites, recall 1.000, precision 0.991; HaplotypeCaller GVCF + GenomicsDBImport + GenotypeGVCFs + hard filters: 468 PASS (2 filtered SOR3), recall 0.998, precision 0.981 (all 9 FPs in the diploid-called male X); genotype concordance 1.0 for shared sites; genomicsdb and CombineGVCFs routes gave the same 470 sites. Ti/Tv per sample 1.8-2.2 (simulated at ~2.0 with few sites; sampling noise is large with ~300 SNPs).

## Sources checked
Tool versions verified against: bcftools 1.24 (htslib 1.24), samtools 1.24, bwa 0.7.19, GATK 4.6.2.0 (HTSJDK 4.2.0, Picard 3.4.0), pysam 0.24.1, cyvcf2 0.34.0, pandas 3.0.6 (conda env `genomics`).
- Installed-binary `--help` (flags/defaults): `bcftools mpileup|call|norm|stats|filter|index`, `plot-vcfstats -h`, `bcftools call --ploidy 'GRCh38?'`/`'GRCh37?'`, `gatk HaplotypeCaller|GenomicsDBImport|GenotypeGVCFs|CombineGVCFs|VariantFiltration|Mutect2|FilterMutectCalls|MarkDuplicates|LearnReadOrientationModel --help`; every command in this file was executed on the simulated data.
- bcftools manual source: https://raw.githubusercontent.com/samtools/bcftools/develop/doc/bcftools.txt (https://samtools.github.io/bcftools/bcftools.html was not reachable from the sandbox).
- GATK source javadoc (usage examples, defaults): https://github.com/broadinstitute/gatk/tree/master/src/main/java/org/broadinstitute/hellbender/tools/walkers (Mutect2, FilterMutectCalls, CreateSomaticPanelOfNormals, GetPileupSummaries, CalculateContamination, HaplotypeCaller, GenotypeGVCFs, GenomicsDBImport, CombineGVCFs, VariantFiltration).
- GATK hard-filter thresholds: https://gatk.broadinstitute.org/hc/en-us/articles/360035531112--How-to-Filter-variants-either-with-VQSR-or-by-hard-filtering (SNP and indel expressions) and https://gatk.broadinstitute.org/hc/en-us/articles/360035890471-Hard-filtering-germline-short-variants (rationale, "very lenient"). The sandbox could not fetch these pages directly (Cloudflare challenge, HTTP 403); the thresholds were read from the pages' text as returned by web search and cross-checked against the GATK-team forum table (indel SOR>10) and a published module that applies the same values.
- Ti/Tv and variant-count reference: https://gatk.broadinstitute.org/hc/en-us/articles/360035531572-Evaluating-the-quality-of-a-germline-short-variant-callset (same access limitation; read via search excerpt).
- VQSR sample-size guidance: https://gatk.broadinstitute.org/hc/en-us/articles/360035531612-Variant-Quality-Score-Recalibration-VQSR (search excerpt) and https://gatk.broadinstitute.org/hc/en-us/articles/4402736812443-Which-training-sets-arguments-should-I-use-for-running-VQSR.
- Not independently verified: the `variant-annotation` skill (being authored in parallel) and the `pacsomatic`/`nextflow` skill contents beyond their catalog descriptions.
