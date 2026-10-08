#!/bin/bash
#SBATCH --partition=shared
#SBATCH --account=jhpce
#SBATCH --cpus-per-task=1
#SBATCH --time=00:15:00
#SBATCH --mem=1G
#SBATCH --job-name=pdsmoke
# phase-delegation cluster smoke test: two job-watch supervised local-kind runs inside one SLURM job.
# A: attempt 1 is SIGKILLed mid-run, attempt 2 resumes from checkpoint (JW_RESUME=1) and finishes.
# B: attempt 1 hangs silently, stall kill, attempt 2 finishes.
set -u
JWSRC=$PWD/jw.py; APP=$PWD/app.py; OUT=$PWD/out; mkdir -p "$OUT"
BASE=${PD_BASE:-/fastscratch/myscratch/$USER}; R=$BASE/pdsmoke_${SLURM_JOB_ID:-local$$}; mkdir -p $R/A $R/B $R/bin
cp "$JWSRC" $R/bin/jw.py; cp "$APP" $R/A/; cp "$APP" $R/B/; JW=$R/bin/jw.py
python3 --version > "$OUT/python_version.txt" 2>&1
python3 $JW start --state $R/A/state --kind local --workdir $R/A --cmd 'TOTAL=40 python3 app.py & P=$!; if [ "$JW_ATTEMPT" = 1 ]; then sleep 12; kill -9 $P; fi; wait $P' --expect result.txt --retries 2 --tag A
python3 $JW start --state $R/B/state --kind local --workdir $R/B --cmd 'if [ "$JW_ATTEMPT" = 1 ]; then echo begin; sleep 300; else TOTAL=15 python3 app.py; fi' --expect result.txt --stall-min 0.2 --retries 1 --tag B
python3 $JW watch --state $R/A/state --interval 3 --deadline-min 8 > $R/A.out 2>&1 & PA=$!
python3 $JW watch --state $R/B/state --interval 3 --deadline-min 8 > $R/B.out 2>&1 & PB=$!
wait $PA; RA=$?; wait $PB; RB=$?
for k in A B; do cp $R/$k/result.txt "$OUT/${k}_result.txt" 2>/dev/null; cp $R/$k/state/state.json "$OUT/${k}_state.json"; cp $R/$k/state/result.json "$OUT/${k}_result.json" 2>/dev/null; tail -1 $R/$k.out > "$OUT/${k}_watch_last_line.txt"; done
echo "watch_exit A=$RA B=$RB" > "$OUT/exit_codes.txt"; echo "$R" > "$OUT/where.txt"
[ $RA -eq 0 ] && [ $RB -eq 0 ]
