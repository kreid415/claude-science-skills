# claude-science-skills

Agent skills for Claude Science, one subfolder per skill. They are written to be general: site-specific values (cluster names, scratch roots, accounts) are placeholders that you fill in for your own environment.

Each skill is self-contained: a `SKILL.md` (YAML frontmatter with the trigger
description, then the guidance the agent reads) plus optional `kernel.py`
helper functions loaded into the analysis kernel when the skill is activated,
`references/` for detail the agent consults on demand, and `scripts/` for
standalone command-line tools.

## Skills

### `deslop`
Strip AI writing tells from scientific prose: manuscripts, abstracts, cover
letters, grant narratives, reviewer responses, figure captions, slides, and
READMEs. Rules are calibrated for technical writing, so numbers, claim
strength, citations, hedges, and terms of art are protected rather than
smoothed away. Passive voice stays in Methods; hedges are reduced to one, not
zero. `kernel.py` ships a linter (`deslop_scan`, `deslop_report`,
`deslop_score`) that flags banned phrases, em dashes, tricolons, bold-first
bullets, hedge stacks, vague attribution, passive-voice candidates, and
sentence-rhythm monotony, scoring a draft out of 50. Secondary calibration for
blog posts, memos, and newsletters.

### `reproducible`
Make a project's results auditable by a human scientist, and scaffold a
git-ready repository to hold them. Traces reported numbers back to the
artifacts that produced them, produces a provenance and verification bundle,
and organizes a project into a `data-gather + src + experiments + results +
paper` tree. Triggers on "audit my results", "can a reviewer check this", or
"where did this number come from".

### `cluster-autoscout`
Scan every connected SSH/SLURM cluster for available nodes and pick the best
partition and account for a job by immediate availability, then lowest wait.
Use before dispatching remote GPU or CPU compute to land the job on whichever
cluster is least contended, and to spread jobs across clusters. Also covers
placing job data, caches, and conda environments on cluster scratch rather
than home. Complements `remote-compute-ssh`, which covers submit and harvest
once a target is chosen.

### `rigor-review`

Project-long scientific rigor review of each output (figure, table, script,
draft). Flags issues likely to draw reviewer pushback, recommends more
rigorous approaches backed by retrieved literature, and runs a tiered
prior-work search (OpenAlex + arXiv + Europe PMC, LLM abstract triage,
full-text and citation-graph escalation). Findings persist in a
`rigor_ledger.json`/`.md` artifact across the project. `references/` holds
pushback catalogs for ML benchmarking, computational biology, and general
statistics (seed citations resolved against Crossref/arXiv/JMLR).

### `science-daemon-ops`
Diagnose and maintain a self-hosted Claude Science daemon: the app process
itself, not the science it runs. Covers crashes, self-update restarts, hangs,
session loss, resident-memory growth, database bloat and VACUUM, orphaned
workspaces, and SSH port forwarding. Distinguishes graceful self-update
restarts from real faults using the daemon's own logs.

### `session-handoff`
Judge whether a working session has grown long enough to hand off, and write
the handoff artifact that lets a fresh session continue without losing the
thread. Rotation is judged on task boundaries first and size second. Thresholds
are derived from measured session data rather than convention.

### `paper-outline`
Maintain a living, citation-backed outline of the paper a project is building
toward, kept in the project's git repo (`paper/`) and updated as the work
changes. Laid out like a paper: main text (Abstract, Introduction with research
questions, Methods, Results with experiments, Discussion), then References
generated from verified BibTeX, then Supplementary material for low-level
detail (method math, dataset specifics, experiment settings) that the main text
points to as `[S-ID]`. The supplement may only cite works the main text cites.
Unverified leads are flagged `(@?key)`; `evidence.md` records what each source
shows and where it was checked; a separate `CHANGELOG.md` records every change
and why. `kernel.py` provides init, consistency check, reference rendering,
Crossref BibTeX fetch, and changelog-plus-commit helpers.

