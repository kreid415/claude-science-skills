Cluster smoke workloads for phase-delegation (2026-10-08, JHPCE shared). jw.py comes from job-watch/scripts/jw.py (sha256 f19836455ae7c947...).
run_cluster_pd.sh + app.py: passing run (two job-watch units, A killed then resumed, B stalled then retried).
run_fail_pd.sh + app2.py: deliberately failing run (params.json not shipped); fix = add params.json {"total": 20} and `cp app2.py params.json`.
