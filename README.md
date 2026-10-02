# claude-science-skills

Personal Claude Science agent skills, one subfolder per skill.

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

## Layout

```
<skill-name>/
  SKILL.md          # frontmatter + agent guidance
  kernel.py         # optional helper functions (auto-loaded)
  references/       # optional detail files, read on demand
  scripts/          # optional standalone CLI tools
```

Files are kept in sync with the locally installed skill versions. Internal
catalog metadata (`.catalog_stamp`, `.sync-org`) is deliberately not
versioned.