### `lab-notebook`
Wet-lab-style electronic lab notebook for computational projects: timestamped,
append-only entries (EXPERIMENT, RESULT, FINDING, DECISION, ISSUE, NOTE,
CORRECTION) on dated daily pages under `notebook/`, written as work happens.
Entries carry a SHA-256 hash chain so edits, deletions, or reordering are
detectable (`nb_check`); mistakes are fixed with CORRECTION entries, never by
editing. A generated `INDEX.md` groups entries by experiment and lists decisions
and open issues; DECISION entries export to `audit/DECISIONS.md` for the
`reproducible` audit bundle. Writes take a file lock, so concurrent agents get
unique IDs. Cross-linked with `paper-outline` (outline changelog cites NB IDs).

### `fail-loud-pipelines`
Coding standard and gates for experiment runners, launchers, manifests and metric code, so that failures stop the run instead of becoming plausible values. `fl_lint` statically flags swallowed exceptions, first-file fallbacks (`listdir()[0]`), `set +e`/`|| true`, `parse_known_args`, numba `fastmath`, executors hardcoded in Nextflow process bodies, and zero exit after recorded failures. Runtime guards:
- `fl_args_consumed`: config keys the runner never reads.
- `fl_unique_outputs`: output paths that do not encode every swept parameter.
- `fl_completion_gate`: write `.done` only when every expected output exists, is finite and has no failed rows.
- `fl_columns`, `fl_env_guard`: exact column names; a CUDA build is still present after an environment fork.
- `fl_mutation_check`: proves a test fails on a known-bad input.

### `claim-gate`
Inline gate to run before any message that says done, fixed or verified, reports a number, or summarizes results. It is the per-message counterpart of the `reproducible` audit:
- `cg_readback`: read the value from the saved artifact.
- `cg_changed`: an "edited" file must differ from its pre-edit hash.
- `cg_reconcile`: counts must add up.
- `cg_scope`: claims are limited to what was checked.
- `cg_render`: numbers go into documents from a results dict; unresolved placeholders fail.
- `cg_stale`: outputs must postdate their code and inputs.
- `cg_contradictions`: prose numbers must match the table.

The agent appends a short claim receipt to its message.

### `experiment-preflight`
Domain-agnostic go/no-go gate run before compute is spent: a launch, sweep or wave, or adoption of a new metric. 24 PF items cover:
- pre-declared outcome;
- split unit vs label unit, and ID integrity;
- selection bias / winner's curse, matched arms and coverage-balanced pooling;
- no-op baselines that really are identities;
- n per condition;
- metric assumptions, and implementation vs source equation;
- benchmark confounds, pilot-based cost and walltime, and launch flags vs intended settings;
- multiplicity;
- inclusion filters.

Writes `experiments/<id>/PREFLIGHT.md` + `preflight.json`. GO requires evidence for every applicable blocking item. Domain packs plug in through `extra_items`.

### `experiment-preflight-singlecell`
Domain pack for single-cell, spatial and perturbation omics (AnnData/scanpy/scvi-tools), loaded with `experiment-preflight`. 22 SC items with helpers:
- donor-grouped splits and patient-ID collisions across cohorts;
- species filtering of gene panels;
- zero-intensity perturbations equal raw counts;
- cell-count/QC collapse along a perturbation axis;
- covariate keys chosen by name, not position;
- batch conditioning vs disentanglement claims;
- block vs per-dimension metric scoring;
- kNN metric validity bounds;
- foundation-model vocabulary coverage.

### `standing-instructions`
Per-project `CONSTRAINTS.md` ledger of the user's standing rules (data, method, format, writing, collaboration, process), each with its source. Rules are captured the moment they are stated or corrected. The agent checks applicable rules before every deliverable (`si_applicable`, `si_check_output`) and proposes, rather than makes, changes to an established decision. Open user questions are answered before a plan is executed (`si_open_questions`), and corrections are propagated to every file that states the old version (`si_propagate`). Mirrors `lab-notebook` DECISION entries.

