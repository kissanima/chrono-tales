#!/usr/bin/env python3
"""Assemble the .cmz equipment parts into one character model.

The shipped `.cmz` files are not a wardrobe of unrelated props: seven of them
(`5995da06` clothes, `5e493068` default shoes, `72b5ee25` gloves,
`a7b5710e` arm guards, `d1500c0c` leg guards, `f2d259a2` face 09,
`ff725ba4` tier-7 shoes) carry **the same 119-bone rig** - identical bone
names and, to within 2 cm on four bones, an identical bind pose (height
1.7536 m in every one of them).  `482f1708` (hairstyle) uses a 109-bone
variant of the same body, so it merges too.  `2682c5d5` is the 43-bone mech
and is a separate model.

Because every part is rigidly skinned (exactly one bone per vertex) and the
vertex positions are stored in the model's own rest space, parts from
different files can simply be concatenated - no skinning transform is needed
to put them together.

Usage:
    python assemble_char.py [--out OBJ] [--with-hair] [--mech]
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from sbus_decode import chunk_bounds, blobs, decode  # noqa: E402
from sbus_obj import part_names  # noqa: E402

HUMAN = ["5995da06", "5e493068", "72b5ee25", "a7b5710e",
         "d1500c0c", "f2d259a2", "ff725ba4"]
HAIR = "482f1708"
MECH = "2682c5d5"
ROOT = pathlib.Path("extracted/netpak")


def load_parts(stem):
    d = (ROOT / "unknown" / (stem + ".cmz")).read_bytes()
    mesh = next((b for b in chunk_bounds(d) if b[1] == "MESH"), None)
    if not mesh:
        return []
    off, _, end = mesh
    raw = blobs(d, off + 8, end)
    parts = [q for q in (decode(b) for b in raw) if q]
    names = part_names(d, off, end, len(parts))
    for q, nm in zip(parts, names):
        q["name"] = nm
        q["stem"] = stem
    return parts


def assemble(stems, out):
    lines = ["# Chrono Tales character - assembled from .cmz equipment parts",
             "# Z up, metres. Each group is one part; vertices are rigidly",
             "# bound to a single bone, so no transform is applied."]
    nv = 1
    report = []
    for stem in stems:
        for p in load_parts(stem):
            used = sorted({b for v in p["verts"] for b in v["bones"]})
            lines.append(f"g {stem}_{p['name']}_b{'_'.join(map(str, used[:3]))}")
            for v in p["verts"]:
                lines.append("v %.5f %.5f %.5f" % tuple(v["pos"]))
                lines.append("vt %.6f %.6f" % tuple(v["uv"] or (0.0, 0.0)))
                lines.append("vn %.4f %.4f %.4f" % tuple(v["nrm"]))
            for f in p["faces"]:
                a, b, c = f[0] + nv, f[1] + nv, f[2] + nv
                lines.append(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}")
            nv += len(p["verts"])
            zs = [v["pos"][2] for v in p["verts"]]
            report.append((stem, p["name"], len(p["verts"]), len(p["faces"]),
                           min(zs), max(zs), used[:3]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path("extracted/models/character.obj"))
    ap.add_argument("--with-hair", action="store_true",
                    help="include the 109-bone hairstyle (482f1708)")
    ap.add_argument("--mech", action="store_true",
                    help="assemble the 43-bone mech instead of the human")
    a = ap.parse_args()

    stems = [MECH] if a.mech else (HUMAN + ([HAIR] if a.with_hair else []))
    rep = assemble(stems, a.out)
    print(f'{"file":10} {"part":22} {"verts":>6} {"faces":>6} '
          f'{"z-range":>16}  bones')
    for stem, nm, nv, nf, z0, z1, used in rep:
        print(f"{stem:10} {nm:22} {nv:6} {nf:6} "
              f"{z0:7.3f}..{z1:7.3f}  {used}")
    tv = sum(r[2] for r in rep)
    tf = sum(r[3] for r in rep)
    lo = min(r[4] for r in rep)
    hi = max(r[5] for r in rep)
    print(f"\ntotal {tv} verts, {tf} faces; model spans z {lo:.3f}..{hi:.3f} "
          f"({hi - lo:.3f} m)")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
