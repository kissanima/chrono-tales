#!/usr/bin/env python3
"""Decode the six `zmap` map payloads in netpak.

These six varbin payloads are not tables — they are **map files**, the same
serialisation the scene editor writes to `<mapname>.zmap`.  The layout is not
guessed: `ui/video/world.gbc::SaveMapData()` builds the var in exactly this
order, and `OnEditData()` reads it back with the same indices.

    #enum MAP_VER,MAP_HEIGHTDATA,MAP_LAYERDATA,MAP_WIDGETDATA,MAP_DATAEX,MAP_MODELDATA

    0  MAP_VER         int    must be 100 (OnEditData returns -2 otherwise)
    1  MAP_HEIGHTDATA  blob   height field
    2  MAP_LAYERDATA   var    [version, width, height, 24-bit pixels]
    3  MAP_WIDGETDATA  var    [map name, decoration objects...]
    4  MAP_DATAEX      var    6 texture layers + light + path layer + fog
    5  MAP_MODELDATA   var    [fixed model placements...]

`MAP_DATAEX` is indexed by

    #enum MAPBASE_LAYER1..MAPBASE_LAYER6, MAPBASE_LIGHT, MAPBASE_PATHLAYERNO,
          MAPBASE_FOGCOR, MAPBASE_PATHLAYERNOEX

and each of the six layers by `MAPLAYER_TEXNAME, MAPLAYER_TRTYPE, MAPLAYER_LEAVE`.
Each `MAP_MODELDATA` row is indexed by the 18-name `FIXMODEL_*` enum.

Height field layout (`MAP_HEIGHTDATA`), determined from the data:

    u16 version (=2)          \\ 12-byte header, identical in all six copies
    u32  a (=64176)            |
    u32  b (=49864)            |
    u16  c (=0x4461)          /
    129 * 258 bytes           height grid, 0..255
    u32                       trailer (not crc32/adler32/sum)

The grid is **two stacked 129x129 terrain patches**, matching `TerrainLodSIZE`
256 at half resolution: 129x129 vertices per patch, and the layer map below is
exactly 2x that in each axis (2 x 256x256).  Patch 0 is flat plains (values
12..41), patch 1 is mountains (0..255) — the steep half looks like noise by
entropy (7.95 bits/byte) but renders as coherent relief; the high entropy is
quantisation dithering, which only appears where there is a gradient.

Heights are 0..255 over `DEFWORLD_MINZ..DEFWORLD_MAXZ` = -50..450 world units
(`#define` in `def.gbc`), so `z = -50 + v * 500 / 255`.

The grid is bytes, not u16: 45.3% of aligned u16 pairs have high byte == low
byte where chance would give ~0.4%, i.e. the pairs are two adjacent cells that
happen to be equal.

The layer map is 3 bytes per cell, **interleaved** (a planar reading is pure
noise: adjacent-byte delta 255 vs 0 for interleaved).  For `w100` it is almost
entirely two colours — `ff0000` above the midpoint and `000000` below — plus a
small painted patch, i.e. the map is barely textured.

Output: extracted/maps/
"""
import csv
import json
import os
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import varbin_tree                      # noqa: E402
from obj_render import write_png        # noqa: E402

OUT = HERE.parent / "extracted" / "maps"

# the six payloads that are map files rather than tables
MAP_BLOBS = ["4514d98f", "6010fb33", "6b45f638", "9feba4ac", "cc5de97f",
             "da80ff38"]

HEIGHT_HDR = 12
HEIGHT_W, HEIGHT_H = 129, 258
HEIGHT_TRAILER = 4

MAPBASE = ["layer1", "layer2", "layer3", "layer4", "layer5", "layer6",
           "light", "path_layer_no", "fog_colour", "path_layer_no_ex"]
MAPLAYER = ["tex_name", "tr_type", "leave"]

FIXMODEL = ["id", "res_name", "x", "y", "z", "size", "aspect", "ani",
            "play_speed", "particle", "part_show", "tips", "sound", "s_delay",
            "vol", "style", "param", "half"]