### `doc-build-gate`
Builds the exact saved LaTeX/markdown/HTML deliverable and fails on:
- unresolved artifact markers or absolute sandbox paths;
- missing `\includegraphics` targets;
- any LaTeX warning class (citations, references, labels, missing files, boxes, reruns);
- images replaced by alt text, and embedded-figure counts that differ from the expected count.

Figure QA (`dg_figure_qa`, `dg_overlap_check`) catches blank panels, near-duplicate figures, text/legend overlaps and text outside the canvas.

### `run-status-board`
One status artifact per long remote run, computed only from the expected manifest, the outputs actually present and valid (via `fl_completion_gate`), and scheduler accounting (`sacct`/`squeue` parsers covering array tasks, OOM, TIMEOUT and requeues). Every unit is classified as done-valid, done-invalid, running, pending, failed or missing. `rs_eta` gives a throughput-based ETA and refuses one until enough units finish. All status answers quote the board and its timestamp. Queries only explicit job ids; never account-wide commands.

### `variant-annotation`
Annotate and interpret human DNA variants. Normalizes VCF records (`bcftools norm`) and rsID/HGVS/chr-pos-ref-alt inputs (Ensembl variant_recoder, REF check against the genome), predicts consequences with the Ensembl VEP REST API (GRCh38 or GRCh37) including AlphaMissense and REVEL scores, looks up ClinVar significance and review status through NCBI E-utilities, and retrieves gnomAD frequencies through the GraphQL API. `acmg_evidence_hints` frames PM2, BA1, PP3 and BP4 using published ClinGen score calibrations. Every merged table carries per-source provenance (endpoint, release, timestamp), and the skill documents label leakage between predictor training data and ClinVar releases. Research support, not clinical classification. REVEL is licensed for non-commercial use only.

### `variant-calling`
Germline and somatic short-read variant calling from BAM/CRAM. `check_bam_inputs` checks sort order, index, read groups and reference/contig consistency before calling. Command builders cover `bcftools mpileup | call`, the GATK HaplotypeCaller GVCF workflow (GenomicsDBImport or CombineGVCFs, then GenotypeGVCFs), GATK hard filters and the Mutect2 chain. They return shell strings that `run_cmd` executes and fails loudly on. `vcf_qc_summary` reports per-sample Ti/Tv, het/hom-alt, indel counts, missingness and depth, cross-checked against `bcftools stats`. `scripts/simulate_test_data.py` builds a seeded multi-sample test set.

### `gwas-analysis`
Genome-wide association studies with plink2 and regenie. Builds QC command chains (call rate, MAF, controls-only HWE, heterozygosity, KING relatedness, sex check), LD pruning and PCA covariates, plink2 `--glm` and regenie step 1/step 2 (with Firth for binary traits), and LD clumping. `load_sumstats` standardizes plink2 and regenie outputs. `genomic_inflation`, `manhattan_plot` and `qq_plot` handle post-processing. `scripts/simulate_gwas.py` generates seeded genotypes with planted causal variants for testing. regenie is run from its own conda env (passed as `regenie_bin`) because its bioconda build conflicts with current htslib.

## Tests

`tests/` holds synthetic fixtures that reproduce known failure patterns for the seven skills above: known-bad inputs must fail and known-good inputs must pass. Run them from the repo root with `bash tests/run_all.sh` (needs Python with numpy, pandas and pillow; `tectonic` is optional for the LaTeX path of `doc-build-gate`).

## Layout

```
<skill-name>/
  SKILL.md          # frontmatter + agent guidance
  kernel.py         # optional helper functions (auto-loaded)
  references/       # optional detail files, read on demand
  scripts/          # optional standalone CLI tools
```

`kernel.py` sidecars follow the Claude Science sidecar rules (functions, imports and literal constants only) and are auto-loaded when the skill is activated. Several skills call the Claude Science `host` API; outside Claude Science the pure-Python helpers (`fl_*`, `cg_*`, `pf_*`, `scpf_*`, `si_*`, `dg_*`, `rs_*`) can be imported by exec-ing `kernel.py`. Internal catalog metadata (`.catalog_stamp`, `.sync-org`, `.authorship`) is deliberately not versioned.
