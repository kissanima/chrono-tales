#!/usr/bin/env python3
"""mesh_vert_decode.py - decode .53wf geometry using the client's own algorithm.

The layout was recovered by disassembling the 53wf loader in chronotales.exe
(chunk dispatch 0x5AE5D3, VERT handler 0x5AE932).  The handler is:

    base_x, step_x, base_y, step_y, base_z, step_z  <- 6 x float32
    (each multiplied by the constant at 0x6D0738, which is 0.01)
    for each vertex:
        b = 3 x uint8
        x = b[0] * step_x + base_x
        y = b[1] * step_y + base_y
        z = b[2] * step_z + base_z

so a quantised VERT chunk is 24 bytes of header + 3 bytes per vertex.

VERH is the same tag space 12 bytes later (0x5AE929 does `sub eax,0xc` to turn
"HREV" into "TREV"), and carries unquantised float32 xyz triples instead:
12 bytes per vertex, no header.  In this build 21 of 22 meshes use VERT and
only one (the tier-7 human male weapon) uses VERH.

File layout:

    53WF <u32 version> <u16 nmesh>
    MAT3 | MAT2                     material table
    MESH                            fixed 72-byte mesh header
    nmesh x ( INDX, TEUV, VERT|VERH )
    ENDM                            exactly once, at end of file

`nmesh` in the file header equals the number of INDX chunks and the number of
VERT/VERH chunks, in every sample.  INDX is u16 triangle indices with no
header; TEUV is float32 UV pairs, one per index (per corner), stride 8; and
ENDM (stored byte-reversed as `MDNE`) appears only after the *last* submesh,
which is what makes the final VERT payload look 4 bytes too long.

Usage:
    python mesh_vert_decode.py [--root DIR] [--obj DIR]
"""
import argparse
import math
import pathlib
import random
import statistics
import struct

SCALE = 0.01                     # constant at VA 0x6D0738
TAGS = {"MAT3", "MAT2", "MESH", "INDX", "TEUV", "VERT", "VERH"}
TERMINATORS = {"ENDM", "END."}
ENDM = b"ENDM"


def rev4(d, i):
    try:
        return d[i:i + 4][::-1].decode("ascii")
    except UnicodeDecodeError:
        return None


def chain_of(d):
    """(offset, tag) for every chunk, terminators included."""
    out = []
    for i in range(len(d) - 4):
        t = rev4(d, i)
        if t and (t in TAGS or t in TERMINATORS):
            if not out or i >= out[-1][0] + 4:
                out.append((i, t))
    return out


def gbk(b):
    return b.decode("gbk", "replace").rstrip("\x00")


def parse(path):
    d = path.read_bytes()
    if d[:4] != b"53WF":
        return None
    ver, nmesh = struct.unpack_from("<IH", d, 4)
    ch = chain_of(d)
    name, mats, groups, cur = "", [], [], {}

    for k, (off, tag) in enumerate(ch):
        end = ch[k + 1][0] if k + 1 < len(ch) else len(d)
        body = d[off + 4:end]
        if tag in TERMINATORS:
            # whole-file terminator; never part of the preceding payload
            if body.endswith(ENDM):
                pass
            continue
        if tag == "MESH":
            z = body.find(b"\x00")
            name = gbk(body[:z] if z > 0 else body[:32])
        elif tag in ("MAT3", "MAT2"):
            p = 0
            while p + 2 <= len(body):
                n = struct.unpack_from("<H", body, p)[0]
                if n == 0 or p + 2 + n > len(body):
                    break
                mn = gbk(body[p + 2:p + 2 + n])
                p += 2 + n + 16
                if p + 2 > len(body):
                    mats.append((mn, ""))
                    break
                tn = struct.unpack_from("<H", body, p)[0]
                p += 2
                tex = gbk(body[p:p + tn]) if tn else ""
                p += tn
                mats.append((mn, tex))
        elif tag == "INDX":
            if "idx" in cur:          # a new submesh starts at each INDX
                groups.append(cur)
                cur = {}
            cur["idx"] = struct.unpack_from(f"<{len(body) // 2}H", body, 0)
        elif tag == "TEUV":
            cur["teuv"] = struct.unpack_from(f"<{len(body) // 4}f", body, 0)
        elif tag in ("VERT", "VERH"):
            if body.endswith(ENDM):   # last submesh: strip the file terminator
                body = body[:-4]
            cur["vert"] = body
            cur["verh"] = tag == "VERH"
    if cur:
        groups.append(cur)
    return {"version": ver, "meshes": nmesh, "name": name,
            "materials": mats, "groups": groups, "size": len(d)}


