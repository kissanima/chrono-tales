#!/usr/bin/env python3
"""Find the record delimiter in a varbin blob.

The header states the exact record count N, so the delimiter must occur exactly N
times.  Scan every 1/2/3-byte pattern and report the ones whose count is exactly N,
then show where they land so the record shape can be read off.
"""
import os
import struct
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")


def counts(data, k):
    c = Counter()
    for i in range(len(data) - k + 1):
        c[data[i:i + k]] += 1
    return c


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "96c9244b"
    nrec = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    data = open(os.path.join(VB, name + ".bin"), "rb").read()
    print(f"{name}: {len(data)} bytes, expect {nrec} records\n")

    for k in (1, 2, 3, 4):
        c = counts(data, k)
        hits = [(p, n) for p, n in c.items() if n == nrec]
        hits.sort()
        print(f"--- {k}-byte patterns occurring exactly {nrec} times: {len(hits)}")
        for p, n in hits[:20]:
            offs = [i for i in range(len(data) - k + 1) if data[i:i + k] == p]
            gaps = [offs[j + 1] - offs[j] for j in range(len(offs) - 1)]
            print(f"  {p.hex():8s} n={n:3d}  first={offs[:6]}  gaps={gaps[:8]}")
        if hits:
            print()


if __name__ == "__main__":
    main()
