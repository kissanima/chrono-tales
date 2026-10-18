#!/usr/bin/env python3
"""Decode .cmz `sbus` skin blobs - the per-vertex mesh data.

Recovered by disassembling the record parser at VA 0x59A640 in
`gbox/lm_en/chronotales.exe` (called once per part from 0x59A2E0, stride
0x88 = 136 bytes in memory).  The file layout is **packed** - a u8 flag sits
between u32 fields, so every earlier 4-byte-aligned reading of the header was
misaligned by design.

Two header variants exist.  The loader reads a marker word at offset 8:

  * marker == 0 (variant 2, used by every shipped file) - the marker is
    followed by the u8 flag byte, then by n_vertices;
  * marker != 0 (variant 1, not present in this build) - the marker word
    *is* n_vertices and there is no flag byte and no `wide` byte.

Variant 2 header (byte offsets from the `sbus` tag):

    0   u32  'sbus'
    4   u32  part_index
    8   u32  0                 <- variant selector
    12  u8   1                 <- flag; breaks 4-byte alignment, so every
                                  field after this is read unaligned
    13  u32  n_vertices
    17  u32  n_faces
    21  u32  unknown  -> this+0x10     (0 in all 15 shipped parts)
    25  u32  unknown  -> count of an 8-byte-element container at this+0x24
                          (also 0 in all 15 shipped parts)
    29  u32  n_uv_sets
    33  u8   wide_flag         != 0 -> influence count is u16, else u8
    34  f32  base_x, step_x, base_y, step_y, base_z, step_z   (6 floats)

Per vertex:

    u16 px, py, pz        position,  pos = u16 * step + base   (x0.01)
    u16 nx, ny, nz        normal,    nrm = u16 * 3.05185e-5 - 1.0
    n_uv_sets x (u16,u16) texture coordinates, uv = u16 * 3.05185e-5
    u8/u16 n              number of bone influences
    n x (u8 bone, u16 weight)          weight = u16 * 1e-4  (10000 = 1.0)

Only the **first** UV set is retained (`test edi,edi; jne` at 0x59ABDC), but
all n_uv_sets pairs are consumed.  In the two shipped parts that declare
n_uv_sets == 2 the second pair is byte-identical to the first, so nothing is
lost by keeping set 0.

Influence handling has two code paths:

  * n <= 4 (`0x59ADA4`): every pair is stored; a raw weight of 0 trips a
    debug assert but the value is still written.
  * n >  4 (`0x59AC4C`): every pair is still **consumed** from the stream,
    but only the first four pairs whose raw weight is > 1 (i.e. > 1e-4) are
    kept.  The rest are dropped on the floor.

The n > 4 path also accumulates a running weight total that the parser never
uses - dead bookkeeping left over from a normalisation step that was removed.

Index buffer: `n_vertices < 1024` (`cmp eax, 0x400` at 0x59AFB8) stores one
u32 per triangle holding three 10-bit indices, otherwise three plain u16.

In-memory vertex record (60 bytes, allocated at 0x59A917 as n_verts * 0x3C):

    0x00  f32 x, y, z
    0x0C  f32 nx, ny, nz
    0x18  u32 n_influences
    0x1C  u32 bone[4], f32 weight[4]  interleaved, 8 bytes each

The owning part object keeps an AABB at this+0x54 (min xyz) and this+0x60
(max xyz), seeded to +-5e6 and grown as each vertex is decoded.

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


def part_name(d, off):
    """Name of the part whose `sbus` blob starts at `off`, or None.

    MESH stores each part as

        u32 name_len | GBK name (NUL terminated) | u32 record_count | sbus blob

    so the label sits immediately in front of the blob.  `record_count` is the
    number of sbus records in the part and is 1 in every shipped file - it is
    the sanity check that keeps the backward scan from wandering into the
    previous part's vertex stream.
    """
    if off < 12 or struct.unpack_from("<I", d, off - 4)[0] != 1:
        return None
    for ln in range(1, 80):
        p = off - 8 - ln
        if p < 0:
            return None
        if struct.unpack_from("<I", d, p)[0] != ln:
            continue
        raw = d[p + 4:p + 4 + ln]
        if not raw or raw[-1] != 0:
            continue
        try:
            return raw[:-1].decode("gbk")
        except UnicodeDecodeError:
            continue
    return None


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
    marker = r.u32()
    if marker == 0:                      # variant 2: flag byte present
        if r.u8() != 1:
            return None
        out["variant"] = 2
        out["n_verts"] = r.u32()
    else:                                # variant 1: marker is n_verts
        out["variant"] = 1
        out["n_verts"] = marker
    out["n_faces"] = r.u32()
    out["unk21"] = r.u32()
    out["unk25"] = r.u32()
    out["n_uv"] = r.u32()
    wide = out["variant"] == 2 and r.u8() != 0
    bx, sx = r.f32() * SCALE, r.f32() * SCALE
    by, sy = r.f32() * SCALE, r.f32() * SCALE
    bz, sz = r.f32() * SCALE, r.f32() * SCALE
    out["base"] = [bx, by, bz]
    out["step"] = [sx, sy, sz]
    out["wide_influence"] = wide

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
        pairs = []
        for _ in range(n):
            bone = r.u8()
            w = r.u16()
            pairs.append((bone, w * WEIGHT_K))
        # mirror the engine's storage rule exactly: n <= 4 keeps everything,
        # n > 4 keeps only the first four pairs with a raw weight above 1
        kept = pairs if n <= 4 else [q for q in pairs if q[1] > 1e-4][:4]
        verts.append(dict(pos=[px, py, pz], nrm=[nx, ny, nz], uv=uv,
                          n_influences=n,
                          bones=[b for b, _ in kept],
                          weights=[w for _, w in kept]))
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
