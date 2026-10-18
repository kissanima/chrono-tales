#!/usr/bin/env python3
"""Identify each netpak `varbin` blob as a game table.

The blob header is three integers:
    [record count][column count][table hash]

Column count is the strong signal -- it must equal the table editor's
`m_szItemName` length.  Where several tables share a column count, the blob's
string vocabulary is scored against each candidate's column names to break the tie.
"""
import glob
import json
import os
import struct
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")
SCHEMA = os.path.join(HERE, "table_schemas.json")
OUT = os.path.join(HERE, "varbin_identity.json")

SIZES = {0x10: 1, 0x11: 1, 0x12: 4, 0x13: 4, 0x40: 1, 0x41: 4}
WSTR = 0x31


def header(d):
    """Return (records, columns, table_hash) or None."""
    i, out = 0, []
    for _ in range(3):
        if i >= len(d):
            break
        t = d[i]
        s = SIZES.get(t)
        if s is None:
            break            # third field may be a tag we have not sized yet
        raw = d[i + 1:i + 1 + s]
        if t == 0x12:
            v = struct.unpack("<i", raw)[0]
        elif t == 0x13:
            v = struct.unpack("<f", raw)[0]
        else:
            v = int.from_bytes(raw, "little")
        out.append(v)
        i += 1 + s
    if len(out) < 2:
        return None
    return out[0], out[1], (out[2] if len(out) > 2 else None)


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
    by_count = defaultdict(list)
    for sname, s in schemas.items():
        cols = [c for c in s["columns"] if c]
        by_count[len(cols)].append((sname, s, set(cols)))

    blobs = [(os.path.basename(p)[:-4], open(p, "rb").read())
             for p in sorted(glob.glob(os.path.join(VB, "*.bin")))]

    result = {}
    print(f"{'blob':10s} {'recs':>6s} {'cols':>5s}  {'table':26s} {'conf':>5s}  title")
    print("-" * 100)
    for name, data in blobs:
        h = header(data)
        if h is None:
            print(f"{name:10s} {'-':>6s} {'-':>5s}  {'(not a table)':26s} {'-':>5s}")
            result[name] = {"records": None, "columns": None, "table": None,
                            "note": "no valid header"}
            continue
        recs, cols, th = h
        cands = by_count.get(cols, [])
        vocab = set(strings_of(data)) if cands else set()
        scored = []
        for sname, s, cset in cands:
            hit = len(cset & vocab)
            scored.append((hit, sname, s))
        scored.sort(key=lambda t: -t[0])
        if len(scored) == 1:
            hit, sname, s = scored[0]
            conf = "exact"
        elif scored and scored[0][0] > (scored[1][0] if len(scored) > 1 else 0):
            hit, sname, s = scored[0]
            conf = "vocab"
        elif scored:
            hit, sname, s = scored[0]
            conf = "tie"
        else:
            hit, sname, s = 0, None, None
            conf = "-"
        title = (s["editor_title"] or sname) if s else ""
        print(f"{name:10s} {recs:6d} {cols:5d}  {(sname or '(unknown)'):26s} "
              f"{conf:>5s}  {title}")
        result[name] = {
            "records": recs,
            "columns": cols,
            "table_hash": th,
            "table": sname,
            "editor_title": title,
            "confidence": conf,
            "column_hits": hit,
        }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    ident = [r for r in result.values() if r.get("table")]
    exact = sum(1 for r in ident if r["confidence"] == "exact")
    print(f"\nidentified {len(ident)}/{len(result)} blobs "
          f"({exact} unambiguous by column count)")
    print(f"total records: {sum(r['records'] or 0 for r in result.values()):,}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
