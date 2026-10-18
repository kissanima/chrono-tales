#!/usr/bin/env python3
"""Repair-walk: parse with base grammar; on failure record the offending tag
byte at its offset and resume by skipping, collecting all bad positions."""
import json
import os
import struct
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from varbin_solve2 import base_tags, Node  # noqa: E402

VB = os.path.join(ROOT, "work", "varbin")


def walk(d, i, sizes, bad):
    """Return list of end positions after parsing one value; on fail record."""
    t = d[i]
    k = sizes.get(t)
    if k is None:
        bad.append((i, t))
        return []
    if k == "str":
        (c,) = struct.unpack_from("<I", d, i + 1)
        e = i + 5 + c * 2
        if c > 0x2000 or e > len(d):
            bad.append((i, t, "strlen"))
            return []
        return [e]
    if k in ("g8", "g32"):
        if k == "g8":
            n, start = d[i + 1], i + 2
        else:
            (n,) = struct.unpack_from("<I", d, i + 1)
            start = i + 5
        ends = {start}
        for _ in range(n):
            nxt = set()
            for j in ends:
                if j >= len(d):
                    bad.append((j, 0xFF, "eof"))
                    continue
                nxt |= set(walk(d, j, sizes, bad))
            ends = nxt
            if not ends:
                return []
        return sorted(ends)
    e = i + 1 + k
    if e > len(d):
        bad.append((i, t, "trunc"))
        return []
    return [e]


def main():
    tags = base_tags()
    for name in sys.argv[1:] or ["d07f4acc", "66d2c4f3", "0580853e"]:
        d = open(os.path.join(VB, name + ".bin"), "rb").read()
        bad = []
        ends = walk(d, 0, tags, bad)
        ends = [e for e in ends if e == len(d)]
        print(f"\n=== {name}: {len(bad)} bad positions, EOF-ok={bool(ends)} ===")
        cnt = Counter(hex(t) for _, t, *_ in bad)
        print("  bad tags:", dict(cnt))
        for i, t, *r in bad[:20]:
            ctx = d[i:i + 12].hex()
            print(f"   @{i:06x} tag 0x{t:02X} {r} ctx={ctx}")
        if len(bad) > 20:
            print(f"   ... +{len(bad) - 20} more, tail:")
            for i, t, *r in bad[-5:]:
                print(f"   @{i:06x} tag 0x{t:02X} {r} ctx={d[i:i+12].hex()}")


if __name__ == "__main__":
    main()
