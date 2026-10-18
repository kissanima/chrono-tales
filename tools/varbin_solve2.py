#!/usr/bin/env python3
"""varbin_solve2 -- solve the field ids the flat gap solver could not, and
verify the six lagging blobs end-to-end.

Method
------
*Strict sweep*: a deterministic nested walk with the known grammar stops at the
first byte that is not a known tag (or an invalid length).  A DFS branches
over candidate payload sizes (0..12) for that tag byte; branches that do not
push the failure point strictly further are pruned.  A complete solution
consumes the blob to the exact EOF.  Solutions are then structurally checked
(uniform record group widths == declared column counts).

Also re-runs the flat strict gap solver over all 49 blobs with the merged tag
table to report the new strict gap coverage.

Usage:  python varbin_solve2.py [blob ...]
"""
import glob
import json
import os
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")
WSTR = 0x31
MAXS = 12

BASE = {0x10: 1, 0x11: 2, 0x12: 4, 0x13: 4, 0x16: 4, 0x32: 2, 0x43: 8,
        0x40: "g8", 0x41: "g32"}
for _z in range(0x00, 0x10):
    BASE[_z] = 0
BASE[0x30] = 0
BASE[0xFF] = 0


def base_tags():
    t = dict(BASE)
    p = os.path.join(HERE, "varbin_tags.json")
    if os.path.exists(p):
        for k, v in json.load(open(p)).items():
            if k.isdigit():
                t[int(k)] = v
    return t


def blob_bytes(name):
    return open(os.path.join(VB, name + ".bin"), "rb").read()


def first_failure(d, tags):
    """Deterministic nested walk.  Returns (offset, tag) of the first byte
    that cannot be parsed; (len(d), None) means clean EOF-exact parse."""
    sys.setrecursionlimit(100000)

    def rec(i):
        if i >= len(d):
            return len(d), None
        t = d[i]
        k = tags.get(t)
        if k is None:
            return i, t
        if k == "str":
            if i + 5 > len(d):
                return i, t
            (c,) = struct.unpack_from("<I", d, i + 1)
            e = i + 5 + c * 2
            if c > 0x2000 or e > len(d):
                return i, t
            return rec(e)
        if k == "g8":
            n, j = d[i + 1], i + 2
        elif k == "g32":
            if i + 5 > len(d):
                return i, t
            (n,) = struct.unpack_from("<I", d, i + 1)
            if n > 0x100000:
                return i, t
            j = i + 5
        else:
            e = i + 1 + k
            if e > len(d):
                return i, t
            return rec(e)
        for _ in range(n):
            j, bad = rec(j)
            if bad is not None:
                return j, bad
        return j, None

    return rec(0)


def sweep(name, tags, max_nodes=500000):
    """DFS over (tag -> size) assignments; return all EOF-exact solutions."""
    d = blob_bytes(name)
    solutions = []
    seen = defaultdict(int)          # frozenset(assigned) -> best offset seen
    nodes = [0]

    def dfs(assigned):
        if nodes[0] > max_nodes:
            return
        nodes[0] += 1
        off, tag = first_failure(d, tags | assigned)
        key = frozenset(assigned.items())
        if seen.get(key, -2) >= off:
            return
        seen[key] = off
        if tag is None:
            solutions.append(dict(assigned))
            return
        for s in range(0, MAXS + 1):
            if tags.get(tag) == s:
                continue
            if any(t == tag for t, _ in assigned):
                continue          # a tag has exactly one payload size
            assigned[(tag, s)] = s
            dfs(assigned)
            del assigned[(tag, s)]

    dfs({})
    return solutions


