#!/usr/bin/env python3
"""Decode .cmz `sbus` skin blobs - the per-vertex mesh data.

Recovered by disassembling the record parser at VA 0x59A640 in
`gbox/lm_en/chronotales.exe` (called once per part from 0x59A2E0, stride
0x88 = 136 bytes in memory).  The file layout is **packed** - a u8 flag sits
between u32 fields, so every earlier 4-byte-aligned reading of the header was
misaligned by design.

Header (offsets are byte offsets from the `sbus` tag):

    0   u32  'sbus'
    4   u32  part_index
    8   u32  0
    12  u8   1                 <- breaks 4-byte alignment; everything after
                                  is read unaligned
    13  u32  n_vertices
    17  u32  unknown
    21  u32  unknown
    25  u32  unknown           (count of an 8-byte-element side array)
    29  u32  n_uv_sets
    33  u8   wide_flag         != 0 -> influence count is u16, else u8
    34  f32  base_x .. step_x, base_y, step_y, base_z, step_z   (6 floats)

Per vertex:

    u16 px, py, pz        position,  pos = u16 * step + base   (x0.01)
    u16 nx, ny, nz        normal,    nrm = u16 * 3.05185e-5 - 1.0
    n_uv_sets x (u16,u16) texture coordinates, uv = u16 * 3.05185e-5
    u8/u16 n              number of bone influences
    n x (u8 bone, u16 weight)          weight = u16 * 1e-4  (10000 = 1.0)

Only the first 4 influences are kept by the engine (`cmp esi,4; jge`).
Weights of 0 or 1 are skipped (`cmp ax,1; jbe`), so a lone weight of 1 means
"no influence".

Usage:
    python sbus_decode.py [--root DIR] [--out DIR]
"""
import argparse
import json
import pathlib
import struct

SCALE = 0.01            # VA 0x6D0738
NORMAL_K = 3.0518509447574615e-05   # VA 0x6D073C  (2/65535)
NORMAL_C = 1.0          # VA 0x6CEC4C
WEIGHT_K = 9.999999747378752e-05    # VA 0x6D457C  (1e-4)


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


class Reader:
    def __init__(self, b):
        self.b = b
        self.p = 0

    def u8(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.b, self.p)[0]
        self.p += 2
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.p)[0]
        self.p += 4
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.b, self.p)[0]
        self.p += 4
        return v


def decode(blob):
    r = Reader(blob)
    if r.u32() != 0x73756273:
        return None
    out = {"part_index": r.u32()}
    zero = r.u32()
    flag1 = r.u8()
    if zero != 0 or flag1 != 1:
        return None
    out["n_verts"] = r.u32()
    out["unk17"] = r.u32()
    out["unk21"] = r.u32()
    out["unk25"] = r.u32()
    out["n_uv"] = r.u32()
    wide = r.u8() != 0
    bx, sx = r.f32() * SCALE, r.f32() * SCALE
    by, sy = r.f32() * SCALE, r.f32() * SCALE
    bz, sz = r.f32() * SCALE, r.f32() * SCALE
    out["base"] = [bx, by, bz]
    out["step"] = [sx, sy, sz]
    out["wide_influence"] = wide

    out["n_faces"] = out["unk17"]

    verts = []
    for _ in range(out["n_verts"]):
        px = r.u16() * sx + bx
        py = r.u16() * sy + by
        pz = r.u16() * sz + bz
        nx = r.u16() * NORMAL_K - NORMAL_C
        ny = r.u16() * NORMAL_K - NORMAL_C
        nz = r.u16() * NORMAL_K - NORMAL_C
        uv = []
        for k in range(out["n_uv"]):
            a = r.u16()
            b2 = r.u16()
            if k == 0:
                uv = [a * NORMAL_K, b2 * NORMAL_K]
        n = r.u16() if wide else r.u8()
        inf = []
        for _ in range(n):
            bone = r.u8()
            w = r.u16()
            inf.append((bone, w * WEIGHT_K))
        verts.append(dict(pos=[px, py, pz], nrm=[nx, ny, nz], uv=uv,
                          bones=[b for b, _ in inf],
                          weights=[w for _, w in inf]))
    out["verts"] = verts

    # ---- index buffer ----------------------------------------------------
    # n_vertices < 1024 -> one u32 per triangle holding three 10-bit indices
    # (the engine tests `cmp n_verts, 0x400` at 0x59AFB8); otherwise three
    # plain u16.  Count is header field F5.
    faces = []
    packed = out["n_verts"] < 1024
    for _ in range(out["n_faces"]):
        if packed:
            w = r.u32()
            faces.append([w & 0x3FF, (w >> 10) & 0x3FF, (w >> 20) & 0x3FF])
        else:
            faces.append([r.u16(), r.u16(), r.u16()])
    out["faces"] = faces
    out["packed_indices"] = packed
    out["consumed"] = r.p
    out["blob_len"] = len(blob)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path,
                    default=pathlib.Path("extracted/netpak"))
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path("extracted/models/cmz"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for p in sorted(a.root.rglob("*.cmz")):
        d = p.read_bytes()
        mesh = next((b for b in chunk_bounds(d) if b[1] == "MESH"), None)
        print(f"\n### {p.stem}")
        if not mesh:
            print("  no MESH")
            continue
        off, _, end = mesh
        for k, blob in enumerate(blobs(d, off + 8, end)):
            r = decode(blob)
            if r is None:
                print(f"  part {k}: bad tag")
                continue
            v = r["verts"]
            xs = [q["pos"][0] for q in v] or [0]
            ys = [q["pos"][1] for q in v] or [0]
            zs = [q["pos"][2] for q in v] or [0]
            nb = max((max(q["bones"], default=0) for q in v), default=0)
            wsum = [sum(q["weights"]) for q in v]
            ok_w = sum(1 for w in wsum if 0.98 <= w <= 1.02)
            exact = "OK " if r["consumed"] == r["blob_len"] else "TAIL"
            print(f"  part {k}: verts={r['n_verts']} uv={r['n_uv']} "
                  f"consumed={r['consumed']}/{r['blob_len']} {exact} "
                  f"maxbone={nb}")
            print(f"      extent x[{min(xs):.2f},{max(xs):.2f}] "
                  f"y[{min(ys):.2f},{max(ys):.2f}] z[{min(zs):.2f},{max(zs):.2f}]"
                  f"  weights~1: {ok_w}/{len(v)}")
        (a.out / (p.stem + ".skin.json")).write_text(
            json.dumps([decode(b) for b in blobs(d, off + 8, end)],
                       ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
