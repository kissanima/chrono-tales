#!/usr/bin/env python3
"""Export the varbin tables to CSV with real column names.

Pipeline: netpak varbin blob -> nested group tree -> one group per record ->
positional match against the table editor's `m_szItemName` column list.

Grammar (solved):
    0x40 <u8 n>   group of n values      0x41 <u32 n>  group of n values
    0x31          u32 charCount + UTF-16LE string
    0x12 int32   0x13 float32   0x10 u8   0x11 u16   0x32 u16   0x43 f64
    everything else: no payload (flags / enums / nulls)

Row IDs are `HashString(系统名, 0)` -- the same MPQ hash used for GPAK filenames.
"""
import csv
import glob
import json
import os
import struct
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")
OUT = os.path.join(ROOT, "extracted", "tables")
IDENT = os.path.join(HERE, "varbin_identity.json")
SCHEMA = os.path.join(HERE, "table_schemas.json")
TAGFILE = os.path.join(HERE, "varbin_tags.json")

sys.setrecursionlimit(60000)

ZERO = set(range(0x00, 0x10)) | {0x30, 0xFF}


def load_tags():
    tags = {0x31: "str", 0x40: "g8", 0x41: "g32",
            0x12: 4, 0x13: 4, 0x10: 1, 0x11: 2, 0x32: 2, 0x43: 8}
    for z in ZERO:
        tags[z] = 0
    if os.path.exists(TAGFILE):
        for k, v in json.load(open(TAGFILE)).items():
            if not k.isdigit():
                continue            # _comment / _grammar
            tags[int(k)] = v
    tags[0x16] = 4          # float32, seen as -0.03 next to 1.12 / 106.8
    return tags


class Fail(Exception):
    pass


def value(d, i, tags):
    if i >= len(d):
        raise Fail("eof")
    t = d[i]
    k = tags.get(t)
    if k is None:
        raise Fail(f"tag 0x{t:02X}")
    if k == "str":
        (c,) = struct.unpack_from("<I", d, i + 1)
        e = i + 5 + c * 2
        if c > 0x2000 or e > len(d):
            raise Fail("str")
        return ("s", d[i + 5:e].decode("utf-16-le", "replace")), e
    if k == "g8":
        n = d[i + 1]
        j = i + 2
        out = []
        for _ in range(n):
            v, j = value(d, j, tags)
            out.append(v)
        return ("g", out), j
    if k == "g32":
        (n,) = struct.unpack_from("<I", d, i + 1)
        if n > 0x100000:
            raise Fail("g32")
        j = i + 5
        out = []
        for _ in range(n):
            v, j = value(d, j, tags)
            out.append(v)
        return ("g", out), j
    if t == 0x12:
        return ("i", struct.unpack_from("<i", d, i + 1)[0]), i + 5
    if t == 0x13:
        return ("f", struct.unpack_from("<f", d, i + 1)[0]), i + 5
    if k == 8:
        return ("d", struct.unpack_from("<d", d, i + 1)[0]), i + 9
    e = i + 1 + k
    if e > len(d):
        raise Fail("trunc")
    return ("b", int.from_bytes(d[i + 1:e], "little")), e


def flat(v):
    """Render a value as a CSV cell."""
    k, x = v
    if k == "s":
        return x
    if k == "g":
        return "|".join(flat(c) for c in x)
    if k == "f":
        return f"{x:.6g}"
    if k == "i":
        return str(x)
    if k == "d":
        return f"{x:.6g}"
    return str(x)


def main():
    tags = load_tags()
    ident = json.load(open(IDENT, encoding="utf-8"))
    schemas = json.load(open(SCHEMA, encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)

    index = []
    # several blobs can shard one table, so accumulate before writing
    shards = {}
    for name in sorted(ident):
        info = ident[name]
        table = info.get("table")
        p = os.path.join(VB, name + ".bin")
        if not table or not os.path.exists(p):
            continue
        d = open(p, "rb").read()
        try:
            top, end = value(d, 0, tags)
            if end != len(d):
                raise Fail(f"trailing {len(d) - end}B")
        except Fail as e:
            print(f"SKIP {name} ({table}): {e}")
            continue
        if top[0] != "g":
            print(f"SKIP {name}: top level is {top[0]}, not a group")
            continue
        shards.setdefault(table, []).extend(top[1])

    for table, recs in shards.items():
        cols = schemas[table]["columns"]
        title = schemas[table]["editor_title"] or table
        width = len(cols)
        fname = os.path.splitext(table)[0] + ".csv"
        path = os.path.join(OUT, fname)
        ragged = Counter()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in recs:
                fields = r[1] if r[0] == "g" else [r]
                ragged[len(fields) - width] += 1
                row = [flat(v) for v in fields[:width]]
                if len(row) < width:
                    row += [""] * (width - len(row))
                w.writerow(row)
        index.append({
            "table": table,
            "title": title,
            "csv": fname,
            "records": len(recs),
            "columns": width,
            "blobs": [n for n, i in ident.items() if i.get("table") == table],
            "ragged": dict(ragged),
        })
        rag = f"  ragged={dict(ragged)}" if any(ragged) else ""
        print(f"-> {fname:26s} {len(recs):5d} rows x {width:2d} cols  {title}{rag}")

    with open(os.path.join(OUT, "_index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    total = sum(i["records"] for i in index)
    print(f"\n{len(index)} tables, {total:,} rows -> {OUT}")


if __name__ == "__main__":
    main()
