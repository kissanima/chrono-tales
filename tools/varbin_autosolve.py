#!/usr/bin/env python3
"""Auto-solve the remaining varbin tags.

Grammar so far (proven): `0x40 <u8 n>` / `0x41 <u32 n>` are groups of n values,
`0x31` is a length-prefixed UTF-16 string, `0x12` int32, `0x13` float32.

For every other tag the payload size is unknown.  Group counts are a hard check --
a group claiming 52 values must yield exactly 52 -- so on hitting an unknown tag we
try each candidate size 0..8 and keep the one that lets the parse get furthest.
Iterate passes until every blob is consumed exactly or no further progress is made.
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
OUT = os.path.join(HERE, "varbin_tags.json")

MAXSIZE = 8
sys.setrecursionlimit(60000)
MAXDEPTH = 400

# proven from the group structure
BASE = {0x31: "str", 0x40: "g8", 0x41: "g32", 0x12: 4, 0x13: 4}
# observed with no payload (flags / enums / nulls)
ZERO = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
        0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x30, 0xFF}


class Fail(Exception):
    def __init__(self, pos, tag):
        self.pos, self.tag = pos, tag


def parse(d, tags, stop_on_unknown=True):
    """Return bytes consumed.  Raises Fail on an unsized tag."""
    n = len(d)

    def value(i, depth=0):
        if depth > MAXDEPTH:
            raise Fail(i, -8)
        if i >= n:
            raise Fail(i, -1)
        t = d[i]
        k = tags.get(t)
        if k is None:
            raise Fail(i, t)
        if k == "str":
            if i + 5 > n:
                raise Fail(i, -2)
            (c,) = struct.unpack_from("<I", d, i + 1)
            e = i + 5 + c * 2
            if c > 0x2000 or e > n:
                raise Fail(i, -3)
            return e
        if k == "g8":
            if i + 2 > n:
                raise Fail(i, -4)
            j = i + 2
            for _ in range(d[i + 1]):
                j = value(j, depth + 1)
            return j
        if k == "g32":
            if i + 5 > n:
                raise Fail(i, -5)
            (c,) = struct.unpack_from("<I", d, i + 1)
            if c > 0x100000:
                raise Fail(i, -6)
            j = i + 5
            for _ in range(c):
                j = value(j, depth + 1)
            return j
        e = i + 1 + k
        if e > n:
            raise Fail(i, -7)
        return e

    try:
        return value(0), None
    except Fail as f:
        return f.pos, f


def main():
    blobs = [(os.path.basename(p)[:-4], open(p, "rb").read())
             for p in sorted(glob.glob(os.path.join(VB, "*.bin")))]

    tags = dict(BASE)
    for z in ZERO:
        tags[z] = 0

    for rnd in range(14):
        learned = {}
        for name, d in blobs:
            tags_local = dict(tags)
            for _ in range(200):
                pos, err = parse(d, tags_local)
                if err is None and pos == len(d):
                    break
                if err is None:              # consumed a prefix only
                    break
                t = err.tag
                if t < 0:
                    break
                # try candidate sizes, keep the one that gets furthest
                best, best_pos = None, -1
                for s in range(0, MAXSIZE + 1):
                    trial = dict(tags_local)
                    trial[t] = s
                    p2, e2 = parse(d, trial)
                    if e2 is None and p2 == len(d):
                        best, best_pos = s, p2
                        break
                    if e2 is None and p2 > best_pos:
                        best, best_pos = s, p2
                if best is None:
                    break
                tags_local[t] = best
                learned.setdefault(t, Counter())[best] += 1
        added = 0
        for t, c in learned.items():
            if t in tags:
                continue
            tags[t] = c.most_common(1)[0][0]
            added += 1
        clean = sum(1 for _, d in blobs
                    if (lambda r: r[1] is None and r[0] == len(d))(parse(d, tags)))
        print(f"round {rnd + 1}: +{added} tags, {len(tags)} known, "
              f"{clean}/{len(blobs)} blobs clean")
        if not added and clean == len(blobs):
            break
        if not added and rnd > 2:
            break

    print("\n--- tag table ---")
    for t in sorted(tags):
        v = tags[t]
        print(f"  0x{t:02X} : {v}")

    total = sum(len(d) for _, d in blobs)
    done = 0
    bad = []
    for name, d in blobs:
        pos, err = parse(d, tags)
        if err is None:
            done += pos
            if pos != len(d):
                bad.append((name, pos, len(d), "trailing"))
        else:
            done += pos
            bad.append((name, pos, len(d), f"tag 0x{err.tag:02X}"))
    print(f"\nbytes parsed: {done:,} / {total:,} ({100.0 * done / total:.2f}%)")
    for b in bad[:15]:
        print(f"  {b[0]}  {b[1]:,}/{b[2]:,}  {b[3]}")

    with open(OUT, "w") as f:
        json.dump({str(k): v for k, v in sorted(tags.items())}, f, indent=1)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