def decode_vert(body, verh=False):
    """Return (positions, codec_name).  VERH is float32, VERT quantised."""
    if not body:
        return [], "-"
    if verh:
        n = len(body) // 12
        vals = struct.unpack_from(f"<{n * 3}f", body, 0)
        return [(vals[i] * SCALE, vals[i + 1] * SCALE, vals[i + 2] * SCALE)
                for i in range(0, len(vals), 3)], "VERH"
    if len(body) < 24:
        return [], "-"
    hdr = struct.unpack_from("<6f", body, 0)
    bx, sx, by, sy, bz, sz = [v * SCALE for v in hdr]
    data = body[24:]
    n = len(data) // 3
    pts = []
    for i in range(n):
        r, g, b = data[i * 3], data[i * 3 + 1], data[i * 3 + 2]
        pts.append((r * sx + bx, g * sy + by, b * sz + bz))
    return pts, "VERT"


def edge_stats(idx, pts, samples=600):
    """Median triangle edge, and the same with positions shuffled (control)."""
    def med(ps):
        out = []
        for t in range(min(len(idx) // 3, samples)):
            a, b, c = idx[t * 3:t * 3 + 3]
            if max(a, b, c) < len(ps):
                out += [math.dist(ps[a], ps[b]), math.dist(ps[b], ps[c]),
                        math.dist(ps[c], ps[a])]
        return statistics.median(out) if out else None

    real = med(pts)
    sh = pts[:]
    random.shuffle(sh)
    return real, med(sh)


def bbox(pts):
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    zs = [q[2] for q in pts]
    return (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path,
                    default=pathlib.Path("extracted/netpak"))
    ap.add_argument("--obj", type=pathlib.Path, default=None,
                    help="also write OBJ files here")
    a = ap.parse_args()
    random.seed(0)
    if a.obj:
        a.obj.mkdir(parents=True, exist_ok=True)

    tot_g = tot_ok = 0
    ratios, bb_all, unused = [], [], []
    print(f'{"mesh":10} {"name":12} {"sub":>3} {"codec":5} {"verts":>6} {"used":>5} '
          f'{"tris":>5} {"bbox (m)":>20} {"e/perm":>7}')

    for path in sorted(a.root.rglob("*.53wf")):
        info = parse(path)
        if not info:
            continue
        lines = None
        if a.obj:
            lines = [f'# {path.name}  mesh "{info["name"]}"',
                     '# VERT = base + byte*step, all *0.01 ; VERH = float32 *0.01',
                     '# units: metres   up axis: +Z   right-handed']
        vbase = tbase = 1
        wrote = False

        for gi, g in enumerate(info["groups"]):
            tot_g += 1
            if "idx" not in g or "vert" not in g:
                continue
            pts, codec = decode_vert(g["vert"], g.get("verh", False))
            idx = g["idx"]
            if not pts or not idx:
                continue
            need = max(idx) + 1
            used = pts[:need]
            if not used:
                continue
            r, p = edge_stats(idx, pts)
            if r and p:
                ratios.append(r / p)
            tot_ok += 1
            unused.append((len(pts) - need) / len(pts))
            bb = bbox(used)
            bb_all.append(max(bb))
            rr = f'{r / p:.2f}' if r and p else '-'
            print(f'{path.stem:10} {info["name"][:12]:12} {gi:3} {codec:5} '
                  f'{len(pts):6} {need:5} {len(idx) // 3:5} '
                  f'{bb[0]:6.2f}x{bb[1]:5.2f}x{bb[2]:5.2f} {rr:>7}')

            if lines is not None:
                mat = info["materials"][gi][0] if gi < len(info["materials"]) else ""
                lines.append(f'g {info["name"] or path.stem}_{gi}_{mat}')
                for q in pts:
                    lines.append('v %.5f %.5f %.5f' % q)
                uv = g.get("teuv", ())
                nuv = len(uv) // 2
                for u in range(nuv):
                    lines.append('vt %.6f %.6f' % (uv[u * 2], uv[u * 2 + 1]))
                for t in range(len(idx) // 3):
                    x, y, z = idx[t * 3:t * 3 + 3]
                    if max(x, y, z) >= len(pts):
                        continue
                    if (t + 1) * 3 <= nuv:
                        lines.append('f %d/%d %d/%d %d/%d' % (
                            x + vbase, t * 3 + 1 + tbase,
                            y + vbase, t * 3 + 2 + tbase,
                            z + vbase, t * 3 + 3 + tbase))
                    else:
                        lines.append('f %d %d %d' % (x + vbase, y + vbase, z + vbase))
                    wrote = True
                vbase += len(pts)
                tbase += nuv

        if lines is not None and wrote:
            (a.obj / (path.stem + ".obj")).write_text(
                "\n".join(lines) + "\n", encoding="utf-8")

    print(f'\nsubmeshes: {tot_g}, decoded: {tot_ok}')
    if ratios:
        print(f'edge/permuted ratio: median {statistics.median(ratios):.3f}, '
              f'min {min(ratios):.3f}, max {max(ratios):.3f}   (<1 = real geometry)')
    if bb_all:
        print(f'largest bbox extent per submesh: median {statistics.median(bb_all):.2f} m, '
              f'max {max(bb_all):.2f} m')
    if unused:
        print(f'unreferenced vertices: median {statistics.median(unused) * 100:.1f}% of buffer')
    if a.obj:
        print(f'OBJ written to {a.obj}')


if __name__ == "__main__":
    main()
