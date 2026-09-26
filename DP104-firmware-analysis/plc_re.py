"""PLC firmware disassembly helpers (Cortex-M4, image base 0x08008000).

Usage: python plc_re.py <addr> [count]      disassemble count instructions
"""
import struct, sys
import capstone
from capstone import Cs

BASE = 0x08008000
with open('DP104_plc_v1.2.0.bin', 'rb') as f:
    D = f.read()
END = BASE + len(D)

md = Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

def dis(addr, count=80):
    addr &= ~1
    if not (BASE <= addr < END):
        print('! 0x%08X outside image 0x08008000-0x%08X' % (addr, END))
        return
    lines = []
    for ins in md.disasm(D[addr-BASE: END-BASE], addr):
        line = '0x%08X  %-8s %s' % (ins.address, ins.mnemonic, ins.op_str)
        if ins.mnemonic == 'ldr' and '[pc' in ins.op_str:
            try:
                pc = (ins.address + 4) & ~3
                imm = int(ins.op_str.split('#')[-1].rstrip(']'), 16)
                la = pc + imm
                if BASE <= la < END:
                    v = struct.unpack_from('<I', D, la-BASE)[0]
                    line += '   ; lit=0x%08X' % v
            except Exception:
                pass
        lines.append(line)
        if len(lines) >= count:
            break
    print('\n'.join(lines))

def bls_to(addr):
    """Find all BL call sites targeting addr (anywhere in image)."""
    res = []
    n = len(D)
    o = 0
    while o + 4 <= n:
        hw1, = struct.unpack_from('<H', D, o)
        if (hw1 & 0xF800) == 0xF000:
            hw2, = struct.unpack_from('<H', D, o+2)
            if (hw2 & 0xD000) == 0xD000:
                S = (hw1 >> 10) & 1
                imm10 = hw1 & 0x3FF
                J1 = (hw2 >> 13) & 1
                J2 = (hw2 >> 11) & 1
                imm11 = hw2 & 0x7FF
                I1 = (~(J1 ^ S)) & 1
                I2 = (~(J2 ^ S)) & 1
                off = (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1)
                if S: off -= 1 << 25
                site = BASE + o
                tgt = (site + 4 + off) & 0xFFFFFFFF
                if (tgt & ~1) == (addr & ~1):
                    res.append(site)
        o += 2
    return res

if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd.startswith('0x'):
        dis(int(cmd, 16), int(sys.argv[2]) if len(sys.argv) > 2 else 80)
    elif cmd == 'callers':
        for site in bls_to(int(sys.argv[2], 16)):
            print('0x%08X' % site)
