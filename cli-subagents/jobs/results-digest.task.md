# Task: digest of changed benchmark results (read-only bulk reading)

You are reading result files only. You cannot run commands and must not try to change anything.

Files to read: the list in `{changed_files_path}` ({changed_count} files, one absolute path per line). These are the files under
/home/kendall/disentanglement-benchmarking/results and /home/kendall/disentanglement-benchmarking/local_run/lab_pathway_results that changed since {since} (UTC; "never" = first run, so this is every file).

What to produce, as the `result` of your closing declaration (a single markdown string; Claude Science reads only this):
1. One section per result file or group of closely related files (same table or run): the absolute path(s), what the file is, and the exact numbers that matter (metric names with values, counts, row and column counts, min/max, any NaN or empty values). Copy numbers exactly as they appear; never round, estimate or recompute from memory.
2. Anything that looks wrong: empty files, truncated files, NaN or constant columns, values outside the plausible range, error or traceback lines in logs (quote the line and give path and line number).
3. A final list "Not read": every path from the list you did not open, or opened only in part, with the reason (binary, too large, unreadable). Write "none" only if you read every file in full.

Rules: facts only, each with its file path; if you are unsure what a number means, say what the file shows and say you are unsure. Do not draw scientific conclusions.
