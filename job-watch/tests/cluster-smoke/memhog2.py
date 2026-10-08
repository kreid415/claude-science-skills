import os, time
n = 2000 * 1024 * 1024
b = bytearray(n)
for i in range(0, n, 4096): b[i] = 1          # touch every page
print("touched", n >> 20, "MB; mem_per_node", os.environ.get("SLURM_MEM_PER_NODE"), flush=True)
for _ in range(18):                            # hold 90 s so periodic accounting can see it
    time.sleep(5)
open("result.txt", "w").write("ok mem_per_node=%s MB, touched %d MB\n" % (os.environ.get("SLURM_MEM_PER_NODE"), n >> 20))
