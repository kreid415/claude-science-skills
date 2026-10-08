import os, json, time
cfg = json.load(open("params.json"))          # run_fail_pd.sh forgets to ship params.json
total = int(cfg["total"]); done = 0
while done < total:
    time.sleep(2); done += 5
open("result.txt", "w").write("done %d\n" % done)
