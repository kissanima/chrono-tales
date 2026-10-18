#!/usr/bin/env python3
"""Lay a .cmz `sbus` payload out on its 20-byte record grid.

The stride was found statistically: byte autocorrelation peaks at lag 20 (and
40/60/80), and the recurring u16 0x2710 (=10000, i.e. weight 1.0 in basis
points) appears at intervals of exactly 20 bytes.  This script aligns the grid
so that 0x2710 lands in a single column, then prints each record as u16 x10 /
u8 x20 / f32 x5 so the field roles can be read off directly.
"""
import collections
import pathlib
import struct
import sys


def chunk_bounds(d):
    hits = []
    for i in range(8, len(d) - 4):
        s = d[i:i + 4][::-1]
        if s in (b"skel", b"MESH", b"MAP1", b"END."):
            if not hits or i >= hits[-1][0] + 4:
                hits.append((i, s.decode("latin1")))
    return [(o, t, hits[k + 1][0] if k + 1 < len(hits) else len(d))
            for k, (o, t) in enumerate(hits)]


def blobs(d, start, end):
    pos, i, out = [], start, []
    while True:
        j = d.find(b"sbus", i, end)
        if j < 0:
            break
        pos.append(j)
        i = j + 1
    for k, s in enumerate(pos):
        e = pos[k + 1] if k + 1 < len(pos) else end
        out.append(d[s:e])
    return out


def find_alignment(pay, stride, token=b"\x10\x27"):
    """Offset r such that every `token` sits at positions == r (mod stride)."""
    ps = [i for i in range(len(pay) - 1) if pay[i:i + 2] == token]
    if not ps:
        return 0, 0
    mods = collections.Counter(p % stride for p in ps)
    r, n = mods.most_common(1)[0]
    return r, n


def main():
    files = sys.argv[1:]
    stride = 20
    for fn in files:
        p = pathlib.Path(fn)
        d = p.read_bytes()
        mesh = next((b for b in chunk_bounds(d) if b[1] == "MESH"), None)
        print("=" * 100)
        print(p.stem)
        if not mesh:
            continue
        off, _, end = mesh
        for k, blob in enumerate(blobs(d, off + 8, end)):
            if len(blob) < 32:
                continue
            hdr = struct.unpack_from("<7I", blob, 4)
            pay = blob[32:]
            r, n = find_alignment(pay, stride)
            print(f"\n  sbus[{k}] pay={len(pay)} part={hdr[0]} A={hdr[2]} "
                  f"B={hdr[3]} cap={hdr[6]}  stride={stride} align={r} "
                  f"({n} hits) recs={(len(pay) - r) // stride} "
                  f"tail={(len(pay) - r) % stride}")
            print(f"    pre-grid bytes: {pay[:r].hex() if r else '-'}")
            print("    idx  " + " ".join(f"u16[{i}]" for i in range(10)))
            start = r - (r % 2)
            recs = (len(pay) - start) // stride
            for i in range(min(recs, 14)):
                o = start + i * stride
                u = struct.unpack_from("<10H", pay, o)
                print(f"    {i:4}  " + " ".join(f"{v:6}" for v in u))
            if recs > 14:
                print("    ...")
            # f32 view of one record
            o = start
            f = struct.unpack_from("<5f", pay, o)
            print("    f32[0] = " + " ".join(f"{v:.4g}" for v in f))
            print("    u8[0]  = " + " ".join(f"{c:3}" for c in pay[o:o + stride]))


if __name__ == "__main__":
    main()
