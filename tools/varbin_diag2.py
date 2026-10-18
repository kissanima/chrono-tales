#!/usr/bin/env python3
"""Diagnose where the current grammar fails on a blob.

Runs the existing varbin_tables.py parser over each blob and prints, for the
first failure: offset, tag, hex context before/after, and the nearest few
string tags before it.
"""
import glob
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")

SIZES = {0x10: 1, 0x11: 2, 0x12: 4, 0x13: 4, 0x16: 4, 0x32: 2, 0x43: 8,
         0x40: "g8", 0x41: "g32"}


def value(d, i, depth=0, tags=None, limit=None):
    """Mimic varbin_tables.value(). Returns (newoff, desc). Raises Fail."""
    if limit and i > limit:
        raise Fail("limit")
    if i >= len(d):
        raise Fail(f"eof at {i}")
    t = d[i]
    if t == 0x31:
        (c,) = struct.unpack_from("<I", d, i + 1)
        e = i + 5 + c * 2
        if c > 0x2000 or e > len(d):
            raise Fail(f"str bad c={c} @{i}")
        return e, ("s", d[i + 5:e].decode("utf-16-le", "replace"))
    if t == 0x40:
        n = d[i + 1]
        j = i + 2
        kids = []
        for _ in range(n):
            j, v = value(d, j, depth + 1, tags, limit)
            kids.append(v)
        return j, ("g8", kids)
    if t == 0x41:
        (n,) = struct.unpack_from("<I", d, i + 1)
        if n > 0x100000:
            raise Fail(f"g32 insane n={n} @{i}")
        j = i + 5
        kids = []
        for _ in range(n):
            j, v = value(d, j, depth + 1, tags, limit)
            kids.append(v)
        return j, ("g32", kids)
    if t in SIZES:
        s = SIZES[t]
        return i + 1 + s, ("n", t)
    raise Fail(f"unknown tag 0x{t:02X} @{i}")


class Fail(Exception):
    pass


def nearest_strings(d, pos, back=3, fwd=1):
    out = []
    i = 0
    strs = []
    while i < len(d):
        if d[i] == 0x31 and i + 5 <= len(d):
            (c,) = struct.unpack_from("<I", d, i + 1)
            e = i + 5 + c * 2
            if 0 < c <= 0x2000 and e <= len(d):
                try:
                    txt = d[i + 5:e].decode("utf-16-le", "replace")
                    if all(ch.isprintable() for ch in txt):
                        strs.append((i, txt))
                        i = e
                        continue
                except Exception:
                    pass
        i += 1
    for s, t in strs:
        if s < pos:
            continue
        if back > 0:
            back -= 1
            out.append((s, t, "fwd" if s > pos else "back"))
            if back == 0:
                break
    return out[-6:], [t for s, t, k in out if k == "fwd"][:2]


def main():
    names = sys.argv[1:] or ["c41c9276", "e15a7343", "433267c3", "0580853e",
                             "66d2c4f3", "d07f4acc"]
    for name in names:
        d = open(os.path.join(VB, name + ".bin"), "rb").read()
        print(f"\n=== {name}  {len(d):,}B ===")
        try:
            end, v = value(d, 0)
            print(f"  parses clean to {end} ({'' if end == len(d) else 'NOT '}EOF)")
            continue
        except Fail as e:
            msg = str(e)
        # find offset
        i = int(msg.split("@")[-1]) if "@" in msg else None
        if i is None and "eof" in msg:
            i = len(d)
        print(f"  FAIL: {msg}")
        if i is not None:
            lo = max(0, i - 32)
            print(f"  ctx[{lo:x}..{i+24:x}]: {d[lo:i + 24].hex()}")
            print(f"      @{i:x}: {' '.join(f'{b:02x}' for b in d[i:i + 24])}")
            back, fwd = nearest_strings(d, i)
            print(f"  last strings before failure: {back}")


if __name__ == "__main__":
    main()
