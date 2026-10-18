#!/usr/bin/env python3
"""PE-aware x86 disassembler for chronotales.exe.
Maps an RVA to a file offset using section headers, then disassembles."""
import struct, sys

EXE = "gbox/lm_en/chronotales.exe"

def pe_sections(data):
    dos = struct.unpack_from("<H", data, 0)[0]
    assert dos == 0x5A4D, "not MZ"
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    magic = data[e_lfanew:e_lfanew+4]
    if magic[:2] == b"PE":
        pe = e_lfanew + 4
    else:
        pe = e_lfanew
    # coff header
    machine = struct.unpack_from("<H", data, pe)[0]
    is64 = (machine == 0x8664)
    opt_off = pe + 20
    opt_magic = struct.unpack_from("<H", data, opt_off)[0]
    if opt_magic == 0x20B:  # PE32+
        num_sect = struct.unpack_from("<H", data, pe + 2)[0]
        # optional header size at pe+16
        oh_size = struct.unpack_from("<H", data, pe + 16)[0]
        sect_off = opt_off + oh_size
        image_base = struct.unpack_from("<Q", data, opt_off + 24)[0]
    else:  # PE32
        num_sect = struct.unpack_from("<H", data, pe + 2)[0]
        oh_size = struct.unpack_from("<H", data, pe + 16)[0]
        sect_off = opt_off + oh_size
        image_base = struct.unpack_from("<I", data, opt_off + 28)[0]
    sects = []
    for i in range(num_sect):
        base = sect_off + i * 40
        name = data[base:base+8].split(b"\x00")[0].decode("latin1", "replace")
        vsize = struct.unpack_from("<I", data, base + 8)[0]
        vaddr = struct.unpack_from("<I", data, base + 12)[0]
        rsize = struct.unpack_from("<I", data, base + 16)[0]
        roff = struct.unpack_from("<I", data, base + 20)[0]
        sects.append((name, vaddr, vsize, roff, rsize))
    return is64, image_base, sects

def rva_to_off(sects, rva):
    for name, vaddr, vsize, roff, rsize in sects:
        if vaddr <= rva < vaddr + max(vsize, rsize):
            return roff + (rva - vaddr)
    return None

def main():
    data = open(EXE, "rb").read()
    is64, base, sects = pe_sections(data)
    print(f"machine={'x64' if is64 else 'x86'} image_base={base:#x}")
    for s in sects:
        print(f"  {s[0]:8} va={s[1]:#x} vsize={s[2]:#x} raw={s[3]:#x} rsize={s[4]:#x}")
    # target RVA from arg
    rva = int(sys.argv[1], 0)
    off = rva_to_off(sects, rva)
    print(f"RVA {rva:#x} -> file offset {off:#x}")
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32 if not is64 else capstone.CS_MODE_64)
    md.detail = True
    n = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x600
    chunk = data[off: off + n]
    # align to instruction? just disassemble linearly
    for ins in md.disasm(chunk, rva):
        # show referenced constants
        print(f"{ins.address:#08x}  {ins.bytes.hex():20}  {ins.mnemonic} {ins.op_str}")

if __name__ == "__main__":
    main()
