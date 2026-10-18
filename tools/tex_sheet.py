"""Contact sheet of archive images, labelled with their hash, for visual identification.

The archive stores every image under the MPQ hash of its path, and the
GUID <-> filename table is not shipped, so identifying a texture by eye is the
only way to name the ones the entity DB does not mention.
"""
import csv
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

TILE = 128
LABEL = 14
COLS = 8


def collect(arc, want=("jpg", "png")):
    man = os.path.join(ROOT, "extracted", arc, "_manifest.csv")
    out = []
    for r in csv.DictReader(open(man, encoding="utf-8")):
        if r["detected"] in want:
            out.append((int(r["hash"], 16), r["detected"],
                        os.path.join(ROOT, "extracted", arc, r["path"]),
                        int(r["size"])))
    out.sort()
    return out


def main():
    arcs = sys.argv[1:] or ["netpak"]
    items = []
    for a in arcs:
        items += [(a,) + it for it in collect(a)]
    rows = (len(items) + COLS - 1) // COLS
    W, H = COLS * TILE, rows * (TILE + LABEL)
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    dr = ImageDraw.Draw(sheet)
    for i, (arc, h, ext, path, size) in enumerate(items):
        cx, cy = (i % COLS) * TILE, (i // COLS) * (TILE + LABEL)
        try:
            im = Image.open(path).convert("RGB")
            im.thumbnail((TILE - 4, TILE - 4))
            sheet.paste(im, (cx + (TILE - im.width) // 2, cy + (TILE - im.height) // 2))
        except Exception:
            dr.rectangle([cx + 2, cy + 2, cx + TILE - 2, cy + TILE - 2], outline=(200, 60, 60))
        dr.text((cx + 3, cy + TILE), f"{h:08x} {ext} {size // 1024}k", fill=(200, 200, 210))
    out = os.path.join(ROOT, "extracted", "models", "tex_sheet.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sheet.save(out)
    print(f"{len(items)} images -> {out}  ({W}x{H})")


if __name__ == "__main__":
    main()
