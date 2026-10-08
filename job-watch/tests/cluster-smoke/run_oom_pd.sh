#!/bin/bash
#SBATCH --partition=shared
#SBATCH --account=jhpce
#SBATCH --cpus-per-task=1
#SBATCH --time=00:25:00
#SBATCH --mem=1G
#SBATCH --job-name=jwoom
# Controller: job-watch supervises one child that holds 2000 MB for 90 s under --mem 1G.
# Prediction: segment 1 (1 GB) and 2 (1.5 GB) end OUT_OF_MEMORY, segment 3 (2.25 GB) completes.
set -u
OUT=$PWD/out; mkdir -p "$OUT"
BASE=${PD_BASE:-/fastscratch/myscratch/$USER}; R=$BASE/jwoom_${SLURM_JOB_ID:-x$$}; mkdir -p $R/bin $R/O
cp jw.py $R/bin/; cp memhog2.py $R/O/; JW=$R/bin/jw.py
python3 $JW start --state $R/O/state --kind slurm --workdir $R/O --cmd "python3 memhog2.py" --time 00:04:00 --mem 1G --mem-factor 1.5 --max-mem 4G --signal-mode none --expect result.txt --sbatch-args "--partition=shared --account=jhpce --cpus-per-task=1" --tag O
python3 $JW watch --state $R/O/state --interval 10 --deadline-min 18 > $R/O.out 2>&1; RO=$?
sleep 30
mkdir -p "$OUT/O"; cp -r $R/O/state "$OUT/O/state"; cp $R/O/result.txt "$OUT/O/" 2>/dev/null
tail -1 $R/O.out > "$OUT/O/watch_last_line.txt"
IDS=$(python3 -c "import json;print(','.join(str(s['id']) for s in json.load(open('$R/O/state/state.json'))['segments']))"); echo "$IDS" > "$OUT/O/job_ids.txt"
sacct -j "$IDS" -P --format=JobID,JobName,State,ExitCode,Elapsed,Timelimit,ReqMem,MaxRSS,NodeList > "$OUT/O/sacct.txt" 2>&1
scontrol show config 2>/dev/null | grep -Ei 'JobAcctGather|TaskPlugin|ProctrackType|MemLimitEnforce|JobAcctGatherParams' > "$OUT/slurm_mem_config.txt"
echo "watch_exit O=$RO" > "$OUT/exit_codes.txt"; echo $R > "$OUT/where.txt"
tar czf "$PWD/oom_out.tar.gz" -C "$PWD" out
exit $RO
