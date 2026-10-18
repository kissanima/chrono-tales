#!/usr/bin/env python3
"""Identify which table each netpak `varbin` blob holds.

The blobs store records as sequences of column *display names* followed by their
values, so each blob's string vocabulary is a near-subset of one table editor's
`m_szItemName` list.  Score every blob against every schema by column-name
overlap and report the best match.
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
SCHEMA = os.path.join(HERE, "table_schemas.json")
WSTR = 0x31


def strings_of(data):
    out, i, n = [], 0, len(data)
    while i < n:
        if data[i] == WSTR and i + 5 <= n:
            (c,) = struct.unpack_from("<I", data, i + 1)
            end = i + 5 + c * 2
            if 0 < c <= 0x2000 and end <= n:
                txt = data[i + 5:end].decode("utf-16-le", "ignore")
                if all(ch.isprintable() for ch in txt):
                    out.append(txt)
                    i = end
                    continue
        i += 1
    return out


def main():
    schemas = json.load(open(SCHEMA, encoding="utf-8"))
    blobs = [(os.path.basename(p), open(p, "rb").read())
             for p in sorted(glob.glob(os.path.join(VB, "*.bin")))]
    print(f"{len(blobs)} blobs, {len(schemas)} schemas\n")

    rows = []
    for name, data in blobs:
        strs = strings_of(data)
        vocab = set(strs)
        best = []
        for sname, s in schemas.items():
            cols = [c for c in s["columns"] if c]
            if not cols:
                continue
            hit = sum(1 for c in cols if c in vocab)
            best.append((hit / len(cols), hit, len(cols), sname,
                         s["editor_title"] or sname))
        best.sort(reverse=True)
        cov, hit, ncols, sname, title = best[0]
        rows.append((cov, name, len(strs), sname, hit, ncols, title))

    rows.sort(reverse=True)
    print(f"{'blob':12s} {'strs':>6s} {'table':26s} {'cols':>9s} {'cov':>6s}  title")
    print("-" * 88)
    for cov, name, nstr, sname, hit, ncols, title in rows:
        print(f"{name:12s} {nstr:6d} {sname:26s} {hit:4d}/{ncols:<4d} "
              f"{100 * cov:5.1f}%  {title}")

    # dump per-blob column order for the best matches
    print("\n--- column presence detail (top 5) ---")
    for cov, name, nstr, sname, hit, ncols, title in rows[:5]:
        s = schemas[sname]
        data = dict((n, d) for n, d in blobs)[name]
        vocab = set(strings_of(data))
        print(f"\n{name} -> {sname} ({title})  cov={100 * cov:.0f}%")
        miss = [c for c in s["columns"] if c and c not in vocab]
        print(f"  missing {len(miss)}: {miss[:12]}")


if __name__ == "__main__":
    main()
