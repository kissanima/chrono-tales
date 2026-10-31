#!/usr/bin/env python3
"""render_scenes.py - top-down visualisation of the decoded intro .zmap scenes.

For each populated scene: terrain heightmap (greyscale, byte*4-100 m per
GPAK_FORMAT.md 4.7) as background, scenery instances as dots (colour per
type, radius ~ scale), placed MAP_MODELDATA objects as labelled squares.
"""
import json
import math
import pathlib
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import varfile  # noqa: E402

OUT = pathlib.Path("extracted/maps")
HEIGHT_N = 129            # authored height grid: 129 x 129 cells at 2 m
PALETTE = [(231, 76, 60), (46, 204, 113), (52, 152, 219), (241, 196, 15),
           (155, 89, 182), (26, 188, 156), (230, 126, 34), (149, 165, 166)]


def height_grid(blob):
    """-> (rows, cols, [h16], minz, maxz) per 4.7.

    MAP_HEIGHTDATA is 12-byte header + two 129x129 byte planes + u32.  The two
    planes are the high and low byte of ONE 16-bit height per vertex, not two
    fields: chronotales.exe's .zmap loader (0x4ee4f9) demands a record length of
    exactly 0x8212 = 33,298, reads minz/maxz from header bytes 4 and 8, points a
    second cursor at first+129*129 and does `mov dh,[edi] / mov dl,[ecx]`, then
    `z = minz + h16 * (maxz-minz) * 1.5260186e-05` (= 1/65530).  So the range is
    the header's -100..900 and the step is 15 mm.
    """
    base = struct.unpack_from("<f", blob, 4)[0]
    top = struct.unpack_from("<f", blob, 8)[0]
    body = blob[12:-4]
    cols = HEIGHT_N
    rows = min(len(body) // cols // 2, HEIGHT_N)
    n = rows * cols
    hi = body[:n]
    lo = body[n:2 * n]
    h16 = [(hi[i] << 8) | lo[i] for i in range(n)]
    return rows, cols, h16, base, top


def smooth(vals, rows, cols, passes=0):
    """Optional tent-blur of the height field (16-bit units).

    The 16-bit decode steps by 15 mm, so there is no quantisation staircase to
    hide any more and this defaults to a no-op.  It used to run 2 passes to
    paper over the 3.9 m stairs of the old 8-bit read.
    """
    if passes <= 0:
        return list(vals)
    import numpy as np
    a = np.asarray(vals, dtype=np.float32).reshape(rows, cols)
    for _ in range(passes):
        q = np.pad(a, 1, mode="edge")
        a = (q[0:-2, 0:-2] + 2 * q[0:-2, 1:-1] + q[0:-2, 2:]
             + 2 * q[1:-1, 0:-2] + 4 * q[1:-1, 1:-1] + 2 * q[1:-1, 2:]
             + q[2:, 0:-2] + 2 * q[2:, 1:-1] + q[2:, 2:]) / 16.0
    return a.ravel().tolist()


def scene_image(scene, name):
    terr = scene["terrain_raw"]
    rows, cols, h16, base, top = height_grid(terr)
    # world extent: grid step 2 m (129 cols = 256 m), x = col, y = row
    W = 760
    sc = W / cols
    H = int(rows * sc)
    small = Image.new("RGB", (cols, rows), (18, 18, 24))
    sp = small.load()
    grid = smooth(h16, rows, cols)
    lo, hi = min(grid), max(grid)
    for r in range(rows):
        for c in range(cols):
            h = grid[r * cols + c]
            g = int(40 + 180 * (h - lo) / max(1, hi - lo))
            sp[c, r] = (int(g * 0.55), int(g * 0.62), int(g * 0.55))
    im = small.resize((W, H), Image.NEAREST)
    dr = ImageDraw.Draw(im)
    font = load_font(13)

    def w2p(x, y):
        return x / 2.0 * sc, y / 2.0 * sc

    # scenery instances
    for si, s in enumerate(scene["scenery"]):
        inst = s.get("instances")
        if not inst:
            continue
        col = PALETTE[si % len(PALETTE)]
        for p in inst["instances"]:
            cx, cy = w2p(p["x"], p["y"])
            rad = max(2, min(9, 3 * p["scale"]))
            # direction tick
            a = p["yaw"]
            dr.line([cx, cy, cx + rad * 1.8 * math.sin(a),
                     cy - rad * 1.8 * math.cos(a)], fill=col)
            dr.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
                       outline=col, width=1)

    # placed objects (v5 model placements)
    dr2 = dr
    for o in scene["objects"]:
        if not o.get("x") and not o.get("y"):
            continue
        cx, cy = w2p(o["x"], o["y"])
        dr2.rectangle([cx - 4, cy - 4, cx + 4, cy + 4], outline=(255, 255, 255))
        dr2.text((cx + 6, cy - 6), o["name"][:16], fill=(255, 255, 255), font=font)

    # legend, on a panel so it stays readable over the terrain
    y = 6
    dr2.rectangle([W - 268, 0, W, 14 + 16 * len(scene["scenery"])],
                  fill=(12, 12, 16))
    for si, s in enumerate(scene["scenery"]):
        col = PALETTE[si % len(PALETTE)]
        n = len(s["instances"]["instances"]) if s.get("instances") else 0
        dr2.rectangle([W - 262, y + 4, W - 250, y + 12], fill=col)
        dr2.text((W - 244, y), f"{s['name'].split('/')[-1]}  x{n}",
                 fill=(220, 220, 220), font=font)
        y += 16
    dr2.rectangle([0, H - 22, 430, H], fill=(12, 12, 16))
    zlo = base + lo * (top - base) / 65530.0
    zhi = base + hi * (top - base) / 65530.0
    dr2.text((6, H - 19),
             f"{name}   terrain 129x129 @2 m (256 m)   "
             f"z {zlo:.0f}..{zhi:.0f} m   deco x{sum(len(s.get('instances', {}).get('instances', [])) if s.get('instances') else 0 for s in scene['scenery'])}",
             fill=(170, 170, 170), font=font)
    return im


