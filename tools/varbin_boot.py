#!/usr/bin/env python3
"""Iteratively derive the varbin tag grammar.

`0x31` (wstring) is proven.  Everything else is bootstrapped: run the parser with
the current rule table, collect the first unknown tag in every blob together with
its following bytes, and infer the payload size from the known-good resumption
point (the next `0x31` string).  Repeat until all 49 blobs parse to EOF.
"""
import glob
import os
import struct
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")

# payload size in bytes, or 'wstr' for the string form
RULES = {
    0x31: "wstr",   # proven: u32 charCount + UTF-16LE
    0x12: 4,        # int32   (saw 0xFFFFFFFF = -1)
    0x13: 4,        # float32 (saw 0x3F800000 = 1.0, 0x3F4CCCCD = 0.8)
    0x40: 1,        # u8
    0x41: 4,        # u32
    0x10: 1,        # u8
    0x11: 1,        # u8
    0xFF: 0,        # null
}
# every low tag with no payload (flags / nulls / enum markers)
for _t in list(range(0x00, 0x10)) + [0x30]:
    RULES.setdefault(_t, 0)


def load():
    return [(os.path.basename(p), open(p, "rb").read())
            for p in sorted(glob.glob(os.path.join(VB, "*.bin")))]


def parse(data, rules, max_tokens=None):
    i, n, toks = 0, len(data), []
    while i < n:
        if max_tokens and len(toks) >= max_tokens:
            return toks, i, None
        tag = data[i]
        rule = rules.get(tag)
        if rule is None:
            return toks, i, f"unknown tag 0x{tag:02X}"
        if rule == "wstr":
            if i + 5 > n:
                return toks, i, "trunc wstr hdr"
            (c,) = struct.unpack_from("<I", data, i + 1)
            end = i + 5 + c * 2
            if c > 0x4000 or end > n:
                return toks, i, f"bad wstr len {c}"
            toks.append((i, tag, data[i + 5:end].decode("utf-16-le", "replace")))
            i = end
        else:
            end = i + 1 + rule
            if end > n:
                return toks, i, f"trunc 0x{tag:02X} need {rule}"
            val = data[i + 1:end]
            toks.append((i, tag, val))
            i = end
    return toks, i, None


def main():
    blobs = load()
    print(f"{len(blobs)} blobs, {sum(len(d) for _, d in blobs):,} bytes\n")
    clean, fails = 0, Counter()
    examples = {}
    for name, d in blobs:
        toks, pos, err = parse(d, RULES)
        if err is None:
            clean += 1
        else:
            fails[err.split()[0] + " " + err.split()[1] if err.startswith("unknown") else err[:18]] += 1
            if err.startswith("unknown"):
                key = err
                examples.setdefault(key, []).append((name, pos, d))
    print(f"clean: {clean}/{len(blobs)}")
    for k, c in fails.most_common(12):
        print(f"  {c:3d}x  {k}")

    print("\n--- first failure per distinct unknown tag ---")
    for key, lst in list(examples.items())[:12]:
        name, pos, d = lst[0]
        ctx = d[pos:pos + 40]
        # distance to the next plausible 0x31 string
        nxt = None
        for j in range(pos + 1, min(pos + 80, len(d))):
            if d[j] == 0x31:
                (c,) = struct.unpack_from("<I", d, j + 1) if j + 5 <= len(d) else (0xFFFF,)
                if 0 < c <= 0x2000 and j + 5 + c * 2 <= len(d):
                    txt = d[j + 5:j + 5 + c * 2].decode("utf-16-le", "ignore")
                    if all(ch.isprintable() for ch in txt):
                        nxt = j
                        break
        print(f"\n  {key}  ({len(lst)} blobs)  first in {name} @0x{pos:X}")
        print(f"    ctx : {ctx.hex()}")
        if nxt:
            print(f"    next string @0x{nxt:X}  -> span {nxt - pos} bytes: {d[pos:nxt].hex()}")


if __name__ == "__main__":
    main()
