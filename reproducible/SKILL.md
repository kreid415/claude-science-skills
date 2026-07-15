---
name: reproducible
description: Make a Claude Science project's results auditable and verifiable by a human scientist, and scaffold a git-ready repository to hold them. Use this whenever the user wants to audit, verify, or fact-check results; make outputs traceable/reproducible; produce a provenance record or verification bundle; check that reported numbers actually come from the artifacts; prepare work for peer review or a reviewer; or commit a project to GitHub as a clean data-gather + src + experiments + results + paper tree. Trigger it on phrases like "audit my results", "make this verifiable", "can a reviewer check this", "where did this number come from", "trace every figure to its source", "prove these numbers are real", or "organize this project for reproducibility" — even when the user does not say the word "reproducible".
---
# Reproducible & Auditable Results

A result a human scientist cannot **trace**, **re-run**, **inspect**, or **challenge** is not
reproducible — it is merely asserted. This skill turns a Claude Science project's outputs into a bundle
a reviewer can check in one sitting, and (optionally) lays it inside a git-ready repository.

The leverage that makes this cheap in Claude Science: every artifact carries **lineage** —
`host.lineage[version_id]` returns the exact `code` that produced it, the `env` it ran in, its resolved
`inputs`, a content `checksum`, and the `frame_id` / `producing_cell_id` that made it. You are not
reconstructing provenance by hand; you are surfacing what the platform already recorded and rendering it
so a person can read it. **Never hand-type a reported number, seed, or artifact ID from memory** — read
it back from the artifact or its lineage, because the whole point of an audit is that the paperwork
matches the files.

## The things a human auditor must be able to do

| Capability | Deliverable | The reviewer's question it answers |
|---|---|---|
| **Trace** | `audit/traceability.csv` | "This figure claims r=0.83 — which file and which line produced that?" |
| **Re-run** | `audit/verify.py` | "If I run it again from the committed inputs, do I get the same numbers?" |
| **Inspect** | `audit/provenance/*.md` | "What exactly went in — which inputs, which environment, which seed?" |
| **Challenge** | `audit/DECISIONS.md` | "Why this threshold? Why were these samples dropped? Why this method?" |
| **Conclude** | `audit/FINDINGS.md` | "So did it hold up? What broke, what's unverifiable, what should I doubt?" |

`audit/AUDIT.md` is the human's front door: it states **coverage** (how many claims were checked, how
many passed) and links every deliverable, in the order to read them. Build all of them — they reinforce
each other. A traceability row is only trustworthy because `verify.py` re-derives it; a re-run is only
interpretable because `DECISIONS.md` explains the choices baked into it; and none of it means anything to
a busy reviewer until `FINDINGS.md` says **what the audit concluded**.

**An audit must conclude, not just organize.** A tidy folder that never states a verdict is a filing
system, not an audit. Every bundle ends in a findings report that lists — with severity — each
discrepancy (prose says 0.83, artifact says 0.81), each untraceable claim, each stochastic result with no
pinned seed, and each `verify.py` check that failed. If everything checks out, say so explicitly and show
the coverage that earns that statement. **Never resolve a discrepancy by editing one side to match the
other** — surface both values and let the scientist decide; silently "correcting" prose to match an
artifact (or vice-versa) destroys the exact signal the audit exists to find.

## When to use
- **Audit-first** (the common trigger): the user wants to verify, fact-check, or hand results to a
  reviewer. Build the `audit/` bundle from the project's artifacts and lineage. This works standalone —
  no repository required.
- **Repo-first**: the user wants the project committed to GitHub / organized for reproducibility. Build
  the standard tree below and drop the `audit/` bundle inside it.
Offer the audit bundle whenever a project has produced numbers, figures, or tables a person will later
rely on — it is the house convention for "results someone can trust."

## Helper functions (loaded with this skill)
A `kernel.py` sidecar defines these in your `python` kernel the moment this skill loads:
- `provenance_record(version_id)` → dict pulled from `host.lineage`: filename, producing frame/cell,
  environment, inputs (with version IDs), detected RNG seeds, and the reproduction code.
