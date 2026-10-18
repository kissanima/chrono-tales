#!/usr/bin/env python3
"""Greedy sweep trace: at each step, assign every candidate size to the failing
tag and report which sizes reach the furthest."""
import sys

sys.path.insert(0, "tools")
from varbin_solve2 import base_tags, blob_bytes, first_failure, MAXS


def trace(name):
    tags = base_tags()
    d = blob_bytes(name)
    assigned = {}
    for step in range(40):
        off, tag = first_failure(d, dict(tags, **assigned))
        if tag is None:
            print(f"[{name}] EOF-exact after {len(assigned)} assignments: "
                  f"{ {hex(t): s for (t, s) in assigned.items()} }")
            return dict(assigned)
        ctx = d[off:off + 12].hex()
        results = []
        for s in range(0, MAXS + 1):
            if any(t == tag for t in assigned):
                continue
            if tags.get(tag) == s:
                continue
            trial = dict(tags, **assigned)
            trial[tag] = s
            o2, t2 = first_failure(d, trial)
            results.append((o2, s, t2))
        results.sort(reverse=True)
        print(f"[{name}] step {step}: fail @{off:06x} tag 0x{tag:02X} ctx={ctx}")
        for o2, s, t2 in results[:4]:
            print(f"    size {s:2d} -> next fail @{o2:06x}"
                  f"{' (EOF)' if t2 is None else f' tag 0x{t2:02X}'}")
        best = results[0]
        if best[0] <= off:
            print("    !! no size gets further -- stop")
            return assigned
        assigned[(tag, best[1])] = best[1]


for n in ["0580853e", "66d2c4f3", "d07f4acc"]:
    trace(n)
    print()
