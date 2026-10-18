#!/usr/bin/env python3
"""Derive the `varbin` tag grammar by testing hypotheses against every blob.

Approach: assume a tag-length-value stream.  Known so far:
    0x31  u32 charCount + charCount*2 bytes UTF-16LE   (confirmed)
    0x40  u8 value                                     (hypothesis)
For each candidate grammar, run it over all 49 blobs and report how many parse
cleanly to EOF -- the grammar that consumes every byte everywhere is the right one.
"""
import glob
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")


def load():
    out = []
    for p in sorted(glob.glob(os.path.join(VB, "*.bin"))):
        out.append((os.path.basename(p), open(p, "rb").read()))
    return out


def try_parse(data, grammar, trace_limit=0):
    """grammar: dict tag -> kind ('u8','u16','u32','wstr','raw<n>')"""
    i = 0
    n = len(data)
    tags = []
    while i < n:
        tag = data[i]
        i += 1
        kind = grammar.get(tag)
        if kind is None:
            return i - 1, tags, f"unknown tag 0x{tag:02X}"
        if kind == "u8":
            if i + 1 > n:
                return i - 1, tags, "trunc u8"
            tags.append((i - 1, tag, data[i]))
            i += 1
        elif kind == "u16":
            if i + 2 > n:
                return i - 1, tags, "trunc u16"
            (v,) = struct.unpack_from("<H", data, i)
            tags.append((i - 1, tag, v))
            i += 2
        elif kind == "u32":
            if i + 4 > n:
                return i - 1, tags, "trunc u32"
            (v,) = struct.unpack_from("<I", data, i)
            tags.append((i - 1, tag, v))
            i += 4
        elif kind == "wstr":
            if i + 4 > n:
                return i - 1, tags, "trunc wstr len"
            (c,) = struct.unpack_from("<I", data, i)
            if c > 0x4000:
                return i - 1, tags, f"wstr len insane {c}"
            need = i + 4 + c * 2
            if need > n:
                return i - 1, tags, f"wstr overrun c={c}"
            s = data[i + 4:need].decode("utf-16-le", "replace")
            tags.append((i - 1, tag, s))
            i = need
        else:
            raise ValueError(kind)
    return n, tags, None


def main():
    blobs = load()
    print(f"{len(blobs)} blobs, {sum(len(d) for _, d in blobs)} bytes total\n")

    # hypothesis A: 0x31 wstr, 0x40 u8
    gA = {0x31: "wstr", 0x40: "u8"}
    ok = 0
    for name, d in blobs:
        pos, tags, err = try_parse(d, gA)
        if err is None:
            ok += 1
    print(f"A {{31:wstr, 40:u8}} -> {ok}/{len(blobs)} clean")

    # show where the largest blob dies under A
    name, d = max(blobs, key=lambda t: len(t[1]))
    pos, tags, err = try_parse(d, gA)
    print(f"\nlargest {name} {len(d)}B: parsed to 0x{pos:X}, {len(tags)} tags, err={err}")
    print("  context:", d[max(0, pos - 24):pos + 16].hex())
    print("  last 6 tags:", [(hex(t[0]), hex(t[1]), (v[:20] if isinstance(v, str) else v))
                              for t in tags[-6:] for v in [t[2]]])

    # tag histogram from the partial parse
    from collections import Counter
    print("\ntag histogram (partial parse of largest):", Counter(hex(t[1]) for t in tags).most_common())


if __name__ == "__main__":
    main()
