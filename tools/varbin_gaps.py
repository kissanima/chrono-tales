#!/usr/bin/env python3
"""Tokenise varbin with the one rule we are certain of (0x31 + u32 charCount +
UTF-16LE) and dump every inter-string gap as exact hex + length.  Aligning those
gaps reveals the field layout without eyeballing hexdumps."""
import glob
import os
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")


def load():
    return [(os.path.basename(p), open(p, "rb").read())
            for p in sorted(glob.glob(os.path.join(VB, "*.bin")))]


def tokenise(data):
    """Yield ('gap', bytes) and ('str', text).  Greedy: a gap runs until a 0x31
    whose u32 charCount and payload look like valid text."""
    out = []
    i = 0
    gap_start = 0
    n = len(data)
    while i < n:
        if data[i] == 0x31 and i + 5 <= n:
            (c,) = struct.unpack_from("<I", data, i + 1)
            end = i + 5 + c * 2
            if 0 < c <= 0x2000 and end <= n:
                txt = data[i + 5:end].decode("utf-16-le", "ignore")
                printable = sum(1 for ch in txt if ch.isprintable() or ch in "\r\n\t")
                if printable == len(txt):
                    out.append(("gap", data[gap_start:i]))
                    out.append(("str", txt))
                    i = end
                    gap_start = i
                    continue
        i += 1
    out.append(("gap", data[gap_start:n]))
    return out


def main():
    blobs = load()
    name, data = max(blobs, key=lambda t: len(t[1]))
    toks = tokenise(data)
    print(f"{name}: {len(data)}B -> {len(toks)} tokens, "
          f"{sum(1 for k, _ in toks if k == 'str')} strings\n")

    # 1) gap length histogram
    gaps = [v for k, v in toks if k == "gap"]
    print("gap length histogram:", Counter(len(g) for g in gaps).most_common(12))

    # 2) show the first 30 tokens with exact gap hex
    print("\n--- first tokens ---")
    for k, v in toks[:30]:
        if k == "str":
            print(f"  STR  {v!r}")
        else:
            print(f"  gap  [{len(v):3d}] {v.hex()}")

    # 3) gaps that appear repeatedly, aligned
    print("\n--- most common gap byte-strings ---")
    c = Counter(g for g in gaps if len(g) >= 3)
    for g, cnt in c.most_common(10):
        print(f"  x{cnt:<4} [{len(g):2d}] {g.hex()}")

    # 4) for gaps of one dominant length, show per-position constancy
    print("\n--- positional analysis of gaps grouped by length ---")
    bylen = defaultdict(list)
    for g in gaps:
        bylen[len(g)].append(g)
    for L, items in sorted(bylen.items(), key=lambda kv: -len(kv[1]))[:4]:
        if L < 3 or len(items) < 10:
            continue
        print(f"\n  len={L}  n={len(items)}")
        for pos in range(L):
            vals = Counter(g[pos] for g in items)
            if len(vals) <= 3:
                top = ", ".join(f"0x{b:02X}x{n}" for b, n in vals.most_common())
                print(f"    +{pos:02d}: {top}")
            else:
                print(f"    +{pos:02d}: {len(vals)} distinct")


if __name__ == "__main__":
    main()
