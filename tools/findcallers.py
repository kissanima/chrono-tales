"""Find call/jmp sites targeting a given VA in chronotales.exe .text."""
import sys, re, struct
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

PATH = r"D:\Games\Chrono Tales\gbox\lm_en\chronotales.exe"
BASE = 0x400000
data = open(PATH, "rb").read()
TEXT_START, TEXT_END = 0x1000, 0x2ce000

def find_callers(target):
    hits = []
    for o in range(TEXT_START, TEXT_END - 5):
        if data[o] == 0xE8:
            rel = struct.unpack_from("<i", data, o + 1)[0]
            if BASE + o + 5 + rel == target:
                hits.append(o)
    return hits

def dis(start, end):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    return [f"{i.address:08x}  {i.mnemonic:<8s} {i.op_str}"
            for i in md.disasm(data[start:end], BASE + start)]

if __name__ == "__main__":
    t = int(sys.argv[1], 16)
    ctx = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x50
    hits = find_callers(t)
    print(f"callers of {t:#x}: {len(hits)}")
    for o in hits:
        print("-" * 60)
        for line in dis(max(TEXT_START, o - ctx), o + 0x10):
            print("   ", line)