def flatten(v, depth=0):
    """Var tree -> JSON-able (bytes become a length marker)."""
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>"
    if isinstance(v, (list, tuple)):
        return [flatten(x, depth + 1) for x in v]
    return v


def decode_height(blob):
    """-> dict(header, grid, trailer)."""
    if len(blob) != HEIGHT_HDR + HEIGHT_W * HEIGHT_H + HEIGHT_TRAILER:
        raise ValueError(f"height blob is {len(blob)} bytes, expected "
                         f"{HEIGHT_HDR + HEIGHT_W * HEIGHT_H + HEIGHT_TRAILER}")
    ver, a, b, c = struct.unpack_from("<HIIH", blob, 0)
    grid = blob[HEIGHT_HDR:HEIGHT_HDR + HEIGHT_W * HEIGHT_H]
    trailer = struct.unpack_from("<I", blob, len(blob) - 4)[0]
    return {"version": ver, "a": a, "b": b, "c": c,
            "width": HEIGHT_W, "height": HEIGHT_H,
            "min": min(grid), "max": max(grid),
            "grid": grid, "trailer": trailer}


def write_height_pngs(path_stem, grid, scale=3):
    """Plain greyscale + a colourised relief, both scaled up."""
    lo, hi = min(grid), max(grid)
    span = max(1, hi - lo)
    w, h = HEIGHT_W, HEIGHT_H
    grey = bytearray(w * scale * h * scale * 3)
    relief = bytearray(w * scale * h * scale * 3)
    for y in range(h * scale):
        sy = y // scale
        for x in range(w * scale):
            sx = x // scale
            v = grid[sy * w + sx]
            t = (v - lo) / span
            g = int(t * 255)
            o = (y * w * scale + x) * 3
            grey[o:o + 3] = bytes((g, g, g))
            # simple hillshade: compare with the cell below-right
            gx = grid[sy * w + min(w - 1, sx + 1)] - v
            gy = grid[min(h - 1, sy + 1) * w + sx] - v
            shade = max(-1.0, min(1.0, (gx + gy) / 24.0))
            r = int(max(0, min(255, t * 200 + 40 + shade * 70)))
            gg = int(max(0, min(255, t * 170 + 60 + shade * 70)))
            b = int(max(0, min(255, t * 120 + 60 + shade * 50)))
            relief[o:o + 3] = bytes((r, gg, b))
    write_png(path_stem.with_name(path_stem.name + "_height.png"),
              w * scale, h * scale, grey)
    write_png(path_stem.with_name(path_stem.name + "_relief.png"),
              w * scale, h * scale, relief)


def decode_layer(field):
    """[version, width, height, pixels] -> dict + PNG."""
    if not (isinstance(field, list) and len(field) == 4
            and isinstance(field[3], bytes)):
        raise ValueError("layer field is not [version, w, h, pixels]")
    ver, w, h, pix = field
    if len(pix) != w * h * 3:
        raise ValueError(f"layer pixels {len(pix)} != {w}x{h}x3")
    return {"version": ver, "width": w, "height": h, "pixels": pix}


def decode_decos(field):
    """[map name, obj...] -> (name, rows).

    Each decoration is `[kind, res_name, model, texture, 0, n, blob, 0]` and the
    blob is `16 zero bytes + n * 9 bytes`.  Each 9-byte instance is four u16 in
    hundredths followed by one u8:

        x, y     world position (z is 0 — the runtime derives it from the
                 height field, cf. `hz=GetZHeight(x,y)+v[FIXMODEL_Z]`)
        z        always 0 in every shipped instance
        angle    ~0..646, so half-degrees (0..720) rather than degrees
        u8       10..70 across the corpus, most likely a scale or variant

    The last two are inferred from their ranges, not from code.
    """
    name = field[0] if field and isinstance(field[0], str) else ""
    rows = []
    for d in field[1:]:
        if not isinstance(d, list):
            continue
        row = [flatten(x) for x in d]
        blob = d[6] if len(d) > 6 and isinstance(d[6], bytes) else b""
        n = d[5] if len(d) > 5 and isinstance(d[5], int) else 0
        if blob and len(blob) >= 16 and len(blob) == 16 + n * 9:
            for i in range(n):
                o = 16 + i * 9
                x, y, z, ang = struct.unpack_from("<4H", blob, o)
                row.append(f"{x/100:.2f};{y/100:.2f};{z/100:.2f};"
                           f"{ang/100:.2f};{blob[o+8]}")
        rows.append(row)
    return name, rows


