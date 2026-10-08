import os, time
n = 1300 * 1024 * 1024
b = bytearray(n)
for i in range(0, n, 4096): b[i] = 1          # touch every page so RSS is real
time.sleep(3)
open("result.txt", "w").write("ok mem_per_node=%s MB, touched %d MB\n" % (os.environ.get("SLURM_MEM_PER_NODE"), n >> 20))