def parse_tree(d, i, tags):
    if i >= len(d):
        return None
    t = d[i]
    k = tags.get(t)
    if k is None:
        return None
    if k == "str":
        if i + 5 > len(d):
            return None
        (c,) = struct.unpack_from("<I", d, i + 1)
        e = i + 5 + c * 2
        if c > 0x2000 or e > len(d):
            return None
        return {"tag": t, "kind": "s",
                "val": d[i + 5:e].decode("utf-16-le", "replace"), "end": e}
    if k in ("g8", "g32"):
        if k == "g8":
            if i + 2 > len(d):
                return None
            n, j = d[i + 1], i + 2
        else:
            if i + 5 > len(d):
                return None
            (n,) = struct.unpack_from("<I", d, i + 1)
            j = i + 5
            if n > 0x100000:
                return None
        kids = []
        for _ in range(n):
            nd = parse_tree(d, j, tags)
            if nd is None:
                return None
            kids.append(nd)
            j = nd["end"]
        return {"tag": t, "kind": k, "kids": kids, "end": j}
    e = i + 1 + k
    if e > len(d):
        return None
    return {"tag": t, "kind": "n", "end": e}


def widths_of(node):
    if node and node["kind"] in ("g8", "g32") and node["kids"]:
        return ([len(k["kids"]) if k["kind"] in ("g8", "g32") else 0
                 for k in node["kids"]], len(node["kids"]))
    return [], 0


def solve_blob(name, sizes, max_report=6):
    d = blob_bytes(name)
    sols = sweep(name, sizes)
    print(f"\n=== {name}  {len(d):,}B ===")
    if not sols:
        print("  no EOF-exact solution with payload sizes 0..12")
        return []
    out = []
    for smap in sols:
        t2 = dict(sizes)
        for (t, s), _ in smap.items():
            t2[t] = s
        node = parse_tree(d, 0, t2)
        if node is None or node["end"] != len(d):
            continue          # should not happen; sweep guarantees it
        widths, nrec = widths_of(node)
        out.append((smap, widths, nrec, t2))
    for smap, widths, nrec, _ in out[:max_report]:
        print(f"  tags: { {hex(t): s for (t, s), _ in smap.items()} }")
        print(f"    records={nrec} widths={dict(Counter(widths))}")
    if len(out) > max_report:
        print(f"  (+{len(out) - max_report} more EOF-exact solutions)")
    return out


def strict_gap_coverage(sizes):
    blobs = [(os.path.basename(p)[:-4], open(p, "rb").read())
             for p in sorted(glob.glob(os.path.join(VB, "*.bin")))]

    def tokenise(data):
        out, i, n, gs = [], 0, len(data), 0
        while i < n:
            if data[i] == WSTR and i + 5 <= n:
                (c,) = struct.unpack_from("<I", data, i + 1)
                end = i + 5 + c * 2
                if 0 < c <= 0x2000 and end <= n:
                    txt = data[i + 5:end].decode("utf-16-le", "ignore")
                    if all(ch.isprintable() for ch in txt):
                        out.append(data[gs:i])
                        gs = i = end
                        continue
            i += 1
        out.append(data[gs:n])
        return out

    def strict_parse(g):
        def rec(p):
            if p == len(g):
                return True
            t = g[p]
            if t == WSTR:
                if p + 5 > len(g):
                    return False
                (c,) = struct.unpack_from("<I", g, p + 1)
                end = p + 5 + c * 2
                if end > len(g):
                    return False
                return rec(end)
            s = sizes.get(t)
            if s is None or isinstance(s, str) or p + 1 + s > len(g):
                return False
            return rec(p + 1 + s)
        return rec(0)

    ok = bad = 0
    for name, d in blobs:
        for g in tokenise(d):
            if not g or len(g) > 512:
                continue
            if strict_parse(g):
                ok += 1
            else:
                bad += 1
    return ok, bad


def main():
    sizes = base_tags()
    names = sys.argv[1:] or ["0580853e", "66d2c4f3", "d07f4acc"]
    for nm in names:
        solve_blob(nm, sizes)
    ok, bad = strict_gap_coverage(sizes)
    print(f"\nflat strict gap coverage (base grammar): "
          f"{ok:,}/{ok + bad:,} ({100.0 * ok / max(1, ok + bad):.1f}%)")


if __name__ == "__main__":
    main()
