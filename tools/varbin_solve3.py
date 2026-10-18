#!/usr/bin/env python3
"""varbin_solve3 -- recover the six varbin blobs that desync under the flat gap
solver and export them to extracted/tables/<hash>.csv.

Why the flat solver desyncs
---------------------------
The six blobs contain field ids whose payload size is not in the solved table.
In a *gap* (the flat span between two strings) two such unknown ids can be sized
in more than one way and still make the gap parse, so the parser picks a wrong
combination and every later field shifts by the size error.

The robust model: a blob is a `0x40 <u8 N>` / `0x41 <u32 N>` group of N records;
each record is a `0x40 <u8 M>` group of exactly M column values (M == the table's
column count).  A record is therefore hard-constrained: it must deliver exactly M
values AND the byte right after it must be the next record's `0x40 <M>` header (or
EOF).  That single extra anchor resolves the previously ambiguous 2-id gap, because
only the correct size pair lands the record boundary on a valid next-record header.
Where a record still admits two orderings of the unknown ids, the chosen ordering
is the one under which the *next* record also parses (consistency look-ahead).

Export: one CSV per blob.  Columns use the table editor's real names when the
table was identified; otherwise positional FIELD_0.. names.  Any column position
dominated by a field id not in the solved table is renamed to UNKNOWN_k (the
values are still decoded correctly -- only the semantic name is unknown).  Records
that cannot be anchored are emitted as empty rows so the file stays rectangular.

Usage:  python varbin_solve3.py
"""
import csv
import json
import os
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")
OUT = os.path.join(ROOT, "extracted", "tables")
IDENT = os.path.join(HERE, "varbin_identity.json")
SCHEMA = os.path.join(HERE, "table_schemas.json")
TAGFILE = os.path.join(HERE, "varbin_tags.json")

MAXSZ = 16          # max payload bytes tried for an unknown tag
CAP = 200000        # DFS node cap per record
LOOKAHEAD = 3       # records used to disambiguate a 2-id ordering

ZERO = set(range(0x00, 0x10)) | {0x30, 0xFF}
sys.setrecursionlimit(1000000)


def base_tags():
    """Solved tag table (the one the 46 clean blobs parse against)."""
    tags = {0x31: "str", 0x40: "g8", 0x41: "g32",
            0x12: 4, 0x13: 4, 0x10: 1, 0x11: 2, 0x32: 2, 0x43: 8}
    for z in ZERO:
        tags[z] = 0
    if os.path.exists(TAGFILE):
        for k, v in json.load(open(TAGFILE)).items():
            if not k.isdigit():
                continue
            tags[int(k)] = v
    tags[0x16] = 4          # float32, confirmed by neighbours
    return tags


# ---------------------------------------------------------------------------
# core value parser (recursive, branches over unknown tag sizes)
# ---------------------------------------------------------------------------
class Unknown(Exception):
    pass


def parse_n(d, i, n, tagmap, learned, counter, cap=CAP):
    """All ways to parse exactly n values from d[i:].

    Returns list of (end, fields, adds) where fields is the list of value nodes
    (('s'|'g'|'i'|'f'|'d'|'b'|'z', tag, value)) and adds maps any unknown tag
    discovered here to the payload size that made it work.
    """
    out = []
    if counter[0] > cap:
        return out

    def rec(i, count, fields, adds):
        if counter[0] > cap:
            return
        if count == n:
            out.append((i, fields, dict(adds)))
            return
        if i >= len(d):
            return
        t = d[i]
        k = tagmap.get(t)
        if k is None:
            k = learned.get(t)
        if k is None:
            # unknown id -> try every candidate payload size 0..MAXSZ
            for s in range(0, MAXSZ + 1):
                e = i + 1 + s
                if e > len(d):
                    break
                counter[0] += 1
                a2 = dict(adds)
                a2[t] = s
                rec(e, count + 1, fields + [("b", t, d[i + 1:e])], a2)
            return
        if k == "str":
            if i + 5 > len(d):
                return
            (c,) = struct.unpack_from("<I", d, i + 1)
            e = i + 5 + c * 2
            if c > 0x2000 or e > len(d):
                return
            counter[0] += 1
            rec(e, count + 1,
                fields + [("s", t, d[i + 5:e].decode("utf-16-le", "replace"))],
                adds)
            return
        if k == "g8":
            if i + 2 > len(d):
                return
            nn = d[i + 1]
            j = i + 2
            res = parse_n(d, j, nn, tagmap, learned, counter)
            if not res:
                return
            end, ch, _ = res[0]
            counter[0] += 1
            rec(end, count + 1, fields + [("g", t, ch)], adds)
            return
        if k == "g32":
            if i + 5 > len(d):
                return
            (nn,) = struct.unpack_from("<I", d, i + 1)
            if nn > 0x100000:
                return
            j = i + 5
            res = parse_n(d, j, nn, tagmap, learned, counter)
            if not res:
                return
            end, ch, _ = res[0]
            counter[0] += 1
            rec(end, count + 1, fields + [("g", t, ch)], adds)
            return
        if k == 8:
            e = i + 9
            if e > len(d):
                return
            counter[0] += 1
            rec(e, count + 1, fields + [("d", t, struct.unpack_from("<d", d, i + 1)[0])], adds)
            return
        # scalar of size k
        e = i + 1 + k
        if e > len(d):
            return
        counter[0] += 1
        if k == 4 and t in (0x12,):
            val = struct.unpack_from("<i", d, i + 1)[0]
        elif k == 4 and t in (0x13, 0x16):
            val = struct.unpack_from("<f", d, i + 1)[0]
        elif k == 0:
            val = t
        else:
            val = int.from_bytes(d[i + 1:e], "little")
        rec(e, count + 1, fields + [("b", t, val)], adds)

    rec(i, 0, [], {})
    return out


