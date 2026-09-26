import struct, sys
with open('DP104_plc_v1.2.0.bin','rb') as f: d = f.read()
BASE = 0x08008000

def u16(o): return struct.unpack_from('<H', d, o)[0]

def bl_targets_from(off):
    """Decode all Thumb BL pairs starting at even offsets; return dict target->[addr]"""
    res = {}
    n = len(d)
    o = off
    while o + 4 <= n:
        hw1 = u16(o)
        if (hw1 & 0xF800) == 0xF000:
            hw2 = u16(o+2)
            if (hw2 & 0xC000) == 0xD000 and (hw2 & 0x1000):
                S = (hw1 >> 10) & 1
                imm10 = hw1 & 0x3FF
                J1 = (hw2 >> 13) & 1
                J2 = (hw2 >> 11) & 1
                imm11 = hw2 & 0x7FF
                I1 = (~(J1 ^ S)) & 1
                I2 = (~(J2 ^ S)) & 1
                off25 = (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1)
                if S:
                    off25 -= 1 << 25
                addr = BASE + o
                target = (addr + 4 + off25) & 0xFFFFFFFF
                res.setdefault(target, []).append(addr)
        o += 2
    return res

if __name__ == '__main__':
    all_bl = bl_targets_from(0)
    for arg in sys.argv[1:]:
        t = int(arg, 16)
        # try both with and without thumb bit (callers compute to odd addr)
        hits = all_bl.get(t, []) + all_bl.get(t | 1, [])
        print('callers of 0x%08X:' % t, ['0x%08X' % c for c in hits])
