#!/usr/bin/env python3
"""Test the hypothesis that `0x40 <n>` is "n strings follow" (a string-array count),
which would make varbin records self-delimiting and unlock the numeric fields.

Grammar under test:
    0x31              u32 charCount + UTF-16LE
    0x40 <n>          n × 0x31 string
    0x41 <u32 n>      n × 0x31 string
    0x10 0x11         u8
    0x12              int32
    0x13              float32
Everything else is reported so the grammar can be extended.
"""
import glob
import os
import struct
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")
WSTR = 0x31


def load():
    return [(os.path.basename(p), open(p, "rb").read())
            for p in sorted(glob.glob(os.path.join(VB, "*.bin")))]


def read_str(d, i):
    if i + 5 > len(d) or d[i] != WSTR:
        return None
    (c,) = struct.unpack_from("<I", d, i + 1)
    end = i + 5 + c * 2
    if not (0 < c <= 0x2000) or end > len(d):
        return None
    txt = d[i + 5:end].decode("utf-16-le", "ignore")
    if not all(ch.isprintable() for ch in txt):
        return None
    return txt, end


def parse(d):
    i, n = 0, len(d)
    toks, err = [], None
    while i < n:
        t = d[i]
        if t == WSTR:
            r = read_str(d, i)
            if not r:
                return toks, i, "bad string"
            toks.append((i, "str", r[0]))
            i = r[1]
        elif t == 0x40:
            if i + 2 > n:
                return toks, i, "trunc 0x40"
            cnt = d[i + 1]
            j = i + 2
            got = []
            ok = True
            for _ in range(cnt):
                r = read_str(d, j)
                if not r:
                    ok = False
                    break
                got.append(r[0])
                j = r[1]
            if not ok:
                return toks, i, f"0x40 count {cnt} but only {len(got)} strings"
            toks.append((i, "arr", got))
            i = j
        elif t == 0x41:
            if i + 5 > n:
                return toks, i, "trunc 0x41"
            (cnt,) = struct.unpack_from("<I", d, i + 1)
            j = i + 5
            got = []
            ok = True
            for _ in range(cnt):
                r = read_str(d, j)
                if not r:
                    ok = False
                    break
                got.append(r[0])
                j = r[1]
            if not ok:
                return toks, i, f"0x41 count {cnt} but only {len(got)} strings"
            toks.append((i, "arr32", got))
            i = j
        elif t in (0x10, 0x11):
            toks.append((i, "u8", d[i + 1]))
            i += 2
        elif t == 0x12:
            (v,) = struct.unpack_from("<i", d, i + 1)
            toks.append((i, "i32", v))
            i += 5
        elif t == 0x13:
            (v,) = struct.unpack_from("<f", d, i + 1)
            toks.append((i, "f32", v))
            i += 5
        else:
            return toks, i, f"unknown 0x{t:02X}"
    return toks, i, err


def main():
    blobs = load()
    clean, fails = 0, Counter()
    first = {}
    for name, d in blobs:
        toks, pos, err = parse(d)
        if err is None:
            clean += 1
        else:
            fails[err.split(" ")[0] if err.startswith(("unknown", "0x40", "0x41"))
                  else err] += 1
            first.setdefault(err, (name, pos, d, toks))
    print(f"clean: {clean}/{len(blobs)}\n")
    for k, c in fails.most_common(10):
        print(f"  {c:3d}x  {k}")
    print("\n--- first failure of each kind ---")
    for err, (name, pos, d, toks) in list(first.items())[:8]:
        print(f"\n  {err}  in {name} @0x{pos:X}  (after {len(toks)} tokens)")
        print(f"    ctx: {d[pos:pos + 32].hex()}")
        for t in toks[-3:]:
            print(f"    prev: {t[1]} @0x{t[0]:X} {str(t[2])[:60]}")


if __name__ == "__main__":
    main()