def world_layout(width=1152):
    """Top-down map of the whole stitched tile grid, with the grid drawn on.

    One `.zmap` is a 256 m tile (MAKEMAPID(ix,iy) = ix + iy*1000, def.gbc:110),
    so this is the only view that shows the world as the engine assembles it.
    The relief is a hillshade of the stitched height field multiplied into the
    per-tile ground atlas, which is the same data the 3D renderer uses.
    """
    import numpy as np
    import render_scene3d as R

    w = R.World("intro")
    H = int(round(width * w.world_h / w.world_w))
    ppm = width / w.world_w

    atlas = R.World.bake_ground(w)
    base = Image.open(atlas).convert("RGB").resize((width, H), Image.LANCZOS)
    a = np.asarray(base, dtype=np.float32)

    # hillshade from the stitched 2 m grid, bilinear to pixel space.  The
    # gradient is normalised by its 90th percentile first: raw metres-per-cell
    # spans two orders of magnitude here (the plain is ~0.02, the hillside ~3),
    # so a fixed gain either flattens the plain or crushes the hills to black.
    g = np.asarray(w.hf, dtype=np.float32)
    gx = np.gradient(g, axis=1)
    gy = np.gradient(g, axis=0)
    mag = max(1e-6, float(np.percentile(np.hypot(gx, gy), 90)))
    shade = np.clip(0.94 + 0.38 * (gx / mag * 0.5 - gy / mag * 0.85), 0.55, 1.32)
    si = Image.fromarray((np.clip(shade - 0.5, 0, 1) * 255).astype(np.uint8))
    shade = 0.5 + np.asarray(si.resize((width, H), Image.BILINEAR),
                             dtype=np.float32) / 255.0
    a = np.clip(a * shade[:, :, None], 0, 255)
    im = Image.fromarray(a.astype(np.uint8))
    dr = ImageDraw.Draw(im)
    font = load_font(15)
    small = load_font(12)

    def w2p(x, y):
        return x * ppm, y * ppm

    # scenery
    for si_, (sc, ox, oy) in enumerate(w.tiles):
        for e in sc.widget:
            if not (isinstance(e[6], (bytes, bytearray))):
                continue
            try:
                inst = R.zmap_instances(bytes(e[6]))
            except ValueError:
                continue
            col = PALETTE[(si_ * 3 + len(e[1])) % len(PALETTE)]
            for p in inst["instances"]:
                cx, cy = w2p(p["x"] + ox, p["y"] + oy)
                rad = max(1.5, min(6, 2.2 * p["scale"]))
                dr.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
                           outline=col, width=1)

    # fixed placements
    for sc, ox, oy in w.tiles:
        for e in sc.models:
            try:
                nm = e[1] if isinstance(e[1], str) else ""
                x, y = float(e[2]) + ox, float(e[3]) + oy
            except (TypeError, ValueError, IndexError):
                continue
            if not nm.strip():
                continue
            cx, cy = w2p(x, y)
            dr.rectangle([cx - 3, cy - 3, cx + 3, cy + 3], outline=(255, 255, 255))

    # tile grid
    for (ix, iy), stem in sorted(w.cells.items()):
        x0, y0 = w2p((ix - w.ix0) * R.TILE_M, (iy - w.iy0) * R.TILE_M)
        x1, y1 = w2p((ix - w.ix0 + 1) * R.TILE_M, (iy - w.iy0 + 1) * R.TILE_M)
        dr.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(255, 255, 255), width=1)
        dr.text((x0 + 8, y0 + 6), stem, fill=(255, 255, 255), font=font)
        dr.text((x0 + 8, y0 + 24),
                f"ix {ix}  iy {iy}   id {ix + iy * 1000}",
                fill=(210, 210, 210), font=small)

    dr.rectangle([0, H - 26, width, H], fill=(10, 10, 14))
    dr.text((8, H - 22),
            f"world '{w.copy}'  {w.nx}x{w.ny} tiles = {w.world_w:.0f}x{w.world_h:.0f} m"
            f"   ix {w.ix0}..{w.ix0 + w.nx - 1}, iy {w.iy0}..{w.iy0 + w.ny - 1}"
            f"   seam max |dz| {w.seam_max * 1000:.0f} mm",
            fill=(180, 180, 180), font=small)
    return im


