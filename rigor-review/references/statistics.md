# General statistics: common reviewer objections

Format as in ml-benchmarking.md. Seed sources were resolved against Crossref on 2026-10-02.

1. **Multiplicity.** Detect: many tests (genes, metrics × datasets, subgroups) with nominal p-values; correction applied to a subset only.
   Rigorous: FDR (Benjamini–Hochberg) or FWER control over the full family actually tested; state the family.
   Source: Benjamini & Hochberg 1995, JRSS-B, 10.1111/j.2517-6161.1995.tb02031.x.
2. **Analytic flexibility (forking paths).** Detect: thresholds, subsets, or metrics changed across iterations of the same analysis; only the favorable variant reported. The rigor ledger history is evidence here.
   Rigorous: pre-declare the primary analysis; report sensitivity analyses across reasonable alternatives (multiverse).
   Sources: Gelman & Loken 2014, Am Sci, 10.1511/2014.111.460; Ioannidis 2005, PLoS Med, 10.1371/journal.pmed.0020124.
3. **p-values without effect sizes or uncertainty; "significant" as the finding.** Rigorous: effect sizes with confidence or credible intervals; interpret magnitude; do not equate p > 0.05 with no effect.
   Sources: Wasserstein & Lazar 2016, Am Stat, 10.1080/00031305.2016.1154108; Amrhein et al. 2019, Nature, 10.1038/d41586-019-00857-9.
4. **Underpowered designs.** Detect: few biological replicates, wide intervals, claims of "no difference". Rigorous: power or precision analysis; equivalence tests for "no difference" claims.
   Source: Button et al. 2013, Nat Rev Neurosci, 10.1038/nrn3475.
5. **Wrong test for the data.** Paired data analyzed as unpaired; non-independent observations; normality assumed for heavy-tailed or bounded scores; correlations on compositional data.
6. **Causal language from observational associations.** Rigorous: correlational wording, or an explicit identification strategy with stated assumptions.
7. **Error bars undefined.** SD vs SEM vs CI unstated, or n unstated in legends.
8. **Circular or selection-biased evaluation.** Selecting on the outcome, regression to the mean, evaluating on the data used for selection (see compbio.md §2 and ml-benchmarking.md §7).
