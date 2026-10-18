#!/usr/bin/env python3
"""Score every field on the 20-byte `sbus` grid as coordinate / bone / weight.

For each byte offset 0..19 (step 4) treat the column as f32 and report how
often it lands in a plausible coordinate range for a character mesh
(|v| < 5 metres).  For each u16 column report how often it is bone-like
(< bone count) or weight-like (<= 10000).  A real field stands out sharply.
"""
import pathlib
import struct
import sys

STRIDE = 20


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


def main():
    bones = {"2682c5d5": 43}
    for fn in sys.argv[1:]:
        p = pathlib.Path(fn)
        d = p.read_bytes()
        nb = bones.get(p.stem, 119)
        mesh = next((b for b in chunk_bounds(d) if b[1] == "MESH"), None)
        if not mesh:
            continue
        off, _, end = mesh
        print("=" * 92)
        print(f"{p.stem}  (bones={nb})")
        # aggregate over every blob, using the 4-byte prefix + 20-byte grid
        for k, blob in enumerate(blobs(d, off + 8, end)):
            if len(blob) < 32:
                continue
            hdr = struct.unpack_from("<7I", blob, 4)
            pay = blob[32:]
            start = 4
            n = (len(pay) - start) // STRIDE
            if n < 5:
                continue
            print(f"\n  sbus[{k}] part={hdr[0]} A={hdr[2]} B={hdr[3]} "
                  f"cap={hdr[6]} recs={n}")
            print("    off  f32: |v|<5   |v|<100  median     "
                  "u16: <bonecap  <=10000  ==10000")
            for o in range(0, STRIDE, 4):
                fs = [struct.unpack_from("<f", pay, start + i * STRIDE + o)[0]
                      for i in range(n)]
                ok5 = sum(1 for v in fs if abs(v) < 5) / n
                ok100 = sum(1 for v in fs if abs(v) < 100) / n
                srt = sorted(fs)
                med = srt[n // 2]
                us = [struct.unpack_from("<H", pay, start + i * STRIDE + o)[0]
                      for i in range(n)]
                b1 = sum(1 for v in us if v < nb) / n
                w1 = sum(1 for v in us if v <= 10000) / n
                w2 = sum(1 for v in us if v == 10000) / n
                print(f"    {o:3}   {ok5:6.0%} {ok100:7.0%}  {med:10.4g}   "
                      f"  {b1:7.0%} {w1:8.0%} {w2:8.0%}")
            for o in range(2, STRIDE, 4):
                us = [struct.unpack_from("<H", pay, start + i * STRIDE + o)[0]
                      for i in range(n)]
                b1 = sum(1 for v in us if v < nb) / n
                w1 = sum(1 for v in us if v <= 10000) / n
                print(f"    {o:3}  (odd u16)                              "
                      f"  {b1:7.0%} {w1:8.0%}")


if __name__ == "__main__":
    main()
