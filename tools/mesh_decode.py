#!/usr/bin/env python3
"""Decode GBox2 model containers: `.53wf` meshes and `.cmz` skeletons.

.53wf
-----
    "53WF"                  magic (literal, not reversed)
    u32   version           1
    u16   materialCount
    u16   meshCount
    u32   0
    then a chain of chunks, each a byte-reversed FourCC:

        MAT3   material list: per material
                   u16 nameLen, GBK name,
                   u16 1, u16 1, u32 -1, u32 0, u16 0, u16 255,
                   u16 texLen, GBK texture filename, 8 zero bytes
        MESH   mesh header: name (NUL-terminated) then counts
        INDX   index buffer
        TEUV   raw float32 UV pairs, no header
        VERT   raw float32 XYZ triples, no header
        END    terminator

    The repeating group is (INDX, TEUV, VERT) -- one submesh per material.

.cmz
----
    "CMZ\\0"  magic
    u32   0x66               version
    "leks"                   byte-reversed "skel"
    u32   count
    u32   boneCount
    then bone records: NUL-terminated bone name followed by packed transform data
    (float32 translations/quaternions; Bip01, Bip01 Pelvis, Bip01 Spine, ...)
"""
import glob
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "extracted", "netpak", "unknown")
OUT = os.path.join(ROOT, "extracted", "models")

CHUNK_TAGS = {"MAT3", "MESH", "INDX", "TEUV", "VERT", "END"}


def rev4(d, i):
    """Byte-reversed FourCC at i, or None."""
    if i + 4 > len(d):
        return None
    r = d[i:i + 4][::-1]
    if all(65 <= b <= 90 or 48 <= b <= 57 for b in r):
        s = r.decode("ascii")
        return s if s in CHUNK_TAGS else None
    return None


def gbk(b):
    return b.decode("gbk", "replace")


# --------------------------------------------------------------------------- #
# 53wf
# --------------------------------------------------------------------------- #

def parse_mat3(d, off):
    """Walk material records until the next chunk tag.  Returns (materials, end)."""
    mats = []
    i = off + 4
    while i + 2 <= len(d):
        if rev4(d, i):
            break
        n = struct.unpack_from("<H", d, i)[0]
        if not (0 < n <= 128) or i + 2 + n > len(d):
            break
        name = gbk(d[i + 2:i + 2 + n])
        j = i + 2 + n
        # u16, u16, u32, u32, u16, u16
        if j + 16 > len(d):
            break
        a, b = struct.unpack_from("<2H", d, j)
        c, e = struct.unpack_from("<2I", d, j + 4)
        f, g = struct.unpack_from("<2H", d, j + 12)
        j += 16
        tex = ""
        if j + 2 <= len(d):
            tl = struct.unpack_from("<H", d, j)[0]
            if 0 < tl <= 260 and j + 2 + tl <= len(d):
                tex = gbk(d[j + 2:j + 2 + tl])
                j += 2 + tl
        j += 8                              # trailing zeros
        mats.append({"name": name, "texture": tex, "a": a, "b": b,
                     "c": c, "d": e, "e": f, "f": g, "offset": i})
        i = j
    return mats, i


def parse_53wf(d):
    out = {"kind": "53wf", "size": len(d)}
    if d[:4] != b"53WF":
        raise ValueError("not a 53WF file")
    ver = struct.unpack_from("<I", d, 4)[0]
    mat_count, mesh_count = struct.unpack_from("<2H", d, 8)
    out.update(version=ver, material_count=mat_count, mesh_count=mesh_count)

    # locate the chunk chain
    tags = []
    for i in range(len(d) - 4):
        t = rev4(d, i)
        if t:
            tags.append((i, t))
    # keep only the chain: strictly increasing offsets, no duplicates inside payloads
    chain, last = [], -1
    for off, t in tags:
        if off > last:
            chain.append((off, t))
            last = off
    out["chunks"] = [{"offset": o, "tag": t} for o, t in chain]

    mats = []
    for k, (off, t) in enumerate(chain):
        end = chain[k + 1][0] if k + 1 < len(chain) else len(d)
        if t == "MAT3":
            mats, _ = parse_mat3(d, off)
    out["materials"] = mats

    # mesh names: NUL-terminated ASCII/GBK right after each MESH tag
    names = []
    for k, (off, t) in enumerate(chain):
        if t != "MESH":
            continue
        raw = d[off + 4:off + 4 + 64]
        z = raw.find(b"\x00")
        names.append(gbk(raw[:z]) if z > 0 else "")
    out["mesh_names"] = names

    # per-chunk payload stats
    chunks = []
    for k, (off, t) in enumerate(chain):
        end = chain[k + 1][0] if k + 1 < len(chain) else len(d)
        payload = end - off - 4
        info = {"tag": t, "offset": off, "payload": payload}
        body = d[off + 4:end]
        if t in ("VERT", "TEUV") and payload >= 12:
            cands = []
            for stride, label, comps in ((8, "uv", 2), (12, "xyz", 3), (16, "xyzw", 4)):
                if payload % stride:
                    continue
                cnt = payload // stride
                vals = struct.unpack_from(f"<{payload // 4}f", d, off + 4)
                finite = sum(1 for v in vals if -1e6 < v < 1e6)
                if finite / len(vals) < 0.95:
                    continue
                c = {"stride": stride, "count": cnt, "layout": label}
                if comps:
                    c["bbox"] = [
                        v for i in range(comps)
                        for v in (round(min(vals[i::comps]), 3),
                                  round(max(vals[i::comps]), 3))]
                    c["unit_range"] = all(
                        -0.01 <= min(vals[i::comps])
                        and max(vals[i::comps]) <= 1.01
                        for i in range(comps))
                cands.append(c)
            if cands:
                info["candidates"] = cands
                info.update(cands[0])
        chunks.append(info)
    out["chunk_detail"] = chunks
    return out


