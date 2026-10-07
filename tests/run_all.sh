#!/usr/bin/env bash
# Run every skill's incident-derived test suite against the kernel.py in this repo.
# Usage: bash tests/run_all.sh   (from the repo root; needs python with numpy, pandas, pillow; tectonic optional for doc-build-gate)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
declare -A K=( [fail-loud-pipelines]="fl_kernel.py" [claim-gate]="claim-gate_kernel.py" [experiment-preflight]="kernel.py pfwork/kernel.py" \
  [experiment-preflight-singlecell]="scpf_kernel.py" [standing-instructions]="skill_build/kernel.py" [doc-build-gate]="kernel.py" [run-status-board]="rsb_kernel.py" )
fail=0
for n in "${!K[@]}"; do
  d=$(mktemp -d); for rel in ${K[$n]}; do mkdir -p "$d/$(dirname "$rel")"; cp "$ROOT/$n/kernel.py" "$d/$rel"; done
  [ "$n" = standing-instructions ] && cp "$ROOT/lab-notebook/kernel.py" "$d/skill_build/nb_kernel.py"
  cp "$ROOT/tests/${n}_tests.py" "$d/t.py"
  if (cd "$d" && python t.py > out.txt 2>&1); then echo "PASS $n: $(tail -1 "$d/out.txt")"; else echo "FAIL $n"; tail -20 "$d/out.txt"; fail=1; fi
done
if python -c "import cairosvg, lxml, pypdf" 2>/dev/null; then
  if python "$ROOT/tests/bio-diagram_tests.py" > /tmp/bd_out.txt 2>&1; then echo "PASS bio-diagram: $(tail -1 /tmp/bd_out.txt)"; else echo "FAIL bio-diagram"; tail -20 /tmp/bd_out.txt; fail=1; fi
else echo "SKIP bio-diagram (needs cairosvg, lxml, pypdf)"; fi
if python "$ROOT/tests/codex-offload_tests.py" > /tmp/co_out.txt 2>&1; then echo "PASS codex-offload: $(tail -1 /tmp/co_out.txt)"; else echo "FAIL codex-offload"; tail -20 /tmp/co_out.txt; fail=1; fi
if python "$ROOT/tests/codex-offload_loop_tests.py" > /tmp/col_out.txt 2>&1; then echo "PASS codex-offload loop: $(tail -1 /tmp/col_out.txt)"; else echo "FAIL codex-offload loop"; tail -20 /tmp/col_out.txt; fail=1; fi
exit $fail
