#!/usr/bin/env python3
"""Parse varbin as a nested group tree.

Recovered grammar -- `0x40 <n>` is a *count*, not a delimiter:

    0x40 <u8 n>    group of n values
    0x41 <u32 n>   group of n values
    0x31           string: u32 charCount + charCount*2 bytes UTF-16LE
    0x12           int32
    0x13           float32
    0x10, 0x11     u8
    0x00 0x01 0x02 0x03 0x04 0x05 0x06 0x07 0x08 0x09 0x0a
    0x0b 0x0c 0x0d 0x0e 0x0f 0x30 0xff   no payload

So a file is one group of records; each record is a group of columns.  The group
counts are a hard check: a group claiming 52 values must yield exactly 52.
"""
import glob
import json
import os
import struct
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")

ZERO = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
        0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x30, 0xFF}


class Fail(Exception):
    pass


def val(d, i):
    """Return (value, next_index)."""
    if i >= len(d):
        raise Fail("eof")
    t = d[i]
    if t == 0x31:
        if i + 5 > len(d):
            raise Fail("str hdr")
        (c,) = struct.unpack_from("<I", d, i + 1)
        e = i + 5 + c * 2
        if c > 0x2000 or e > len(d):
            raise Fail(f"str len {c}")
        s = d[i + 5:e].decode("utf-16-le", "replace")
        return ("s", s), e
    if t == 0x40:
        if i + 2 > len(d):
            raise Fail("g8 hdr")
        n = d[i + 1]
        j = i + 2
        out = []
        for _ in range(n):
            v, j = val(d, j)
            out.append(v)
        return ("g", out), j
    if t == 0x41:
        if i + 5 > len(d):
            raise Fail("g32 hdr")
        (n,) = struct.unpack_from("<I", d, i + 1)
        if n > 0x100000:
            raise Fail(f"g32 count {n}")
        j = i + 5
        out = []
        for _ in range(n):
            v, j = val(d, j)
            out.append(v)
        return ("g", out), j
    if t == 0x12:
        return ("i", struct.unpack_from("<i", d, i + 1)[0]), i + 5
    if t == 0x13:
        return ("f", struct.unpack_from("<f", d, i + 1)[0]), i + 5
    if t in (0x10, 0x11):
        return ("b", d[i + 1]), i + 2
    if t in ZERO:
        return ("z", t), i + 1
    raise Fail(f"tag 0x{t:02X}")


def parse(d):
    v, i = val(d, 0)
    return v, i


def main():
    blobs = [(os.path.basename(p)[:-4], open(p, "rb").read())
             for p in sorted(glob.glob(os.path.join(VB, "*.bin")))]
    clean, fails = 0, Counter()
    for name, d in blobs:
        try:
            v, i = parse(d)
            if i != len(d):
                fails[f"trailing {len(d) - i}B"] += 1
                print(f"{name}  parsed {i}/{len(d)} bytes  (trailing)")
            else:
                clean += 1
        except Fail as e:
            fails[str(e)] += 1
            print(f"{name}  FAIL {e}")
    print(f"\nclean: {clean}/{len(blobs)}")
    for k, c in fails.most_common(10):
        print(f"  {c:3d}x {k}")


if __name__ == "__main__":
    main()
