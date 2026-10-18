#!/usr/bin/env python3
"""Export the varbin tables to CSV with real column names.

Pipeline: netpak varbin payload -> tagged tree (`varfile.load_varbin`) ->
one fixed-schema table per payload -> positional match against the table
editor's `m_szItemName` column list.

The grammar is the same one `SysFileVer.var` uses, unencrypted — see
`SYSFILEVER.md` and `GPAK_FORMAT.md` §4.2.  Because `varfile` decodes every
payload to its exact last byte, this exporter never has to resynchronise and
never drops a record.

Blobs whose records contain a `bytes` field are map payloads, not tables, and
carry no `table` in `varbin_identity.json`, so they are skipped here too.
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import varbin_tree  # noqa: E402

OUT = os.path.join(ROOT, "extracted", "tables")
IDENT = os.path.join(HERE, "varbin_identity.json")
SCHEMA = os.path.join(HERE, "table_schemas.json")


def cell(v):
    """Render a decoded value as a CSV cell."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>"
    if isinstance(v, (list, tuple)):
        return "|".join(cell(x) for x in v)
    return str(v)


def main():
    ident = json.load(open(IDENT, encoding="utf-8"))
    schemas = json.load(open(SCHEMA, encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)

    # several blobs can shard one table, so accumulate before writing
    shards = {}
    ragged_total = 0
    for name, d in sorted(varbin_tree.payloads()):
        info = ident.get(name) or {}
        table = info.get("table")
        if not table:
            continue
        if table not in schemas and not info.get("synthetic"):
            continue
        recs = d["value"]
        if not isinstance(recs, list):
            print(f"SKIP {name}: top level is not a table")
            continue
        s = shards.setdefault(table, {
            "recs": [], "blobs": [],
            "synthetic": bool(info.get("synthetic")),
            "width": info.get("columns") or len(recs[0]),
            "title": (schemas[table]["editor_title"] if table in schemas
                      else info.get("editor_title") or table),
        })
        s["recs"].extend(recs)
        s["blobs"].append(name)

    index = []
    for table, s in sorted(shards.items()):
        recs = s["recs"]
        width = s["width"]
        if s["synthetic"]:
            # no shipped editor: headers are positional
            cols = [f"c{i}" for i in range(width)]
            fname = table + ".csv"
        else:
            cols = schemas[table]["columns"]
            fname = os.path.splitext(table)[0] + ".csv"
        title = s["title"]
        path = os.path.join(OUT, fname)
        ragged = 0
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in recs:
                fields = r if isinstance(r, list) else [r]
                if len(fields) != width:
                    ragged += 1
                row = [cell(v) for v in fields[:width]]
                if len(row) < width:
                    row += [""] * (width - len(row))
                w.writerow(row)
        ragged_total += ragged
        index.append({
            "table": table,
            "title": title,
            "csv": fname,
            "records": len(recs),
            "columns": width,
            "blobs": s["blobs"],
            "synthetic": s["synthetic"],
            "confidence": sorted({ident[b].get("confidence", "")
                                  for b in s["blobs"]}),
            "ragged": ragged,
        })
        flag = f"  RAGGED={ragged}" if ragged else ""
        mark = "  [no editor]" if s["synthetic"] else ""
        print(f"-> {fname:26s} {len(recs):5d} rows x {width:2d} cols  "
              f"{title}{mark}{flag}")

    with open(os.path.join(OUT, "_index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)

    total = sum(i["records"] for i in index)
    print(f"\n{len(index)} tables, {total:,} rows -> {OUT}")
    if ragged_total:
        print(f"warning: {ragged_total} rows did not match their column count")


if __name__ == "__main__":
    main()
