#!/usr/bin/env python3
"""Export decoded .cmz `sbus` skin parts to OBJ (+ a JSON summary).

Each part becomes one OBJ group named after the bone(s) it is rigidly bound
to.  Vertex positions are already in the model's bind space (Z up, metres,
the same frame as .53wf and the .cmz skeleton), so no skinning transform is
applied - the decode is validated by bone index range, unit normals and
weight sums, all of which check out.

Usage:
    python sbus_obj.py [--root DIR] [--obj DIR] [--json DIR]
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from sbus_decode import chunk_bounds, decode, part_name  # noqa: E402

BONES = {"2682c5d5": 43, "482f1708": 109, "5995da06": 119, "5e493068": 119,
         "72b5ee25": 119, "a7b5710e": 119, "d1500c0c": 119, "f2d259a2": 119,
         "ff725ba4": 119}


def sbus_offsets(d, start, end):
    """File offsets of every `sbus` marker in [start, end)."""
    out, i = [], start
    while True:
        j = d.find(b"sbus", i, end)
        if j < 0:
            return out
        out.append(j)
        i = j + 1


def write_obj(parts, path, stem):
    out = [f"# {stem}.cmz skinned parts - decoded from MESH `sbus` records",
           "# Z up, metres. Groups are named by the rigidly-bound bone."]
    nv = 1
    for p in parts:
        if not p:
            continue
        bone = max(p["verts"][0]["bones"], default=-1) if p["verts"] else -1
        used = sorted({b for v in p["verts"] for b in v["bones"]})
        tag = "bones_" + "_".join(str(b) for b in used[:4]) if used else "nobone"
        out.append(f"g {stem}_p{p['part_index']}_{tag}")
        for v in p["verts"]:
            out.append("v %.5f %.5f %.5f" % tuple(v["pos"]))
            out.append("vt %.6f %.6f" % tuple(v["uv"] if v["uv"] else (0.0, 0.0)))
            out.append("vn %.4f %.4f %.4f" % tuple(v["nrm"]))
        for f in p["faces"]:
            # one v / vt / vn per vertex, so all three indices coincide
            a, b, c = f[0] + nv, f[1] + nv, f[2] + nv
            out.append(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}")
        nv += len(p["verts"])
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path,
                    default=pathlib.Path("extracted/netpak"))
    ap.add_argument("--obj", type=pathlib.Path,
                    default=pathlib.Path("extracted/models/skin_obj"))
    ap.add_argument("--json", type=pathlib.Path,
                    default=pathlib.Path("extracted/models/skin"))
    a = ap.parse_args()
    a.obj.mkdir(parents=True, exist_ok=True)
    a.json.mkdir(parents=True, exist_ok=True)

    print(f'{"file":10} {"parts":>5} {"verts":>7} {"faces":>7}  name')
    summary = {}
    for p in sorted(a.root.rglob("*.cmz")):
        d = p.read_bytes()
        mesh = next((b for b in chunk_bounds(d) if b[1] == "MESH"), None)
        if not mesh:
            continue
        off, _, end = mesh
        marks = sbus_offsets(d, off + 8, end)
        parts = []
        for k, m in enumerate(marks):
            q = decode(d[m:marks[k + 1] if k + 1 < len(marks) else end])
            if not q:
                continue
            q["name"] = part_name(d, m) or f"part{len(parts)}"
            parts.append(q)
        nv = sum(q["n_verts"] for q in parts)
        nf = sum(len(q["faces"]) for q in parts)
        print(f"{p.stem:10} {len(parts):5} {nv:7} {nf:7}  {parts[0]['name']}")
        write_obj(parts, a.obj / (p.stem + ".obj"), p.stem)
        summary[p.stem] = dict(bones=BONES.get(p.stem), parts=[{
            "name": q["name"], "part_index": q["part_index"],
            "n_verts": q["n_verts"], "n_faces": q["n_faces"],
            "base": q["base"], "step": q["step"],
            "bones_used": sorted({b for v in q["verts"] for b in v["bones"]}),
            "verts": q["verts"], "faces": q["faces"],
        } for q in parts])
    (a.json / "_skin.json").write_text(json.dumps(summary, ensure_ascii=False),
                                       encoding="utf-8")
    print(f"\nwrote {len(summary)} OBJ -> {a.obj}")


if __name__ == "__main__":
    main()
