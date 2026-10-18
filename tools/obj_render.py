#!/usr/bin/env python3
"""obj_render.py - dependency-free shaded renderer for the exported .53wf meshes.

Reads the OBJ files written by mesh_vert_decode.py and produces PNGs, so the
decoded geometry can be eyeballed without installing a 3D stack.  Pure stdlib:
a z-buffered barycentric rasteriser plus a minimal PNG encoder.

Shading is two-sided Lambert with a headlight plus a rim term, so back faces of
open meshes (foliage cards, terrain strips) stay readable.

Textures are optional.  Pass --texmap with a JSON object mapping a material
name (e.g. "新手村藤") to an image path, and each submesh is sampled through
its own UVs.  Image decoding needs Pillow; without it the renderer falls back
to flat shading.

Usage:
    python obj_render.py [--obj DIR] [--png DIR] [--sheet FILE] [--size N]
                         [--texmap FILE]
"""
import argparse
import json
import math
import pathlib
import struct
import zlib

BG = (18, 20, 26)
FILL = (214, 168, 96)          # warm neutral, reads well on dark
RIM = (120, 160, 220)

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def load_texture(path, alpha_cut=128):
    """-> (w, h, rgb bytes, alpha bytes or None)."""
    if not HAVE_PIL or path is None:
        return None
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:
        return None
    w, h = im.size
    raw = im.tobytes()
    rgb = bytearray(w * h * 3)
    al = bytearray(w * h)
    for i in range(w * h):
        rgb[i * 3] = raw[i * 4]
        rgb[i * 3 + 1] = raw[i * 4 + 1]
        rgb[i * 3 + 2] = raw[i * 4 + 2]
        al[i] = raw[i * 4 + 3]
    if min(al) >= alpha_cut:
        al = None                      # fully opaque, skip the test
    return (w, h, bytes(rgb), bytes(al) if al else None)


