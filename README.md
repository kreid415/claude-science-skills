# claude-science-skills

Personal Claude Science agent skills.

Each subfolder is a self-contained skill: a `SKILL.md` (frontmatter +
guidance) and a `kernel.py` sidecar of helper functions loaded into the
analysis kernel when the skill is activated.

## Skills

### `reproducible`
Make a project's results auditable and verifiable by a human scientist, and
scaffold a git-ready repository to hold them. Traces reported numbers back to
the artifacts that produced them, produces a provenance/verification bundle,
and organizes a project into a clean `data-gather + src + experiments +
results + paper` tree. Triggers on requests like "audit my results", "make
this verifiable", "can a reviewer check this", or "where did this number come
from".

### `cluster-autoscout`
Scan every connected SSH/SLURM cluster for available nodes and pick the best
partition + account for a job by immediate availability, then lowest wait. Use
before dispatching remote GPU/CPU compute to land the job on whichever
cluster/partition is least contended right now, and to spread jobs across
clusters. Complements the submit/harvest workflow in `remote-compute-ssh`.

## Layout

```
reproducible/
  SKILL.md
  kernel.py
cluster-autoscout/
  SKILL.md
  kernel.py
```