- `render_provenance_md(records)` → markdown for one record or a list (collapsible code block per result).
- `scan_seeds(code)` → list of RNG-seed / `random_state` lines found in source (flags stochastic results
  that pin no seed — an audit red flag).
- `write_traceability(rows, path)` → writes a claim→artifact CSV with the canonical columns.
- `write_manifest(root, path)` → walks a directory and writes a `MANIFEST.sha256` (sorted `sha256  relpath`
  lines) so a reviewer can confirm nothing drifted since the audit; skips the manifest itself and VCS/junk.

Use them; don't re-derive the formatting each time. If a name is already taken by another loaded skill,
the load report gives you the aliased form to call.

---

## Audit workflow

**1. Inventory what the project claims and what produced it.**
In the `python` tool, list the project's real outputs and resolve each to a `latest_version_id`:
```python
arts = host.artifacts()                          # this project, newest first
# or target the deliverables you mean to audit:
figs   = host.artifacts(content_type="image/png")
tables = host.artifacts(filename=".csv")
```
For the write-up whose numbers you are auditing (a report `.md`, a paper, a memo), read it and list
**every** load-bearing claim — each reported statistic, each figure's headline result, each table. Count
them: the total is your **denominator**. An audit that traces 3 of 10 claims while staying silent about
the other 7 looks thorough but is cherry-picked. Track how many you trace, re-run, and cannot verify, and
report that fraction in `AUDIT.md` — the coverage number is what tells a reviewer how much of the work you
actually stand behind.

**2. Trace every claim to an artifact + its source.**
For each claim, find the artifact that carries the value and the lineage that produced it. Build rows and
write the matrix:
```python
rows = [
    {"claim": "Spearman r between dose and response",
     "value": "0.83",
     "artifact_version_id": corr_csv_vid,          # where the number LIVES
     "source": f"frame {lin['frame_id']} cell {lin['producing_cell_id']}",
     "verify": "python audit/verify.py --check corr"},
    # ...one row per load-bearing claim
]
write_traceability(rows, "audit/traceability.csv")
```
The `value` column must be **read from the artifact**, not the prose — if they disagree, that is exactly
the discrepancy an audit exists to catch. Report mismatches to the user; do not silently "correct" either
side.

**3. Emit a provenance record per key result.**
```python
records = [provenance_record(vid) for vid in key_vids]
open("audit/provenance/records.md", "w").write(render_provenance_md(records))
```
Each record shows inputs (so a reviewer sees what went in), the environment (so they can rebuild it),
seeds (so a stochastic result is either pinned or flagged), and the code (collapsed). For inputs that are
external downloads, record the **source URL and a checksum** so a reviewer can confirm they fetched the
same bytes — compute it if lineage did not:
```python
import hashlib
sha = hashlib.sha256(open(path,"rb").read()).hexdigest()[:16]
```
Two rules strengthen the Inspect deliverable:
- **No figure without its source data.** Every committed figure ships the CSV/table of the exact numbers
  behind it (a reviewer checks a bar height or re-plots without reverse-engineering a PNG). If a figure's
  numbers live only inside plotting code, extract them to `results/tables/` and trace them like any other
  claim.
- **Capture the environment that actually ran, not just a spec.** `environment.yml` names intended
  versions; also record the resolved truth — `pip freeze` / `conda list --explicit` from the env in each
  provenance record's `environment` field. And warn where exact reproduction is not guaranteed: GPU/CUDA
  kernels are often nondeterministic even with every seed pinned, so a seeded ML result may re-derive only
  to a tolerance, not bit-for-bit — say so rather than promising more than the hardware delivers.

