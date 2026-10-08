#!/bin/bash
#SBATCH --partition=shared
#SBATCH --account=jhpce
#SBATCH --cpus-per-task=1
#SBATCH --time=00:10:00
#SBATCH --mem=1G
#SBATCH --job-name=pdfail
set -u
OUT=$PWD/out; mkdir -p "$OUT"
BASE=${PD_BASE:-/fastscratch/myscratch/$USER}; R=$BASE/pdfail_${SLURM_JOB_ID:-local$$}; mkdir -p $R/A $R/bin
cp jw.py $R/bin/jw.py; cp app2.py $R/A/; JW=$R/bin/jw.py
python3 $JW start --state $R/A/state --kind local --workdir $R/A --cmd 'python3 app2.py' --expect result.txt --retries 1 --tag A
python3 $JW watch --state $R/A/state --interval 3 --deadline-min 5 > $R/A.out 2>&1; RA=$?
cp $R/A/state/state.json "$OUT/A_state.json"; cp $R/A/result.txt "$OUT/A_result.txt" 2>/dev/null
tail -1 $R/A.out > "$OUT/A_watch_last_line.txt"; echo "watch_exit A=$RA" > "$OUT/exit_codes.txt"; echo $R > "$OUT/where.txt"
exit $RA
