import os, sys, time, signal, json
ck = "ckpt.json"; total = int(os.environ.get("TOTAL", "100")); done = 0
if os.environ.get("JW_RESUME") == "1" and os.path.exists(ck): done = json.load(open(ck))["done"]
print("start seg", os.environ.get("JW_SEGMENT"), "resume", os.environ.get("JW_RESUME"), "from", done, flush=True)
stop = []
if os.environ.get("HANDLE") == "1": signal.signal(signal.SIGUSR1, lambda *a: stop.append(1))
while done < total:
    time.sleep(5); done += 5
    json.dump({"done": done}, open(ck + ".tmp", "w")); os.replace(ck + ".tmp", ck)
    print("progress", done, flush=True)
    if stop: print("USR1: checkpointed at", done, flush=True); sys.exit(3)
open("result.txt", "w").write("done %d\n" % done)