def write_png(path, w, h, pix):
    """pix: bytearray of w*h*3."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += pix[y * w * 3:(y + 1) * w * 3]

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def load_obj(path):
    """-> (verts, uvs, groups) where each group is (name, tris) and each tri is
    ((vi,ti), (vi,ti), (vi,ti)); ti is None when the face had no vt."""
    verts, uvs, groups = [], [], []
    gname, tris = "", []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("v "):
            _, x, y, z = line.split()[:4]
            verts.append((float(x), float(y), float(z)))
        elif line.startswith("vt "):
            _, u, v = line.split()[:3]
            uvs.append((float(u), float(v)))
        elif line.startswith("g "):
            if tris:
                groups.append((gname, tris))
            gname, tris = line[2:].strip(), []
        elif line.startswith("f "):
            ids = []
            for tok in line.split()[1:]:
                bits = tok.split("/")
                vi = int(bits[0]) - 1
                ti = int(bits[1]) - 1 if len(bits) > 1 and bits[1] else None
                ids.append((vi, ti))
            for k in range(1, len(ids) - 1):        # fan-triangulate
                tris.append((ids[0], ids[k], ids[k + 1]))
    if tris:
        groups.append((gname, tris))
    return verts, uvs, groups


def rot(yaw, pitch, up="z"):
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    # view space: x right, y up, z away from camera
    if up == "z":
        return ((cy, sy, 0.0), (-sp * sy, sp * cy, cp), (-cp * sy, cp * cy, -sp))
    return ((cy, 0.0, sy), (-sp * sy, cp, sp * cy), (-cp * sy, -sp, cp * cy))


def render(verts, tris, size, yaw, pitch, zoom=0.86, bg=BG, up="z",
           uvs=None, checker=0, texes=None, tri_tex=None):
    w = h = size
    pix = bytearray(bytes(bg) * (w * h))
    zbuf = [1e30] * (w * h)

    if not verts or not tris:
        return pix

    # frame on the vertices triangles actually use: VERT buffers are
    # over-allocated, so stray unreferenced points would skew the bbox.
    used = set()
    for t in tris:
        used.add(t[0][0])
        used.add(t[1][0])
        used.add(t[2][0])
    used = [i for i in used if 0 <= i < len(verts)] or list(range(len(verts)))

    xs = [verts[i][0] for i in used]
    ys = [verts[i][1] for i in used]
    zs = [verts[i][2] for i in used]
    cx, cy, cz = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, (max(zs) + min(zs)) / 2
    ext = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) or 1.0

    m = rot(yaw, pitch, up)
    scale = (size * 0.5 * zoom) / ext
    proj = []
    for (x, y, z) in verts:
        x, y, z = x - cx, y - cy, z - cz
        rx = m[0][0] * x + m[0][1] * y + m[0][2] * z
        ry = m[1][0] * x + m[1][1] * y + m[1][2] * z
        rz = m[2][0] * x + m[2][1] * y + m[2][2] * z
        proj.append((w * 0.5 + rx * scale, h * 0.5 - ry * scale, rz * scale))

    # three-light studio: warm key from upper-right, cool fill from the left,
    # and a facing-based rim.  All two-sided (|dot|) so open meshes stay readable.
    def _norm(v):
        l = math.sqrt(sum(c * c for c in v)) or 1.0
        return tuple(c / l for c in v)
    key = _norm((0.38, 0.42, 0.82))
    fill = _norm((-0.55, -0.20, 0.55))
    light = key

    for ti, tri in enumerate(tris):
        a, b, c = tri[0][0], tri[1][0], tri[2][0]
        if max(a, b, c) >= len(proj):
            continue
        pa, pb, pc = proj[a], proj[b], proj[c]
        # screen-space winding (y flipped already) -> orientation test
        area = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pc[0] - pa[0]) * (pb[1] - pa[1])
        if abs(area) < 1e-9:
            continue
        # world normal for shading
        ux, uy, uz = verts[b][0] - verts[a][0], verts[b][1] - verts[a][1], verts[b][2] - verts[a][2]
        vx, vy, vz = verts[c][0] - verts[a][0], verts[c][1] - verts[a][1], verts[c][2] - verts[a][2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        nl = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / nl, ny / nl, nz / nl
        d = nx * light[0] + ny * light[1] + nz * light[2]
        d2 = nx * fill[0] + ny * fill[1] + nz * fill[2]
        # transform normal into view space for a facing test
        vnz = m[2][0] * nx + m[2][1] * ny + m[2][2] * nz
        facing = max(0.0, vnz)
        lam = max(0.0, abs(d))
        lam2 = max(0.0, abs(d2))
        shade = 0.14 + 0.56 * lam + 0.24 * lam2 + 0.16 * (1.0 - facing) ** 2
        shade = min(1.0, shade)
        col = (int(FILL[0] * shade), int(FILL[1] * shade), int(FILL[2] * shade))

        ta, tb, tc = tri[0][1], tri[1][1], tri[2][1]
        tex = None
        if texes and tri_tex:
            k = tri_tex[ti]
            if k is not None:
                tex = texes[k]
        have_uv = bool(uvs and None not in (ta, tb, tc)
                       and max(ta, tb, tc) < len(uvs))
        use_tex = bool(tex and have_uv)
        use_check = bool(checker and have_uv)
        use_uv = use_tex or use_check
        if use_uv:
            uva, uvb, uvc = uvs[ta], uvs[tb], uvs[tc]

        x0 = max(0, int(math.floor(min(pa[0], pb[0], pc[0]))))
        x1 = min(w - 1, int(math.ceil(max(pa[0], pb[0], pc[0]))))
        y0 = max(0, int(math.floor(min(pa[1], pb[1], pc[1]))))
        y1 = min(h - 1, int(math.ceil(max(pa[1], pb[1], pc[1]))))
        if x0 > x1 or y0 > y1:
            continue
        inv = 1.0 / area
        for py in range(y0, y1 + 1):
            fy = py + 0.5
            row = py * w
            for px in range(x0, x1 + 1):
                fx = px + 0.5
                w0 = ((pb[0] - fx) * (pc[1] - fy) - (pc[0] - fx) * (pb[1] - fy)) * inv
                if w0 < 0:
                    continue
                w1 = ((pc[0] - fx) * (pa[1] - fy) - (pa[0] - fx) * (pc[1] - fy)) * inv
                if w1 < 0:
                    continue
                w2 = 1.0 - w0 - w1
                if w2 < 0:
                    continue
                z = w0 * pa[2] + w1 * pb[2] + w2 * pc[2]
                i = row + px
                if z < zbuf[i]:
                    cr, cg, cb = col
                    if use_uv:
                        u = w0 * uva[0] + w1 * uvb[0] + w2 * uvc[0]
                        v = w0 * uva[1] + w1 * uvb[1] + w2 * uvc[1]
                        if use_tex:
                            tw, th, tdata, tal = tex
                            u -= math.floor(u)
                            v -= math.floor(v)
                            tx = int(u * tw)
                            if tx >= tw:
                                tx = tw - 1
                            ty = int((1.0 - v) * th)
                            if ty >= th:
                                ty = th - 1
                            elif ty < 0:
                                ty = 0
                            tix = ty * tw + tx
                            if tal is not None and tal[tix] < 128:
                                continue            # alpha cutout: leave bg
                            s = 0.58 + 0.50 * lam
                            cr = min(255, int(tdata[tix * 3] * s))
                            cg = min(255, int(tdata[tix * 3 + 1] * s))
                            cb = min(255, int(tdata[tix * 3 + 2] * s))
                        elif (int(math.floor(u * checker)) +
                                int(math.floor(v * checker))) & 1:
                            cr = min(255, int(cr * 0.45))
                            cg = min(255, int(cg * 0.45))
                            cb = min(255, int(cb * 0.55))
                    zbuf[i] = z
                    pix[i * 3] = cr
                    pix[i * 3 + 1] = cg
                    pix[i * 3 + 2] = cb
    return pix


def tile(dst, dw, src, sw, ox, oy):
    for y in range(sw):
        d = ((oy + y) * dw + ox) * 3
        s = y * sw * 3
        dst[d:d + sw * 3] = src[s:s + sw * 3]


def texmap_paths(tm):
    """Normalise texmap.json into material name -> image path and mesh hash ->."""
    tm_by_name = tm.get("by_name", {}) if isinstance(tm, dict) else {}
    tm_by_mesh = tm.get("by_mesh", {}) if isinstance(tm, dict) else {}
    root = pathlib.Path(__file__).resolve().parent.parent

    def _to_path(v):
        if isinstance(v, str):
            return root / v
        if isinstance(v, dict):
            ent = v.get("tex_entry")
            if ent:
                return root / "extracted" / ent[0] / ent[1]
        return None

    out = {name: _to_path(v) for name, v in tm_by_name.items()}
    out = {k: v for k, v in out.items() if v}
    mesh = {int(k, 16): _to_path(v) for k, v in tm_by_mesh.items()}
    mesh = {k: v for k, v in mesh.items() if v}
    return out, mesh


def bind_textures(stem, groups, paths, mesh_paths, cache):
    """-> (texes, tri_tex) for one mesh.

    Submesh *i* uses material *i*: the OBJ group names only spell the material
    out for the first group (`蘑菇04_0_蘑菇001`, then `蘑菇04_1_`), but the
    material table and the submesh list are in the same order, which holds for
    every multi-material mesh in the set.  `paths` is keyed by the material's
    texture filename.  If the mesh has no material table at all, fall back to
    `mesh_paths` keyed by the mesh archive hash.
    """
    mdir = pathlib.Path(__file__).resolve().parent.parent / "extracted" / "models"
    jf = mdir / (stem + ".json")
    texfiles = []
    if jf.exists():
        d = json.load(open(jf, encoding="utf-8"))
        texfiles = [m.get("texture") for m in (d.get("materials") or [])]

    if not texfiles:
        try:
            mh = int(stem, 16)
        except ValueError:
            mh = None
        mp = mesh_paths.get(mh)
        if mp:
            tex = cache.get("__mesh__") or load_texture(mp)
            cache["__mesh__"] = tex
            tri_tex = [0 if tex is not None else None] * sum(len(g) for _, g in groups)
            return ([tex] if tex else []), tri_tex
        return [], [None] * sum(len(g) for _, g in groups)

    texes, idx, tri_tex = [], {}, []
    for gi, (gname, gtris) in enumerate(groups):
        texfile = texfiles[gi] if gi < len(texfiles) else None
        k = None
        if texfile and texfile in paths:
            if texfile not in idx:
                idx[texfile] = len(texes)
                texes.append(cache.get(texfile) or load_texture(paths[texfile]))
                cache[texfile] = texes[idx[texfile]]
            if texes[idx[texfile]] is not None:
                k = idx[texfile]
        tri_tex.extend([k] * len(gtris))
    return texes, tri_tex


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--obj", type=pathlib.Path, default=pathlib.Path("extracted/models/obj"))
    ap.add_argument("--png", type=pathlib.Path, default=pathlib.Path("extracted/models/png"))
    ap.add_argument("--sheet", type=pathlib.Path,
                    default=pathlib.Path("extracted/models/contact_sheet.png"))
    ap.add_argument("--size", type=int, default=480)
    ap.add_argument("--tile", type=int, default=200)
    ap.add_argument("--cols", type=int, default=7)
    ap.add_argument("--up", choices=("y", "z"), default="z",
                    help="world up axis (assets are Z-up)")
    ap.add_argument("--checker", type=int, default=0,
                    help="if >0, draw a UV checker with this many repeats")
    ap.add_argument("--texmap", type=pathlib.Path,
                    default=pathlib.Path("extracted/models/texmap.json"),
                    help="material name -> texture image (see tex_link.py)")
    ap.add_argument("--yaw", type=float, default=38.0)
    ap.add_argument("--pitch", type=float, default=18.0)
    a = ap.parse_args()
    a.png.mkdir(parents=True, exist_ok=True)

    paths, mesh_paths = ({}, {})
    if a.texmap and a.texmap.exists():
        paths, mesh_paths = texmap_paths(json.load(open(a.texmap, encoding="utf-8")))
    if paths and not HAVE_PIL:
        print("note: Pillow not installed, textures disabled")
        paths, mesh_paths = {}, {}

    objs = sorted(a.obj.glob("*.obj"))
    if not objs:
        print("no OBJ files; run mesh_vert_decode.py --obj first")
        return

    cache = {}
    thumbs = []
    ntex = 0
    for path in objs:
        verts, uvs, groups = load_obj(path)
        tris = [t for _, g in groups for t in g]
        if paths:
            texes, tri_tex = bind_textures(path.stem, groups, paths, mesh_paths, cache)
        else:
            texes, tri_tex = [], None
        got = sum(1 for k in (tri_tex or []) if k is not None)
        if got:
            ntex += 1
        pix = render(verts, tris, a.size, yaw=math.radians(a.yaw),
                     pitch=math.radians(a.pitch), up=a.up, uvs=uvs,
                     checker=a.checker, texes=texes, tri_tex=tri_tex)
        write_png(a.png / (path.stem + ".png"), a.size, a.size, pix)
        tag = f' tex={got}/{len(tris)}' if paths else ''
        print(f'{path.stem}  verts={len(verts):5} uvs={len(uvs):5} '
              f'tris={len(tris):5} groups={len(groups)}{tag}')
        thumbs.append(render(verts, tris, a.tile, yaw=math.radians(a.yaw),
                             pitch=math.radians(a.pitch), zoom=0.80, up=a.up,
                             uvs=uvs, checker=a.checker, texes=texes,
                             tri_tex=tri_tex))

    cols = a.cols
    rows = (len(thumbs) + cols - 1) // cols
    dw, dh = cols * a.tile, rows * a.tile
    sheet = bytearray(bytes(BG) * (dw * dh))
    for i, t in enumerate(thumbs):
        tile(sheet, dw, t, a.tile, (i % cols) * a.tile, (i // cols) * a.tile)
    write_png(a.sheet, dw, dh, sheet)
    if paths:
        print(f'textured meshes: {ntex}/{len(objs)}  (materials resolved: {len(cache)})')
    print(f'\n{a.sheet}  ({dw}x{dh}, {len(thumbs)} tiles)')


if __name__ == "__main__":
    main()
