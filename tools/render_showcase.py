#!/usr/bin/env python3
"""render_showcase.py - labelled 3-view textured showcase of the decoded meshes.

Renders every mesh that resolves at least one texture from `texmap.json` from
three azimuths and composes them into one contact sheet with per-mesh labels
(mesh hash, part/material name, tri count).  Uses obj_render.py for the
rasteriser and Pillow only for composition + text.

Usage:
    python tools/render_showcase.py [--tile 240] [--cols 2] [--out FILE]
"""
import argparse
import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import obj_render as R  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

ROOT = HERE.parent
MODELS = ROOT / "extracted" / "models"

# three azimuths around the model, same gentle pitch
VIEWS = [(28.0, 14.0), (148.0, 14.0), (268.0, 14.0)]
BG = R.BG
LABEL_H = 30
GAP = 8


def to_image(pix, size):
    return Image.frombytes("RGB", (size, size), bytes(pix))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tile", type=int, default=240)
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--out", type=pathlib.Path,
                    default=MODELS / "showcase.png")
    ap.add_argument("--texmap", type=pathlib.Path,
                    default=MODELS / "texmap.json")
    a = ap.parse_args()

    if not HAVE_PIL:
        print("Pillow required for the showcase composition")
        return

    paths, mesh_paths = ({}, {})
    if a.texmap.exists():
        paths, mesh_paths = R.texmap_paths(json.load(open(a.texmap, encoding="utf-8")))

    cache = {}
    cards = []
    objs = sorted((MODELS / "obj").glob("*.obj"))
    for path in objs:
        verts, uvs, groups = R.load_obj(path)
        tris = [t for _, g in groups for t in g]
        if not tris:
            continue
        texes, tri_tex = R.bind_textures(path.stem, groups, paths, mesh_paths, cache)
        got = sum(1 for k in (tri_tex or []) if k is not None)
        if not got:
            continue
        # material / part label: first OBJ group name carries the material
        gname = groups[0][0] if groups else ""
        label = f'{path.stem}'
        sub = f'{got} tex  {len(tris)} tris'
        if gname:
            pretty = gname.split("_")[-1] if "_" in gname else gname
            if pretty:
                sub = f'{pretty}  ·  {sub}'
        views = []
        for yaw, pitch in VIEWS:
            pix = R.render(verts, tris, a.tile,
                           yaw=math.radians(yaw), pitch=math.radians(pitch),
                           zoom=0.82, up="z", uvs=uvs,
                           texes=texes, tri_tex=tri_tex)
            views.append(to_image(pix, a.tile))
        cards.append((label, sub, views))
        print(f'{path.stem}: {sub}')

    if not cards:
        print("no textured meshes found")
        return

    # layout
    tw = a.tile
    card_w = 3 * tw + 2 * 4
    card_h = tw + LABEL_H
    cols = a.cols
    rows = (len(cards) + cols - 1) // cols
    pad = 14
    title_h = 46
    W = cols * card_w + (cols + 1) * pad
    H = title_h + rows * card_h + (rows + 1) * pad
    sheet = Image.new("RGB", (W, H), (12, 13, 17))
    dr = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 15)
        big = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 22)
        small = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 12)
    except Exception:
        font = big = small = ImageFont.load_default()

    dr.text((pad, 12), "Chrono Tales — textured meshes, 3 views", fill=(230, 232, 240), font=big)
    dr.text((pad, 34), f'{len(cards)} meshes · key+fill studio light · texmap-driven',
            fill=(120, 128, 144), font=small)

    for i, (label, sub, views) in enumerate(cards):
        cx = pad + (i % cols) * (card_w + pad)
        cy = title_h + pad + (i // cols) * (card_h + pad)
        # label
        dr.text((cx, cy), label, fill=(224, 200, 150), font=font)
        dr.text((cx + 120, cy + 2), sub, fill=(130, 140, 158), font=small)
        vy = cy + LABEL_H - 6
        for k, im in enumerate(views):
            sheet.paste(im, (cx + k * (tw + 4), vy))

    a.out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(a.out)
    print(f'\n{a.out}  ({W}x{H}, {len(cards)} cards)')


if __name__ == "__main__":
    main()
