"""Disassemble a byte range of chronotales.exe .text with capstone."""
import sys
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

PATH = r"D:\Games\Chrono Tales\gbox\lm_en\chronotales.exe"
BASE = 0x400000
data = open(PATH, "rb").read()

def dis(start, end, label=""):
    print("=" * 78)
    print(f"{label}  file {start:#x} .. {end:#x}   VA {BASE+start:#x}")
    print("=" * 78)
    code = data[start:end]
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    for ins in md.disasm(code, BASE + start):
        raw = " ".join(f"{b:02x}" for b in ins.bytes)
        print(f"{ins.address:08x}  {raw:<24s} {ins.mnemonic:<8s} {ins.op_str}")

if __name__ == "__main__":
    a = int(sys.argv[1], 16)
    b = int(sys.argv[2], 16)
    dis(a, b)
