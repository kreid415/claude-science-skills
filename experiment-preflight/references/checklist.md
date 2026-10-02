# experiment-preflight: checklist rationale

Source of truth for item text is `pf_items()`. This file gives the reason each item exists, the failure it prevents, and tips beyond the helper. Severity: blocking = no launch until pass/n-a with reason; major = list in report if failed; minor = note.

Contents: A. Design and splits | B. Comparison and aggregation | C. Baselines and sample size | D. Metrics | E. Timing and cost | F. Launch and persistence | G. Provenance and config

## A. Design and splits

- **PF-01 Pre-declaration (blocking).** One primary outcome fixed before results; selection rule and data filters stated. Prevents outcome switching and code that reproduces a number hinted in the brief, and designs that lose the real test arm. The plan hash is stored; any later edit makes the verdict NO-GO, so a changed outcome must be a new, visible record.
- **PF-02 Multiple comparisons (major).** Declare the family size and correction, or label results exploratory. Cheap to state; expensive to retrofit after ranking many arms.
- **PF-03 Split unit (blocking).** When labels are constant within a unit (donor, patient, document, site), a sample-level random split lets the classifier memorise the unit. `pf_check_grouped_split` needs a sample-id -> unit map; build it from verified metadata.
- **PF-04 ID integrity (blocking).** A remediation control is only as good as the unit map: apparent batch levels may be repeat samples from far fewer subjects, with IDs reused across sources. Count distinct subjects per assumed level; namespace IDs by source.
- **PF-05 Nesting (major).** If A deterministically sets B, 'overlap' between A and B is arithmetic and holding out A holds out B. Use `allow=` to record intended nesting.
- **PF-06 Selection (blocking).** Choosing the best hyperparameter on a run's own curve and then reporting seed-aggregated CIs there is winner's curse. Select on selection seeds/units; report on fresh ones.

## B. Comparison and aggregation

- **PF-07 Matched comparison (blocking).** Arms must get equal tuning budget, selection rule, lambda policy. Also check that arms differ only in the factor under test: harness/scaffolding vs model capability, modality coverage, forced common width vs published defaults. If forcing equality changes comparability to published results, record that trade-off.
- **PF-08 Coverage-balanced aggregation (blocking).** A method covering 3 of 5 datasets is not comparable to ones covering 5 in a pooled mean. Pool over `common_units` or show per-unit. A non-computable value stays missing, never 1.0 or 0.

## C. Baselines and sample size

- **PF-09 No-op baselines (blocking).** A knob's neutral setting must be an identity on the production data; a 'zero-intensity' baseline can differ from the raw input, and a supposedly neutral resampling or dispersion setting can change the data substantially. Test on real data at full size.
- **PF-10 Sample size (blocking).** Perturbation or filtering can collapse n silently (orders of magnitude along one axis), so metrics end up comparing models fitted on very different n. Report n beside each metric; bound max/min n along the axis.

## D. Metrics

- **PF-11 Metric vs model structure (major).** Dimension-wise probes inflate for block-structured latents; continuous-score metrics misread concept-structured models; a mean-shift target is met by linear decoders by construction; positional pairing can misassign a shared subspace. A proxy substituted for an unavailable quantity (journal-level for paper-level citations) needs an in-table caveat.
- **PF-12 Metric implementation (blocking).** Known-answer tests cover: perfect/worst cases, the small-n regime (NaN below k < n/2 bound), sign/orientation against the source library, array orientation per library. Always >= 2 distinct known answers so a constant or flipped function cannot pass.
- **PF-13 Estimator noise (major).** A subsampled numerator can be mostly noise; report repeat-to-repeat spread and a shuffled-label null (a score 'below null' signals a metric/model mismatch).
- **PF-14 Test vs control (major).** Check that the planned parts include the actual test, not only controls; avoid circular validations (e.g. co-expression of a factor's own top loadings); a number that matches an expected value is not confirmation.
- **PF-15 Attribution (major).** Do not credit a model property for what a mechanism guarantees by construction (e.g. conditioning on a covariate removes it; any large embedding makes a factor findable). Name trivial mechanisms and include a control for each.

## E. Timing and cost

- **PF-16 Timing confounds (blocking).** Idle machine, load recorded, first call separated (JIT or warm-up can dominate a short timing), startup cost separated from steady state, no foreign processes and no self-interrupts. A contention multiplier must not be applied to the reference condition itself.
- **PF-17 Cost estimate (blocking).** Pilots at diagnostic settings (fewer epochs, smaller data) can under-estimate production cost by an order of magnitude; hand-profiled shares and factors copied from another component are unreliable; a quoted runtime must agree with the manifest's own makespan. Use profiler measurements at production settings, a safety factor, and walltime per task.
- **PF-18 Co-scheduling (major).** Memory sized for one process does not hold when several lanes run concurrently; balancing lanes by runtime alone ignores memory; exemptions by name must be checked against true sizes. Measure footprint and use true counts.

## F. Launch and persistence

- **PF-19 Launch manifest (blocking).** A manifest rewrite can silently drop a flag (e.g. `--epochs`) so every shard runs the default. CLI booleans can arrive as truthy strings. Diff every row against the intended settings dict.
- **PF-20 Count reconciliation (major).** Count by running the generator, not by regex over source (commented-out blocks inflate counts); apply protocol exclusions before totalling.
- **PF-21 Replicates vary (major).** Seed loops over scripts that have no seed parameter yield bit-identical replicates.
- **PF-22 Output persistence (blocking).** Long waves can finish without saving the outputs that matter; late-stage code (early stopping, export) can kill tasks after training completes; failures recorded as rows with exit 0 get a done marker, so a resume skips them. Smoke-test the late stages and a forced failure.

## G. Provenance and config

- **PF-23 Provenance and inclusion (blocking).** A feature panel built from a combined reference without a source filter (species, language, modality) can carry a large share of out-of-scope items; an assumed data deposit may lack the needed modality. Compute a category per kept item and assert it.
- **PF-24 Config vs input (major).** Top-N larger than the filtered feature count silently falls back to a different procedure; guards added separately can disable each other; categorical values with dots break framework identifiers; one fixed orientation assumed across libraries.

## Suggested applicability shortcuts

- Pure descriptive analysis without models: PF-01, 02, 04, 08, 10, 12, 23 usually apply.
- Hyperparameter sweeps: add PF-06, 07, 17, 19, 20, 21, 22.
- Timing/scaling studies: PF-16, 17, 18 and 10.
- New metric: PF-11, 12, 13, 14.