**4. Build the re-run harness.**
`audit/verify.py` regenerates the load-bearing numbers from committed inputs and asserts they match the
committed results within a stated tolerance. Structure it so a human runs one command:
```python
# audit/verify.py  (sketch)
import argparse, pandas as pd, numpy as np
def check_corr():
    got = recompute_corr(load_inputs())          # imports the project's src/ library
    want = float(pd.read_csv("results/tables/corr.csv")["r"][0])
    assert abs(got - want) < 1e-3, f"corr drift: recomputed {got} vs committed {want}"
    print(f"OK  corr: {got:.4f} == {want:.4f} (tol 1e-3)")
# ...register one check per traceable claim; `--check all` runs them
```
Pin a tolerance and **say why** (float noise vs. a genuinely stochastic step). If a result is inherently
stochastic, verify a seeded run reproduces exactly, or verify a summary statistic lies in an expected
interval — and note which you chose. Run `verify.py` yourself before handing it over; a harness that does
not pass is worse than none.

**5. Log the decisions.**
`audit/DECISIONS.md` records the judgment calls a reviewer would otherwise have to reverse-engineer:
every threshold (and what the result looks like at nearby values, if you checked), every excluded
sample/outlier (with the rule that excluded it, not a post-hoc reason), every method choice over an
obvious alternative, and every place the data forced a compromise. State the rationale, not just the
choice — a reviewer challenges reasoning, not values.

**6. Show robustness, where a result rests on a choice.** A reviewer's next question after "is the number
right?" is "would it survive a small perturbation?" For the load-bearing conclusions, record short
robustness evidence and cite it in `FINDINGS.md`:
- a **negative control** (permutation / label shuffle) — does the effect vanish when it should?
- a **second seed** for any stochastic result — does the conclusion (not the exact value) hold?
- a **threshold sweep** (±~20% around any hand-chosen cutoff) — is the finding an artifact of the cutoff?
This edges from auditing into light re-analysis; do it for the headline claims, not every number, and be
explicit that it is a robustness check, not a re-derivation.

**7. Write the findings — `audit/FINDINGS.md`.** The verdict. List each issue with a severity so a
reviewer triages at a glance:
- **BLOCKER** — a reported number has no traceable source, or `verify.py` disagrees with a committed
  result beyond tolerance.
- **CONCERN** — a stochastic result pins no seed; a conclusion flips under a second seed or a threshold
  nudge; a figure ships without its source data.
