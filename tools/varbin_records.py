#!/usr/bin/env python3
"""Test the record-delimiter hypothesis: each record starts with `0x40 <columnCount>`.

If true, the byte pair `40 <cols>` must occur exactly `records` times in the blob --
a very tight check, since the header already tells us both numbers.
"""
import glob
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")
IDENT = os.path.join(HERE, "varbin_identity.json")

SIZES = {0x10: 1, 0x11: 1, 0x12: 4, 0x13: 4, 0x40: 1, 0x41: 4}


def header(d):
    i, out = 0, []
    for _ in range(3):
        if i >= len(d):
            break
        t = d[i]
        s = SIZES.get(t)
        if s is None:
            break
        out.append(int.from_bytes(d[i + 1:i + 1 + s], "little"))
        i += 1 + s
    return (out + [None, None, None])[:3]


def main():
    ident = json.load(open(IDENT, encoding="utf-8"))
    print(f"{'blob':10s} {'recs':>6s} {'cols':>5s} {'40<cols>':>9s}  verdict")
    print("-" * 62)
    ok = tot = 0
    for name in sorted(ident):
        p = os.path.join(VB, name + ".bin")
        if not os.path.exists(p):
            continue
        d = open(p, "rb").read()
        recs, cols, _ = header(d)
        if recs is None or cols is None or cols > 255:
            continue
        pat = bytes([0x40, cols & 0xFF])
        n = sum(1 for i in range(len(d) - 1) if d[i:i + 2] == pat)
        tot += 1
        good = (n == recs)
        ok += good
        print(f"{name:10s} {recs:6d} {cols:5d} {n:9d}  "
              f"{'EXACT' if good else ('off by %+d' % (n - recs))}")
    print(f"\n{ok}/{tot} blobs match exactly")


if __name__ == "__main__":
    main()
