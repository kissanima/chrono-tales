#!/usr/bin/env python3
"""Solve the varbin field table, strictly.

Model: the stream is a flat sequence of `[u8 fieldId][payload]`.  Payload *size*
depends only on the field id; `0x31` is the self-describing string
(u32 charCount + UTF-16LE).  The schema itself lives in the game binary -- here we
only recover each id's size, which is all a reader needs.

Method: every gap between two strings is an independent, complete sequence of
non-string fields, so it is a hard constraint.  Start from the six sizes verified
by inspection, then for each failing gap that contains exactly one unsolved id,
record which sizes let the *whole* gap parse.  Intersect those candidate sets
across every gap the id appears in; when one size survives, pin it and repeat.
"""
import glob
import itertools
import json
import os
import struct
import sys
from collections import Counter, defaultdict

sys.setrecursionlimit(20000)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")
WSTR = 0x31
MAXSIZE = 12

# verified by inspection:
#   0x12 int32   (0xFFFFFFFF = -1 seen)
#   0x13 float32 (0x3F800000 = 1.0, 0x3F4CCCCD = 0.8 seen)
#   0x40 u8, 0x41 u32, 0x10 u8, 0x11 u8
SEED = {0x10: 1, 0x11: 1, 0x12: 4, 0x13: 4, 0x40: 1, 0x41: 4}


def load():
    return [(os.path.basename(p), open(p, "rb").read())
            for p in sorted(glob.glob(os.path.join(VB, "*.bin")))]


def tokenise(data):
    out, i, n, gs = [], 0, len(data), 0
    while i < n:
        if data[i] == WSTR and i + 5 <= n:
            (c,) = struct.unpack_from("<I", data, i + 1)
            end = i + 5 + c * 2
            if 0 < c <= 0x2000 and end <= n:
                txt = data[i + 5:end].decode("utf-16-le", "ignore")
                if all(ch.isprintable() for ch in txt):
                    out.append(data[gs:i])
                    gs = i = end
                    continue
        i += 1
    out.append(data[gs:n])
    return out


def strict_parse(g, sizes):
    """Parse g exactly, rejecting unknown ids.  Returns tag list or None."""
    def rec(p):
        if p == len(g):
            return []
        t = g[p]
        if t == WSTR:
            if p + 5 > len(g):
                return None
            (c,) = struct.unpack_from("<I", g, p + 1)
            end = p + 5 + c * 2
            if end > len(g):
                return None
            sub = rec(end)
            return None if sub is None else [t] + sub
        s = sizes.get(t)
        if s is None or p + 1 + s > len(g):
            return None
        sub = rec(p + 1 + s)
        return None if sub is None else [t] + sub
    return rec(0)


def main():
    blobs = load()
    gaps = Counter()
    for _, d in blobs:
        for g in tokenise(d):
            if g:
                gaps[g] += 1
    order = sorted(gaps, key=len)
    print(f"{len(blobs)} blobs, {sum(len(d) for _, d in blobs):,} bytes")
    print(f"{len(gaps):,} distinct non-empty gaps\n")

    sizes = dict(SEED)
    allowed = defaultdict(lambda: set(range(0, MAXSIZE + 1)))

    for rnd in range(10):
        added = 0
        for g in order:
            if strict_parse(g, sizes):
                continue
            unk = sorted({b for b in g if b not in sizes and b != WSTR})
            if not unk:
                continue
            if len(unk) == 1:
                t = unk[0]
                ok = set()
                for s in range(0, MAXSIZE + 1):
                    trial = dict(sizes)
                    trial[t] = s
                    if strict_parse(g, trial):
                        ok.add(s)
                allowed[t] &= ok
            elif len(unk) == 2:
                a, b = unk
                okA, okB = set(), set()
                for sa, sb in itertools.product(range(0, MAXSIZE + 1), repeat=2):
                    trial = dict(sizes)
                    trial[a], trial[b] = sa, sb
                    if strict_parse(g, trial):
                        okA.add(sa)
                        okB.add(sb)
                allowed[a] &= okA
                allowed[b] &= okB
        for t, cand in allowed.items():
            if t not in sizes and len(cand) == 1:
                sizes[t] = next(iter(cand))
                added += 1
        print(f"round {rnd + 1}: +{added} -> {len(sizes)} ids "
              f"(pending: {sum(1 for t in allowed if t not in sizes)})")
        if not added:
            break

    print("\n--- field table ---")
    for t in sorted(sizes):
        print(f"  0x{t:02X} : {sizes[t]}")

    ok = bad = 0
    for g, c in gaps.items():
        if len(g) > 512:
            continue
        if strict_parse(g, sizes):
            ok += c
        else:
            bad += c
    print(f"\nSTRICT gap verification (<=512B): {ok:,} / {ok + bad:,} "
          f"({100.0 * ok / max(1, ok + bad):.2f}%)")

    # whole-blob verification
    clean = 0
    for name, d in blobs:
        toks = tokenise(d)
        good = sum(1 for g in toks if not g or strict_parse(g, sizes))
        if good == len(toks):
            clean += 1
    print(f"blobs parsing fully: {clean}/{len(blobs)}")

    with open(os.path.join(HERE, "varbin_fields.json"), "w") as f:
        json.dump({str(k): v for k, v in sorted(sizes.items())}, f, indent=1)
    print("wrote varbin_fields.json")


if __name__ == "__main__":
    main()
