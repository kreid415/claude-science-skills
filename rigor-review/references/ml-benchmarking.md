# ML / benchmarking: common reviewer objections

Each entry: **what reviewers object to** → how to detect it in the output or its code → the more rigorous approach → seed sources.
Seed sources were resolved against Crossref / arXiv / JMLR on 2026-10-02. They are starting points: before recommending,
search for newer guidance (last ~3 years) and cite what you actually retrieved.

## Baselines and comparison fairness
1. **Weak or untuned baselines; tuning budget favors the proposed method.**
   Detect: hyperparameter search only for the new method; baselines run at defaults; different epochs or early-stopping rules; baselines numbers copied from other papers under different preprocessing.
   Rigorous: equal tuning budget (same search space size, trials, and selection criterion) for every method; report the budget; include strong recent baselines and a simple baseline (linear, PCA, kNN, raw features).
   Sources: Melis et al. 2018, arXiv:1707.05589 (well-tuned standard LSTMs outperformed newer architectures); Lipton & Steinhardt 2019, Queue, 10.1145/3317287.3328534.
2. **Missing ablations.** A multi-component method with no component-wise removal. Rigorous: ablate each component; report which carries the gain.
3. **Self-assessment trap / benchmark designed by method authors.** Detect: authors' method wins every dataset and metric. Rigorous: neutral benchmark design, pre-declared datasets and metrics, report losses.
   Sources: Norel, Rice & Stolovitzky 2011, Mol Syst Biol, 10.1038/msb.2011.70; Weber et al. 2019, Genome Biol, 10.1186/s13059-019-1738-8.

## Variance and statistical comparison
4. **Single seed / no variance.** Detect: one run per configuration; no error bars; seed fixed once. Rigorous: multiple seeds (and data splits) with the variance source stated; report mean with interval, not best run.
   Sources: Bouthillier et al. 2021, arXiv:2103.03098 (MLSys); Henderson et al. 2018, AAAI, 10.1609/aaai.v32i1.11694.
5. **Invalid significance tests across folds/datasets.** Detect: paired t-test over CV folds (overlapping training sets); bold "best" without a test; many dataset × metric comparisons uncorrected.
   Rigorous: corrected resampled t-test (Nadeau & Bengio), 5×2cv (Dietterich), Friedman/Nemenyi or Wilcoxon over datasets (Demšar), or Bayesian comparison (Benavoli).
   Sources: Nadeau & Bengio 2003, Mach Learn, 10.1023/a:1024068626366; Dietterich 1998, Neural Comput, 10.1162/089976698300017197; Demšar 2006, JMLR 7 (jmlr.org/papers/v7/demsar06a); Benavoli et al. 2017, JMLR 18 (jmlr.org/papers/v18/16-305).
6. **Small test sets with tight-looking numbers.** Rigorous: report the CV error bar; it is often larger than the claimed gain.
   Source: Varoquaux 2018, NeuroImage, 10.1016/j.neuroimage.2017.06.061.

## Leakage
7. **Train/test contamination.** Detect in code: normalization, feature selection, HVG selection, PCA, imputation, or batch correction fit on the full dataset before splitting; hyperparameters chosen on the test set; duplicates or the same donor/patient/cell line across splits; temporal leakage; test labels used for early stopping.
   Rigorous: fit every preprocessing step inside the training fold; group-aware splits (donor, study, batch); held-out dataset or study for generalization claims.
   Sources: Kapoor & Narayanan 2023, Patterns, 10.1016/j.patter.2023.100804 (leakage taxonomy); Kapoor et al. 2024 REFORMS, Sci Adv, 10.1126/sciadv.adk3452; Walsh et al. 2021 DOME, Nat Methods, 10.1038/s41592-021-01205-4.

## Metrics and claims
8. **Metric does not measure the claim.** Detect: claim about biology, generalization, or interpretability supported only by a reconstruction or proxy score; a metric whose behavior under known ground truth was never checked.
   Rigorous: validate metrics on controlled cases with known answers (positive and negative controls); report several complementary metrics; show failure cases.
9. **Unsupervised disentanglement claims.** Detect: claims that a model "discovers" factors without supervision or inductive bias; model selection using ground-truth-based metrics that would be unavailable in practice; few seeds.
   Rigorous: state the inductive bias or supervision; report seed and hyperparameter variance (often larger than model differences); separate model selection from evaluation.
   Source: Locatello et al. 2019, ICML, arXiv:1811.12359 (AAAI commentary 10.1609/aaai.v34i09.7120).
10. **Overclaiming / speculation as explanation.** "State of the art", "first", "novel" without a scoped search; mechanism language for correlational results. Source: Lipton & Steinhardt 2019.

## Reproducibility and reporting
11. **Missing compute, data, and code details.** Rigorous: report hyperparameters, search spaces, compute, data versions, preprocessing, and seeds; release code.
    Sources: Pineau et al. 2021, arXiv:2003.12206 (NeurIPS reproducibility checklist); REFORMS; DOME.
