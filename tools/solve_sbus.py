#!/usr/bin/env python3
"""Brute-force the sbus skin-record format.

We know:
- bone indices must be < bone_count (43 mech / 119 humanoid)
- weights are in [0,1]; quantized forms likely 0..0x7fff (32767) or 0..0xffff (65535)

We test, for each sbus blob, every group size g (u16 per vertex) and every field
position p, whether field p is "all bone-like" (< bone_count) or "all weight-like".
A valid format should show a field that is ~100% bone-like and another ~100% weight-like.
"""
import struct, re, json, sys

BONE_LIMIT = {"2682c5d5": 43, "482f1708": 109, "5995da06": 119,
              "5e493068": 119, "72b5ee25": 119, "a7b5710e": 119,
              "d1500c0c": 119, "f2d259a2": 119, "ff725ba4": 119}

def gbk(b):
    return b.split(b"\x00")[0].decode("gbk", "replace")

def sbus_blobs(path):
    d = path.read_bytes()
    me = d.find(b"1PAM")
    if me < 0:
        me = len(d)
    # collect sbus positions inside MESH chunk
    mesh = d.find(b"HSEM")
    poss = [m.start() for m in re.finditer(b"sbus", d) if mesh < m.start() < me]
    blobs = []
    for k, st in enumerate(poss):
        en = poss[k + 1] if k + 1 < len(poss) else me
        blobs.append(d[st:en])
    return blobs

def analyze(blob, bone_limit):
    rec = blob
    # header 32 bytes: sbus(4) + 7 u32
    hdr_u32 = [struct.unpack_from("<I", rec, 4 + 4 * i)[0] for i in range(7)]
    pay = rec[32:]
    us = [struct.unpack_from("<H", pay, o)[0] for o in range(0, len(pay) - 1, 2)]
    n = len(us)
    print(f"  blob total {len(rec)} header u32={hdr_u32} payload u16={n} cap={hdr_u32[6]}")
    # ---- u16 view ----
    best = []
    for g in range(1, 9):
        if n % g:
            continue
        nv = n // g
        for p in range(g):
            field = us[p::g]
            if all(v < bone_limit for v in field):
                best.append((16, g, p, "BONE-ALL", nv))
            if all(v <= 0x7fff for v in field):
                best.append((16, g, p, "W32767-ALL", nv))
    # ---- u8 view (bonecap hints 8-bit bone indices) ----
    ub = list(pay)
    m = len(ub)
    for g in (2, 4, 8):
        if m % g:
            continue
        nv = m // g
        for p in range(g):
            field = ub[p::g]
            if all(v < bone_limit for v in field):
                best.append((8, g, p, "BONE-ALL", nv))
            if all(v <= 255 for v in field):
                best.append((8, g, p, "W8-ALL", nv))
    seen = set()
    for w, g, p, tag, nv in best:
        key = (w, g, p, tag)
        if key in seen:
            continue
        seen.add(key)
        print(f"    {w}-bit group={g} field[{p}] -> {tag}  (vertices={nv})")
    if not best:
        print("    (no clean fixed-stride interpretation)")
    return best

def main():
    import pathlib
    root = pathlib.Path("extracted/netpak/unknown")
    files = sys.argv[1:] or [f"{g}.cmz" for g in BONE_LIMIT]
    for fn in files:
        p = root / fn
        if not p.exists():
            continue
        bl = BONE_LIMIT.get(p.stem, 119)
        print(f"\n### {p.stem} (bone_limit={bl})")
        for i, blob in enumerate(sbus_blobs(p)):
            print(f"  -- blob {i}")
            analyze(blob, bl)

if __name__ == "__main__":
    main()