DECO_COLS = ["kind", "res_name", "model", "texture", "f0", "count", "blob",
             "f7", "instances(x;y;z;angle;b8)"]


def decode_models(field):
    rows = []
    for d in field:
        if isinstance(d, list):
            row = [flatten(x) for x in d]
            row += [""] * (len(FIXMODEL) - len(row))
            rows.append(row[:len(FIXMODEL)])
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    payloads = dict(varbin_tree.payloads())
    summary = []

    for blob_id in MAP_BLOBS:
        rec = payloads[blob_id]["value"][0]
        ver, height, layer, widget, dataex, models = rec

        if ver != 100:
            raise SystemExit(f"{blob_id}: MAP_VER is {ver}, expected 100")

        stem = OUT / blob_id
        h = decode_height(height)

        lay = decode_layer(layer)
        write_png(OUT / f"{blob_id}_layer.png",
                  lay["width"], lay["height"], bytearray(lay["pixels"]))

        write_height_pngs(stem, h["grid"])

        map_name, decos = decode_decos(widget)
        mdl = decode_models(models)

        layers = []
        for i in range(6):
            e = dataex[i] if i < len(dataex) else None
            if isinstance(e, list):
                layers.append({MAPLAYER[k]: flatten(e[k])
                               for k in range(min(len(e), len(MAPLAYER)))})
            else:
                layers.append(None)

        meta = {
            "blob": blob_id,
            "format": "zmap",
            "map_name": map_name,
            "version": ver,
            "height": {k: h[k] for k in
                       ("version", "a", "b", "c", "width", "height",
                        "min", "max", "trailer")},
            "height_patches": {"count": 2, "each": [129, 129],
                               "z_world": [-50, 450],
                               "z_of_value": "-50 + v * 500 / 255"},
            "layer": {"version": lay["version"], "width": lay["width"],
                      "height": lay["height"]},
            "layers": layers,
            "light": flatten(dataex[6]) if len(dataex) > 6 else None,
            "path_layer_no": flatten(dataex[7]) if len(dataex) > 7 else None,
            "fog_colour": flatten(dataex[8]) if len(dataex) > 8 else None,
            "path_layer_no_ex": flatten(dataex[9]) if len(dataex) > 9 else None,
            "decorations": len(decos),
            "models": len(mdl),
        }
        (OUT / f"{blob_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

        with open(OUT / f"{blob_id}_models.csv", "w", newline="",
                  encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(FIXMODEL)
            w.writerows(mdl)

        with open(OUT / f"{blob_id}_decos.csv", "w", newline="",
                  encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if decos:
                w.writerow(DECO_COLS[:len(decos[0])])
                w.writerows(decos)

        summary.append((blob_id, map_name, h["min"], h["max"],
                        len(layers), len(decos), len(mdl)))

    print(f"{'blob':10s} {'map':8s} {'hmin':>5s} {'hmax':>5s} "
          f"{'lay':>4s} {'deco':>5s} {'model':>6s}")
    print("-" * 52)
    for s in summary:
        print(f"{s[0]:10s} {s[1]:8s} {s[2]:5d} {s[3]:5d} {s[4]:4d} "
              f"{s[5]:5d} {s[6]:6d}")
    print(f"\nwrote {len(summary)} maps -> {OUT}")


if __name__ == "__main__":
    main()
