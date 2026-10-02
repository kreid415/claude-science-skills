# Computational biology / single-cell & omics: common reviewer objections

Format as in ml-benchmarking.md. Seed sources were resolved against Crossref on 2026-10-02. Before recommending, search for
newer guidance and cite what you actually retrieved. General workflow reference: Heumos et al. 2023, Nat Rev Genet,
10.1038/s41576-023-00586-w (updated successor to Luecken & Theis 2019, Mol Syst Biol, 10.15252/msb.20188746).

## Units of replication
1. **Pseudoreplication: cells treated as independent samples.** Detect: DE or condition tests with n = cells; p-values from Wilcoxon on cells between conditions; error bars over cells. Reviewers' question: "what is N?"
   Rigorous: pseudobulk per donor/sample (edgeR/DESeq2/limma), or mixed models with a sample random effect; report the number of biological replicates.
   Sources: Squair et al. 2021, Nat Commun, 10.1038/s41467-021-25960-2; Zimmerman et al. 2021, Nat Commun, 10.1038/s41467-021-21038-1; Crowell et al. 2020 (muscat), Nat Commun, 10.1038/s41467-020-19894-4; Lazic et al. 2018, PLoS Biol, 10.1371/journal.pbio.2005282.

## Double dipping
2. **Clustering (or latent estimation) and testing on the same data.** Detect: marker genes "discovered" by DE between clusters defined from the same counts; p-values reported as evidence the clusters are real; latent factors tested for association using the data that defined them.
   Rigorous: count splitting / data thinning, selective inference, or independent validation data; or present DE between data-driven clusters as descriptive.
   Sources: Neufeld et al. 2022, Biostatistics, 10.1093/biostatistics/kxac047; Gao, Bien & Witten 2022, JASA, 10.1080/01621459.2022.2116331.

## Batch and confounding
3. **Batch confounded with condition; integration that removes biology.** Detect: each condition processed in its own batch; integration evaluated only by batch mixing.
   Rigorous: design check (batch × condition table); evaluate integration on both batch removal and biological conservation; state when the design cannot separate them.
   Sources: Luecken et al. 2022, Nat Methods, 10.1038/s41592-021-01336-8 (scIB); Tran et al. 2020, Genome Biol, 10.1186/s13059-019-1850-9.

## Simulation-based evaluation
4. **Conclusions resting on simulations that do not resemble real data.** Detect: Splatter-style or custom simulations with no comparison of simulated vs real summary statistics; the method's own generative model used to simulate.
   Rigorous: quantify simulation realism against a reference dataset; simulate from a model different from the method's; confirm key conclusions on real data with known structure (spike-ins, cell lines, perturbations, annotated atlases).
   Sources: Crowell et al. 2023, Genome Biol, 10.1186/s13059-023-02904-1; Cao, Yang & Yang 2021, Nat Commun, 10.1038/s41467-021-27130-w.

## Visualization and interpretation
5. **Interpreting distances or structure in t-SNE/UMAP.** Detect: claims about cluster distances, continuity, or relative sizes from a 2D embedding.
   Rigorous: base quantitative claims on the high-dimensional space or latent; use embeddings only for display.
   Source: Chari & Pachter 2023, PLoS Comput Biol, 10.1371/journal.pcbi.1011288.

## Method benchmarking
6. **Benchmark scope.** Too few datasets, datasets chosen after seeing results, a single ground-truth definition, missing runtime or scalability.
   Rigorous: pre-declared dataset panel spanning technologies and tissues; several ground-truth types; runtime and memory; neutral reporting.
   Sources: Weber et al. 2019, Genome Biol, 10.1186/s13059-019-1738-8; Soneson & Robinson 2018, Nat Methods, 10.1038/nmeth.4612.

## Data and reporting
7. **Dataset provenance and preprocessing.** Accessions, versions, QC thresholds, filtering counts, normalization, HVG choice, and gene ID mapping should be reported; QC thresholds chosen per dataset should be justified.
8. **Annotation circularity.** Cell-type labels derived with the method or features under evaluation, then used as ground truth.