def load_font(size):
    """A CJK-capable TTF if the machine has one, else PIL's bitmap default."""
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "C:/Windows/Fonts/simsun.ttc"):
        if pathlib.Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    return ImageFont.load_default()


def main():
    if "--world" in sys.argv[1:]:
        im = world_layout()
        p = OUT / "world_intro_layout.png"
        im.save(p)
        print(f"wrote {p} ({im.size[0]}x{im.size[1]})")
        return
    scenes = json.load(open(OUT / "scenes" / "intro_scenes.json",
                            encoding="utf-8"))
    # re-attach raw terrain blobs (not serialisable to json earlier)
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    import zipfile
    import zmap
    for f in sorted(pathlib.Path("extracted/netpak/intro").glob("*.zmap")):
        key = f.stem
        if key not in scenes:
            continue
        # re-read raw terrain blob (parse() keeps only metadata)
        d = f.read_bytes()
        z = zipfile.ZipFile(__import__("io").BytesIO(d[4:]))
        v = varfile.load_varbin(z.read("varbin"))["value"][0]
        scenes[key]["terrain_raw"] = v[1]
    for key, sc in scenes.items():
        if not sc.get("scenery") and not sc.get("objects"):
            continue
        im = scene_image(sc, key)
        p = OUT / f"{key}_layout.png"
        im.save(p)
        print("wrote", p)


if __name__ == "__main__":
    main()
