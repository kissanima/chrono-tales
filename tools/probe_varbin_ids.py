#!/usr/bin/env python3
"""Probe: do the int32 values inside netpak's `varbin` blobs reference netpak entries?

The varbin blobs are UTF-16LE entity tables (NPCs, monsters, drop tables) with
int32 fields interleaved.  If any of those int32s equal a netpak entry nameHash,
the entity table is pointing at a model/texture file -- which would finally give
us names for the 146 hash-only netpak entries.
"""
import glob
import os
import re
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gpak import GpakArchive  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NETPAK = os.path.join(ROOT, "gbox", "lm_en", "netpak.gpak")
UNK = os.path.join(ROOT, "extracted", "netpak", "unknown")


def main():
    arch = GpakArchive(NETPAK)
    hashes = {}
    for e in arch.entries:
        hashes.setdefault(e.name_hash, []).append(e.index)
    print(f"netpak entries: {len(arch.entries)}, distinct hashes: {len(hashes)}")

    zips = sorted(glob.glob(os.path.join(UNK, "*.zip")))
    print(f"varbin bundles: {len(zips)}")

    hit_vals = {}
    total_ints = 0
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            if not names:
                continue
            d = zf.read(names[0])
        # walk every 4-byte-aligned offset for a cheap sweep
        for off in range(0, len(d) - 4, 4):
            (v,) = struct.unpack_from("<I", d, off)
            total_ints += 1
            if v in hashes:
                hit_vals.setdefault(v, []).append((os.path.basename(z), off))

    print(f"scanned {total_ints} int32 slots")
    print(f"distinct netpak hashes referenced: {len(hit_vals)}")
    for v, where in sorted(hit_vals.items(), key=lambda kv: -len(kv[1]))[:20]:
        print(f"  0x{v:08X}  {len(where):5d} refs  first={where[0]}")

    # also test: are the ints maybe hashes of the *strings* in the same blob?
    print("\n-- string-derived hash test --")
    from gpak import hash_name
    cand = set()
    for z in zips[:10]:
        with zipfile.ZipFile(z) as zf:
            d = zf.read("varbin")
        s = d.decode("utf-16-le", "ignore")
        for m in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_ \-\.]{3,60}", s):
            cand.add(m)
    print(f"sample strings: {len(cand)}")
    matched = 0
    for name in cand:
        for variant in (name, name.lower(), name + ".jpg", name + ".png",
                        "model/" + name, "texture/" + name):
            if hash_name(variant) in hashes:
                print("  MATCH", variant, "->", f"{hash_name(variant):08X}")
                matched += 1
    print(f"string hash matches: {matched}")


if __name__ == "__main__":
    main()
