#!/usr/bin/env python3
"""Unpack the 49 netpak `varbin` bundles to a working dir and dump an annotated
hex view of the first record region so the layout can be read off directly."""
import glob
import os
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UNK = os.path.join(ROOT, "extracted", "netpak", "unknown")
OUT = os.path.join(ROOT, "work", "varbin")


def unpack():
    os.makedirs(OUT, exist_ok=True)
    zips = sorted(glob.glob(os.path.join(UNK, "*.zip")))
    made = []
    for z in zips:
        base = os.path.splitext(os.path.basename(z))[0]
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            if not names:
                continue
            data = zf.read(names[0])
        p = os.path.join(OUT, base + ".bin")
        if not os.path.exists(p) or os.path.getsize(p) != len(data):
            with open(p, "wb") as f:
                f.write(data)
        made.append((base, len(data), names[0]))
    print(f"unpacked {len(made)} blobs to {OUT}")
    made.sort(key=lambda t: -t[1])
    print("largest:", made[0][:2], "smallest:", made[-1][:2])
    return made


def hexdump(data, off, n, label=""):
    if label:
        print(f"\n--- {label} @0x{off:X} ---")
    end = min(len(data), off + n)
    for i in range(off, end, 16):
        chunk = data[i:i + 16]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"{i:08X}  {hx:<47}  |{asc}|")


def main():
    made = unpack()
    made.sort(key=lambda t: -t[1])
    for base, size, member in made[:1]:
        p = os.path.join(OUT, base + ".bin")
        data = open(p, "rb").read()
        print(f"\n=== {base}.bin  {size} bytes  (zip member '{member}') ===")
        hexdump(data, 0, 0x180, "head")
        hexdump(data, size - 0x100, 0x100, "tail")


if __name__ == "__main__":
    main()
