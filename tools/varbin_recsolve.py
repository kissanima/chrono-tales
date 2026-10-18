#!/usr/bin/env python3
"""Per-record solver with next-record-start anchoring.

Every record is a group `0x40 g8 n` of exactly `ncols` values.  A candidate
payload size for an unknown tag is correct only if:
  * the record parses to exactly ncols values, and
  * the byte right after is the next record's `0x40 ncols` header (or EOF).
Branching happens only at unknown tag bytes; strings must decode to
ASCII/CJK text (guards against false string hits inside float data).
"""
import os
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from varbin_solve2 import base_tags, blob_bytes  # noqa: E402


def good_str(b):
    """UTF-16LE bytes -> str if it decodes to plausible game text."""
    if len(b) % 2:
        return None
    try:
        s = b.decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    if not s:
        return None
    for ch in s:
        o = ord(ch)
        if 0x20 <= o <= 0x7E:
            continue
        if o in (0x3000, 0x3001, 0x3002) or 0x4E00 <= o <= 0x9FFF:
            continue
        if o in (0xFF01, 0xFF08, 0xFF09, 0xFF0C, 0xFF1A, 0xFF1B):
            continue
        if o in (0x2018, 0x2019, 0x201C, 0x201D, 0x2026, 0x00B7):
            continue
        return None
    return s


def parse_group(d, i, n, tags, out, depth=0):
    """Yield (end, ok). out receives (kind, tag, value) tuples."""
    if len(out) >= n:
        yield i, True
        return
    for end, node in parse_value(d, i, tags, out):
        if end is None:
            continue
        yield end, False


def parse_value(d, i, tags, out):
    """Yield (end, is_group_close) parses of one value; out gets field tuples."""
    if i >= len(d):
        return
    t = d[i]
    k = tags.get(t)
    if k is None:
        for s in range(0, 17):
            e = i + 1 + s
            if e <= len(d):
                yield e, ("n", t, s)
        return
    if k == "str":
        if i + 5 > len(d):
            return
        (c,) = struct.unpack_from("<I", d, i + 1)
        e = i + 5 + c * 2
        if c > 0x2000 or e > len(d):
            return
        s = good_str(d[i + 5:e])
        if s is None:
            return
        yield e, ("s", t, s)
        return
    if k in ("g8", "g32"):
        if k == "g8":
            if i + 2 > len(d):
                return
            n, j = d[i + 1], i + 2
        else:
            if i + 5 > len(d):
                return
            (n,) = struct.unpack_from("<I", d, i + 1)
            if n > 0x100000:
                return
            j = i + 5
        if len(out) + 1 > 0:
            pass
        # group counts as ONE field; parse children for value extraction but
        # keep flat control
        kids = []

        def tail(jj, remaining, kids):
            if remaining == 0:
                yield jj, kids
                return
            for j2, kid in parse_value(d, jj, tags, []):
                yield from tail(j2, remaining - 1, kids + [kid])

        for end, ks in tail(j, n, []):
            yield end, ("g", t, ks)
        return
    e = i + 1 + k
    if e > len(d):
        return
    yield e, ("n", t, k)


def solve_records(name, ncols, tags, limit_records=400):
    """Solve per-record; returns (cons, tagmap, records_parsed)."""
    d = blob_bytes(name)
    n = len(d)
    assert d[0] in (0x40, 0x41), hex(d[0])
    if d[0] == 0x41:
        (nrec,) = struct.unpack_from("<I", d, 1)
        start = 5
    else:
        nrec, start = d[1], 2
    print(f"  {name}: declared {nrec} records x {ncols} cols, first rec @{start}")

    assigned = {}
    recs = []
    pos = start
    ok_recs = 0
    for r in range(min(nrec, limit_records)):
        sols = parse_record(d, pos, ncols, dict(tags, **assigned))
        if not sols:
            print(f"  record {r}: FAILED with current map "
                  f"{ {hex(t): s for t, s in assigned.items()} }")
            return None, assigned, ok_recs
        if len(sols) > 1:
            # ambiguous: try each, keep the one that also parses NEXT record
            best = None
            for end, fields, add in sols:
                nextstart = check_next(d, end, ncols, r == nrec - 1)
                if nextstart is not None and best is None:
                    best = (end, fields, add, nextstart)
            if best is None:
                print(f"  record {r}: {len(sols)} ambiguous parses, none "
                      f"confirmable by next-record header")
                for end, fields, add in sols[:5]:
                    nxt = check_next(d, end, ncols, r == nrec - 1)
                    print(f"    end={end} add={add} next_ok={nxt is not None}")
                return None, assigned, ok_recs
            end, fields, add, nxt = best
            pos = nxt[1]
        else:
            end, fields, add = sols[0]
            nxt = check_next(d, end, ncols, r == nrec - 1)
            if nxt is None:
                print(f"  record {r}: unique parse but next-record header "
                      f"missing at {end} (ctx {d[end:end+6].hex()})")
                return None, assigned, ok_recs
            pos = nxt
        for t, s in add.items():
            assigned[t] = s
        recs.append(fields)
        ok_recs += 1
    return recs, assigned, ok_recs


def check_next(d, end, ncols, is_last):
    if is_last:
        return ("EOF", len(d)) if end == len(d) else None
    if end + 2 <= len(d) and d[end] == 0x40 and d[end + 1] == ncols:
        return ("rec", end)
    return None


def parse_record(d, start, ncols, tags, cap=4000):
    """All parses of one record (g8 ncols) at start -> [(end, fields, newtags)]"""
    results = []
    counter = [0]

    def rec_field(i, count, fields, added):
        if counter[0] > 200000:
            return
        if count == ncols:
            results.append((i, fields, dict(added)))
            return
        for end, node in parse_value(d, i, tags, []):
            counter[0] += 1
            t = node[1]
            add = added
            if t not in tags and node[0] == "n":
                add = dict(added)
                add[t] = node[2]
            yield from rec_field(end, count + 1, fields + [node], add)
        return

    # need generator; write iteratively
    def gen(i, count, fields, added):
        if counter[0] > 200000:
            return
        if count == ncols:
            yield i, fields, dict(added)
            return
        for end, node in parse_value(d, i, tags, []):
            counter[0] += 1
            t = node[1]
            add = added
            if node[0] == "n" and t not in tags:
                add = dict(added)
                add[t] = node[2]
            yield from gen(end, count + 1, fields + [node], add)

    for r in gen(start, 0, [], {}):
        results.append(r)
        if len(results) >= cap:
            break
    return results


def main():
    tags = base_tags()
    for name, ncols in [("0580853e", 66), ("66d2c4f3", 3), ("d07f4acc", 27)]:
        print(f"\n=== {name} ===")
        recs, assigned, n = solve_records(name, ncols, tags, limit_records=2000)
        print(f"  solved {n} records; tag map: "
              f"{ {hex(t): s for t, s in assigned.items()} }")
        if recs:
            print(f"  sample fields (record 0): "
                  f"{[(f[0], hex(f[1]), str(f[2])[:20]) for f in recs[0][:8]]}")


if __name__ == "__main__":
    main()