# --------------------------------------------------------------------------- #
# cmz
# --------------------------------------------------------------------------- #

def parse_cmz(d):
    out = {"kind": "cmz", "size": len(d)}
    if d[:4] != b"CMZ\x00":
        raise ValueError("not a CMZ file")
    ver = struct.unpack_from("<I", d, 4)[0]
    tag = d[8:12][::-1].decode("latin1")
    cnt = struct.unpack_from("<I", d, 12)[0]
    bones_decl = struct.unpack_from("<I", d, 16)[0]
    out.update(version=ver, chunk=tag, count=cnt, bone_count_declared=bones_decl)

    # bone names are NUL-terminated; walk them and record offsets
    bones = []
    for m in re.finditer(rb"Bip0\d[A-Za-z0-9 _]*", d):
        start = m.start()
        end = d.find(b"\x00", start)
        if end < 0:
            continue
        name = d[start:end].decode("latin1")
        bones.append({"name": name, "offset": start})
    # de-duplicate, keep order
    seen, ordered = set(), []
    for b in bones:
        if b["name"] not in seen:
            seen.add(b["name"])
            ordered.append(b)
    for k, b in enumerate(ordered):
        b["record_size"] = (ordered[k + 1]["offset"] - b["offset"]
                            if k + 1 < len(ordered) else None)
    out["bones"] = ordered
    out["bone_count"] = len(ordered)

    # other chunk tags present
    others = []
    for i in range(12, len(d) - 4):
        r = d[i:i + 4][::-1]
        if all(65 <= b <= 90 or 48 <= b <= 57 for b in r):
            t = r.decode("ascii")
            if t in ("MESH", "NBZ1", "POPO", "SISI", "L51R", "ENXV", "EMPV", "MAP1", "LJIL"):
                others.append({"offset": i, "tag": t})
    out["other_chunks"] = others[:40]
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    summary = []
    for pattern, fn in (("*.53wf", parse_53wf), ("*.cmz", parse_cmz)):
        for p in sorted(glob.glob(os.path.join(SRC, pattern))):
            d = open(p, "rb").read()
            name = os.path.splitext(os.path.basename(p))[0]
            try:
                info = fn(d)
            except Exception as e:                       # noqa: BLE001
                print(f"{name}: FAIL {e}")
                continue
            info["source"] = os.path.basename(p)
            with open(os.path.join(OUT, name + ".json"), "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=1)
            summary.append(info)
            if info["kind"] == "53wf":
                nmat = len(info["materials"])
                nch = len(info["chunks"])
                v = [c for c in info["chunk_detail"] if c.get("layout")]
                print(f"{name:10s} 53wf {len(d):8,d}B  {nmat} materials  "
                      f"{nch} chunks  resolved={len(v)}")
            else:
                print(f"{name:10s} cmz  {len(d):8,d}B  {info['bone_count']} bones  "
                      f"{info['chunk']}")

    with open(os.path.join(OUT, "_index.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    n5 = sum(1 for s in summary if s["kind"] == "53wf")
    nc = sum(1 for s in summary if s["kind"] == "cmz")
    print(f"\n{n5} meshes + {nc} skeletons -> {OUT}")


if __name__ == "__main__":
    main()