def record_boundary(d, start, M):
    """Yield candidate end offsets at which record `start` could close: every
    position holding the next record header `0x40 <M>`, plus EOF."""
    cands = []
    p = start
    n = len(d)
    while True:
        p = d.find(b"\x40", p, n)
        if p < 0:
            break
        if p + 1 < n and d[p + 1] == M:
            cands.append(p)
        p += 1
    return cands


def solve_blob(name, tagmap):
    d = open(os.path.join(VB, name + ".bin"), "rb").read()
    n = len(d)
    learned = {}
    # ---- header: file is a group of N records ----
    if d[0] == 0x41:
        (N,) = struct.unpack_from("<I", d, 1)
        pos = 5
    elif d[0] == 0x40:
        N = d[1]
        pos = 2
    else:
        return None, None, {"error": "no g8/g32 header (first byte 0x%02X)" % d[0]}

    # first record header gives M (column count)
    if not (pos + 2 <= n and d[pos] in (0x40, 0x41)):
        return None, None, {"error": "bad first record header at %d" % pos}
    if d[pos] == 0x41:
        (M,) = struct.unpack_from("<I", d, pos + 1)
        rhead = 5
    else:
        M = d[pos + 1]
        rhead = 2

    records = []
    ok = 0
    first_fail = None
    col_tags = defaultdict(Counter)   # position -> Counter(field id)

    for r in range(N):
        if pos >= n:
            break
        if not (d[pos] in (0x40, 0x41) and
                ((d[pos] == 0x40 and d[pos + 1] == M) or
                 (d[pos] == 0x41 and struct.unpack_from("<I", d, pos + 1)[0] == M))):
            # record header does not match expected M -> desync, try resync
            if first_fail is None:
                first_fail = (r, pos, d[pos])
            break
        start = pos + rhead
        cands = record_boundary(d, start, M)
        if r == N - 1:
            cands = cands + [n]
        solutions = []
        reason = ""
        for end in cands:
            counter = [0]
            res = parse_n(d, start, M, tagmap, learned, counter)
            for e, fields, adds in res:
                if e == end:
                    solutions.append((e, fields, adds))
            if solutions:
                break
        if not solutions:
            if first_fail is None:
                first_fail = (r, start, d[start] if start < n else -1)
            break
        # disambiguate multiple solutions via next-record consistency
        if len(solutions) > 1:
            kept = []
            for e, fields, adds in solutions:
                trial = dict(tagmap)
                trial.update(learned)
                trial.update(adds)
                if _next_record_ok(d, e, M, N, r, trial, learned):
                    kept.append((e, fields, adds))
            if kept:
                solutions = kept
        e, fields, adds = solutions[0]
        for t, s in adds.items():
            learned[t] = s
        for j, (kind, tag, val) in enumerate(fields):
            col_tags[j][tag] += 1
        records.append(fields)
        ok += 1
        pos = e

    info = {"N": N, "M": M, "ok": ok, "first_fail": first_fail,
            "learned": {hex(t): s for t, s in learned.items()},
            "col_tags": {j: {hex(t): c for t, c in col_tags[j].items()}
                         for j in col_tags}}
    return records, M, info


def _next_record_ok(d, e, M, N, r, trial, learned):
    """Does the record that should start at `e` parse cleanly (anchored)?"""
    if e >= len(d):
        return True                       # EOF after last record
    if not (d[e] == 0x40 and d[e + 1] == M):
        return False
    start = e + 2
    cands = record_boundary(d, start, M)
    for end in cands:
        counter = [0]
        res = parse_n(d, start, M, trial, learned, counter)
        if any(ev == end for ev, _, _ in res):
            return True
    return False


# ---------------------------------------------------------------------------
# CSV rendering
# ---------------------------------------------------------------------------
def flat_cell(v):
    kind, tag, x = v
    if kind == "s":
        return x
    if kind == "g":
        return "|".join(flat_cell(c) for c in x)
    if kind == "f":
        return f"{x:.6g}"
    if kind == "d":
        return f"{x:.6g}"
    if kind == "i":
        return str(x)
    if kind == "z":
        return str(x)
    if kind == "b":
        return f"0x{x:02X}" if isinstance(x, int) and x < 256 and tag in ZERO else str(x)
    return str(x)


