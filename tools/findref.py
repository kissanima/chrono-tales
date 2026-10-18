"""Find code that references a given VA (push imm32 / mov reg,imm32 / lea)."""
import sys, struct, re
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

PATH = r"D:\Games\Chrono Tales\gbox\lm_en\chronotales.exe"
BASE = 0x400000
data = open(PATH, "rb").read()

def find_refs(va):
    pat = struct.pack("<I", va)
    hits = []
    for m in re.finditer(re.escape(pat), data):
        o = m.start()
        # require it be inside .text raw range
        if 0x1000 <= o < 0x2ce000:
            hits.append(o)
    return hits

def dis(start, end):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    out = []
    for ins in md.disasm(data[start:end], BASE + start):
        out.append(f"{ins.address:08x}  {ins.mnemonic:<8s} {ins.op_str}")
    return out

if __name__ == "__main__":
    va = int(sys.argv[1], 16)
    ctx = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x40
    hits = find_refs(va)
    print(f"references to {va:#x}: {len(hits)}")
    for o in hits:
        print("-" * 70)
        print(f"  ref at file {o:#x} (VA {BASE+o:#x})")
        for line in dis(max(0x1000, o - ctx), o + 0x30):
            print("   ", line)
