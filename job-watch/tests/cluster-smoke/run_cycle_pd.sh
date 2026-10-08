#!/bin/bash
#SBATCH --partition=shared
#SBATCH --account=jhpce
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
#SBATCH --mem=1G
#SBATCH --job-name=jwcycle
# Controller job: job-watch submits and supervises child SLURM jobs.
#  O: OUT_OF_MEMORY at --mem 1G, expected resubmit at 1.5x
#  C: --cycle, USR1 checkpoint 60 s before a 2-min limit; limit must stay 2 min
#  T: --cycle, no signal, TIMEOUT at a 1-min limit; limit must stay 1 min; resumes with JW_RESUME=1
set -u
OUT=$PWD/out; mkdir -p "$OUT"
BASE=${PD_BASE:-/fastscratch/myscratch/$USER}; R=$BASE/jwcycle_${SLURM_JOB_ID:-x$$}; mkdir -p $R/bin $R/O $R/C $R/T
cp jw.py $R/bin/; cp memhog.py $R/O/; cp app.py $R/C/; cp app.py $R/T/; JW=$R/bin/jw.py
SB="--partition=shared --account=jhpce --cpus-per-task=1"
python3 $JW start --state $R/O/state --kind slurm --workdir $R/O --cmd "python3 memhog.py" --time 00:03:00 --mem 1G --mem-factor 1.5 --max-mem 4G --signal-mode none --expect result.txt --sbatch-args "$SB" --tag O
python3 $JW start --state $R/C/state --kind slurm --workdir $R/C --cmd "HANDLE=1 TOTAL=100 python3 app.py" --cycle --time 00:02:00 --mem 1G --signal-margin 60 --signal-mode sbatch --progress-file ckpt.json --expect result.txt --sbatch-args "$SB" --tag C
python3 $JW start --state $R/T/state --kind slurm --workdir $R/T --cmd "TOTAL=100 python3 app.py" --cycle --time 00:01:00 --mem 1G --signal-mode none --progress-file ckpt.json --expect result.txt --sbatch-args "$SB" --tag T
for k in O C T; do python3 $JW watch --state $R/$k/state --interval 10 --deadline-min 20 > $R/$k.out 2>&1 & eval "P$k=$!"; done
wait $PO; RO=$?; wait $PC; RC=$?; wait $PT; RT=$?
sleep 30   # let sacct catch up
for k in O C T; do
  mkdir -p "$OUT/$k"; cp -r $R/$k/state "$OUT/$k/state"; cp $R/$k/result.txt $R/$k/ckpt.json "$OUT/$k/" 2>/dev/null
  tail -1 $R/$k.out > "$OUT/$k/watch_last_line.txt"
  IDS=$(python3 -c "import json,sys;print(','.join(str(s['id']) for s in json.load(open('$R/$k/state/state.json'))['segments']))")
  echo "$IDS" > "$OUT/$k/job_ids.txt"
  sacct -j "$IDS" -P --format=JobID,JobName,State,ExitCode,Elapsed,Timelimit,ReqMem,MaxRSS,NodeList > "$OUT/$k/sacct.txt" 2>&1
done
echo "watch_exit O=$RO C=$RC T=$RT" > "$OUT/exit_codes.txt"; echo $R > "$OUT/where.txt"; python3 --version > "$OUT/python_version.txt" 2>&1
tar czf "$PWD/cycle_out.tar.gz" -C "$PWD" out
[ $RO -eq 0 ] && [ $RC -eq 0 ] && [ $RT -eq 0 ]
