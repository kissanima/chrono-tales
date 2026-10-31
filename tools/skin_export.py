"""Export .cmz sbus (CoreSubmesh) skin data to JSON + renderable OBJs.

Output:
  extracted/models/skin/<guid>.json   - full parse: parts -> submeshes ->
                                        vertices(pos,normal,uv,influences), faces
  extracted/models/skin_obj/<guid>[_part].obj - geometry OBJ per part (positions +
                                        faces), for visual proof via obj_render.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbus


def safe(name):
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name)


def main():
    out_json = "extracted/models/skin"
    out_obj = "extracted/models/skin_obj"
    os.makedirs(out_json, exist_ok=True)
    os.makedirs(out_obj, exist_ok=True)

    for f in sorted(glob.glob("extracted/netpak/model/*.cmz")):
        stem = os.path.basename(f)[:-4]
        d = open(f, "rb").read()
        h = d.find(b"HSEM")
        end = d.find(b"1PAM", h)
        parts, consumed = sbus.parse_mesh_chunk(d, h + 4, end)
        assert consumed == end, "walk did not close: %d vs %d" % (consumed, end)

        doc = {"source": f, "parts": []}
        for p in parts:
            pd = {"name": p["name"], "submeshes": []}
            for sm in p["submeshes"]:
                sd = {
                    "index": sm["index"],
                    "vertexCount": sm["vertexCount"],
                    "faceCount": sm["faceCount"],
                    "nUV": sm["nUV"],
                    "min": sm["min"],
                    "scale": sm["scale"],
                    "vertices": [
                        {"pos": v[0], "nrm": v[1],
                         "uv": [list(u) for u in v[2]],
                         "infl": [[b, w] for b, w in v[3]]}
                        for v in sm["vertices"]
                    ],
                    "faces": [list(fc) for fc in sm["faces"]],
                }
                pd["submeshes"].append(sd)
            doc["parts"].append(pd)

        with open(os.path.join(out_json, stem + ".json"), "w") as fh:
            json.dump(doc, fh, ensure_ascii=False)

        # OBJ per part (all submeshes merged, groups by submesh)
        for pi, p in enumerate(parts):
            lines = ["# %s part %d: %s" % (stem, pi, p["name"])]
            base = 1
            for si, sm in enumerate(p["submeshes"]):
                lines.append("g %s_s%d" % (safe(p["name"]), si))
                for v in sm["vertices"]:
                    lines.append("v %.5f %.5f %.5f" % tuple(v[0]))
                for v in sm["vertices"]:
                    lines.append("vn %.4f %.4f %.4f" % tuple(v[1]))
                for fc in sm["faces"]:
                    a, b, c = fc
                    lines.append("f %d//%d %d//%d %d//%d" % (
                        base + a, base + a, base + b, base + b, base + c, base + c))
                base += sm["vertexCount"]
            suf = "" if len(parts) == 1 else "_%d" % pi
            with open(os.path.join(out_obj, "%s%s.obj" % (stem, suf)), "w") as fh:
                fh.write("\n".join(lines) + "\n")

        nv = sum(sm["vertexCount"] for p in parts for sm in p["submeshes"])
        print("%s: parts=%d verts=%d -> skin/%s.json + %d OBJs"
              % (stem, len(parts), nv, stem, len(parts)))


if __name__ == "__main__":
    main()