def export(name, records, M, info, schema_cols=None):
    base = load_tags_for_names()
    # column headers: real names if table known, else positional
    if schema_cols and len(schema_cols) == M:
        headers = list(schema_cols)
    else:
        headers = ["FIELD_%d" % j for j in range(M)]
    # rename columns dominated by an unknown (non-base) tag to UNKNOWN_k
    unknown_idx = 0
    for j in range(M):
        tags_here = info["col_tags"].get(j, {})
        dom = max(tags_here.items(), key=lambda kv: kv[1])[0] if tags_here else None
        if dom is not None and int(dom, 16) not in base:
            headers[j] = "UNKNOWN_%d" % (unknown_idx + 1)
            unknown_idx += 1
    path = os.path.join(OUT, name + ".csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for rec in records:
            row = [flat_cell(v) for v in rec[:M]]
            if len(row) < M:
                row += [""] * (M - len(row))
            w.writerow(row)
    return path, headers


_BASE = None


def load_tags_for_names():
    global _BASE
    if _BASE is None:
        _BASE = set(base_tags())
    return _BASE


# ---------------------------------------------------------------------------
def main():
    tagmap = base_tags()
    ident = json.load(open(IDENT, encoding="utf-8"))
    schemas = json.load(open(SCHEMA, encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)
    targets = ["0580853e", "66d2c4f3", "d07f4acc", "433267c3", "e15a7343", "c41c9276"]

    report = {}
    for name in targets:
        info_i = ident.get(name, {})
        table = info_i.get("table")
        schema_cols = None
        if table and table in schemas:
            cols = schemas[table]["columns"]
            cols = [c for c in cols if c is not None]
            schema_cols = cols
        print(f"\n=== {name}  (identity table={table}, "
              f"cols={info_i.get('columns')}, recs={info_i.get('records')}) ===")
        records, M, info = solve_blob(name, tagmap)
        if records is None:
            print("  SKIP:", info.get("error"))
            report[name] = {"status": "skip", "reason": info.get("error")}
            continue
        print(f"  recovered {info['ok']}/{info['N']} records, M={M}")
        print(f"  learned tags: {info['learned']}")
        if info["first_fail"]:
            ff = info["first_fail"]
            print(f"  first unresolved record: #{ff[0]} at byte {ff[1]} (tag 0x{ff[2]:02X})")
        path, headers = export(name, records, M, info, schema_cols)
        unk = sum(1 for h in headers if h.startswith("UNKNOWN_"))
        print(f"  wrote {path}  ({len(headers)} cols, {unk} unknown-named)")
        report[name] = {
            "status": "partial" if info["first_fail"] else "ok",
            "records": info["ok"], "total": info["N"], "columns": M,
            "unknown_cols": unk,
            "learned_tags": info["learned"],
            "first_fail": list(info["first_fail"]) if info["first_fail"] else None,
            "table": table,
        }
    with open(os.path.join(OUT, "README_varbin6.md"), "w", encoding="utf-8") as f:
        f.write(render_report(report))
    print("\nwrote", os.path.join(OUT, "README_varbin6.md"))


def render_report(report):
    lines = ["# varbin6 -- recovery of the six desyncing blobs", ""]
    lines.append("Method: per-record anchored solver (each record is a `0x40 <M>` "
                 "group of exactly M column values; the byte after it must be the "
                 "next record's `0x40 <M>` header or EOF).  Ambiguous 2-id gaps are "
                 "resolved by requiring the following record to also parse.")
    lines.append("")
    lines.append("| blob | table | columns | recovered / total | unknown-named cols | status |")
    lines.append("|---|---|---|---|---|---|")
    for name, r in report.items():
        if r.get("status") == "skip":
            lines.append(f"| {name} | - | - | - | - | skip: {r['reason']} |")
            continue
        lines.append(f"| {name} | {r.get('table') or '-'} | {r['columns']} | "
                     f"{r['records']}/{r['total']} | {r['unknown_cols']} | {r['status']} |")
    lines.append("")
    lines.append("## per-blob detail")
    for name, r in report.items():
        lines.append(f"\n### {name}")
        if r.get("status") == "skip":
            lines.append(f"  skipped: {r['reason']}")
            continue
        lines.append(f"  - table: {r.get('table') or 'UNIDENTIFIED'}")
        lines.append(f"  - columns: {r['columns']}, records recovered: "
                     f"{r['records']}/{r['total']}")
        lines.append(f"  - columns named UNKNOWN_*: {r['unknown_cols']}")
        if r["first_fail"]:
            lines.append(f"  - first unresolved record: #{r['first_fail'][0]} "
                         f"at byte {r['first_fail'][1]}")
        lines.append(f"  - field ids resolved (id: size): {r['learned_tags']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