- **NOTE** — cosmetic or low-stakes (rounding in prose, a missing unit).
For each: what you expected, what you found (both values, read from the sources), and where
(`traceability.csv` row / provenance record). Open with a **coverage line** ("14 claims; 13 traced, 12
re-run green, 1 untraceable") and a one-line **overall verdict**. If nothing is wrong, say that plainly
and let the coverage number back it up — a clean audit is a real and valuable result.

**8. Write `audit/AUDIT.md`.** The reviewer's entry point:
- the **coverage line + overall verdict** at the very top (mirror `FINDINGS.md`), so a reviewer knows in
  one glance how much was checked and whether it held,
- a one-paragraph orientation (what was done, what to trust it for),
- a numbered "how to verify in 10 minutes" checklist (rebuild env → `python audit/verify.py --check all`
  → spot-check N traceability rows → skim `FINDINGS.md` and `DECISIONS.md`),
- the traceability table (inline or linked),
- links to the findings, provenance records, decisions log, and `MANIFEST.sha256`.

**9. Save the bundle.** Write `MANIFEST.sha256` last (`write_manifest("audit")`, or over the repo root)
so the checksums cover the final files, then `save_artifacts` the `audit/` files. If there is no repo,
this bundle *is* the deliverable; embed the coverage line and `FINDINGS.md`'s verdict in your reply and
link the files.

---

## Repo scaffold (the container, when the user wants one)

Create a standard, git-ready layout so data-gathering code, experiment source, run scripts, results,
the audit bundle, and the write-up all live in one committable tree.

```
<repo>/
  README.md            layout + reproduce steps
  EXPERIMENTS.md       status-tracked index ([x] done / [~] running / [ ] planned)
  environment.yml      conda env pinned to the versions the project ACTUALLY used
  .gitignore           ignore data/weights/*.npz/*.pkl/*.pt/hf_cache; KEEP results + audit
  data/gather/         code that fetches/builds datasets (one script per source)
  src/                 shared library imported by experiments (pip install -e .)
  experiments/NN_name/ per experiment: source + run.sh + README.md (= its synthesis memo)
  scripts/             gather_data.sh, submit_cluster.sh (remote submit wrapper)
  results/figures/     regenerated figures (small, committed)
  results/tables/      regenerated tables/CSVs (small, committed)
  audit/               AUDIT.md, FINDINGS.md, traceability.csv, provenance/, DECISIONS.md,
                       verify.py, MANIFEST.sha256
  paper/main.tex       LaTeX write-up; sections/*.tex; refs.bib; \input's results/figures
```

**Workflow**
1. **Inventory artifacts.** `host.artifacts(search=...)` / `host.artifacts(filename=..., exact=True)` to
   find code files (`*.py`) and synthesis memos (`.md`); resolve each to a `latest_version_id`.
2. **Build the tree** with `os.makedirs`, then `shutil.copy(host.artifact_path(vid), dest)` each real code
   file into `data/gather/` or the right `experiments/NN_*/` folder — never write empty stubs when real
   code exists.
3. **Seed per-experiment READMEs from the synthesis memos** so inputs, commands, key numbers, and
   artifact IDs travel with the code.
4. **Write top-level docs** (README, EXPERIMENTS.md with real status, `environment.yml` with the versions
   actually used, `.gitignore`), each `run.sh`, and `scripts/`.
5. **Build the `audit/` bundle** (above) and place it at the repo root.
6. **Seed `paper/`**: `main.tex` with one `\section` per experiment, `sections/*.tex` summarizing the
   completed ones, `refs.bib` with the methods actually cited. Use `\graphicspath{{../results/figures/}}`.
7. **git-init + commit**: `git init -q; git add -A; git commit -m ...`.
8. **Package**: `tar czf <repo>.tar.gz <repo>` and `save_artifacts` the tarball. Offer to push to a new
   GitHub repo via `GITHUB_TOKEN` (gh CLI / PyGithub) if the user wants it remote.

## Library + tests (do this, don't skip it)
The scaffold is not a dump of one-off scripts. Refactor shared logic into an installable, documented,
tested library, and make experiment scripts and `audit/verify.py` thin wrappers that import it — that is
what lets the re-run harness actually re-derive results *through the same code path*, which is the proof
a reviewer needs.
- **Functional:** extract repeated logic (data stats, pooling, probes, models, metrics) into a package
  under `src/`; prefer pure, typed functions with explicit inputs/outputs. `pyproject.toml` makes it
  `pip install -e .`-able (with `[test]` and `[experiments]` extras).
- **Documented:** every public function carries a docstring with parameters, returns, and a worked
  example. Collect modules under pytest `--doctest-modules` so the examples are executed.
- **Tested:** a `tests/` suite with per-module unit tests on synthetic data with known structure, plus an
  **end-to-end regression test** that reproduces a committed result *through the refactored library* and
  asserts equality within tolerance (proof a refactor preserved behaviour, not just a claim it did).
  `audit/verify.py` can call these, or share their recompute functions.
- Verify green (`pytest`) BEFORE committing. `.gitignore` build metadata (`*.egg-info/`,
  `.pytest_cache/`, `__pycache__/`).

## Notes
- `.gitignore` must KEEP `results/` and `audit/` (they ARE the verifiable output) while ignoring bulk data
  and model weights — those are re-fetchable via `data/gather/`.
- Pin `environment.yml` to versions the project used (e.g. `transformers==4.44.2`), not "latest" — a
  re-run that silently upgrades a dependency is not a reproduction.
- EXPERIMENTS.md should carry any standing open question the user is tracking, so the write-up target is
  explicit.
- For remote/cluster experiments, `scripts/submit_cluster.sh` records the venv-activation + offline-HF env
  so the run is reproducible on the same host, and the provenance record should name the host and job ID.
