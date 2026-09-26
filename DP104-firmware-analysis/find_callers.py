import struct, sys
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
with open('DP104_plc_v1.2.0.bin','rb') as f: d = f.read()
BASE = 0x08008000
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
targets = [int(x,16) for x in sys.argv[1:]]
callers = {t: [] for t in targets}
code_start, code_end = 0x08008400, 0x080188FE
a = code_start
while a < code_end:
    seg = min(a+0x1000, code_end)
    for ins in md.disasm(d[a-BASE:seg-BASE], a):
        if ins.mnemonic in ('bl','blx') and ins.op_str.startswith('0x'):
            t = int(ins.op_str,16)
            if t in callers:
                callers[t].append(ins.address)
    a = seg
for t, cs in callers.items():
    print('callers of 0x%08X:' % t, [hex(c) for c in cs])
