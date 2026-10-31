#!/usr/bin/env python3
"""render_anim.py - render the one fully-shipped actor as an animation strip.

`tools/action_link.py` showed the retail client ships exactly one complete
animated character: `骑宠_机甲摩托1阶_Z` -> `model/B056501A.cmz` +
`action/E41C7990.zip`, and that pack holds the five loose `.caf` clips.  This
tool poses that rig from a clip and rasterises it, so the animation can be
looked at without a browser.

Pipeline (identical to `caf_frames.py`, which feeds the web viewer):

    skin JSON holds the mesh in *bone-local bind space*:
        v_local = R_bind^-1 * (v_bind - p_bind)          (rigid skin, 1 bone)
    a clip key stores the inverse bone->parent rotation, so per frame:
        R_world[i] = R_world[parent] * conj(q_key)
        p_world[i] = p_world[parent] + R_world[parent] * (t_key * 0.01)
    and the posed vertex is:
        v_posed = p_world[bone] + R_world[bone] * v_local

Output: extracted/models/anim/<clip>_strip.png  (one row per clip)
        extracted/models/anim/<clip>_<frame>.png (single frames, large)
"""
import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from obj_render import render, rot, load_texture  # noqa: E402
from caf_frames import (qmul, qconj, rotmat, applyrot,  # noqa: E402
                        bone_world, SCALE)

RIG = "2682c5d5"
CMZ = ROOT / "extracted/models/cmz" / f"{RIG}.json"
SKIN = ROOT / "extracted/models/skin/_skin.json"
CAF = ROOT / "extracted/models/caf"
OUT = ROOT / "extracted/models/anim"

# part_index -> colour, matching the five 机甲摩托 parts in skin/_skin.json:
# 0 前轮, 1 后轮, 2 车身, 3 车头, 4 轮眉架子
PART_COLORS = [(86, 90, 98), (86, 90, 98), (196, 150, 74),
               (150, 158, 170), (168, 132, 104)]


def load():
    rig = json.loads(CMZ.read_text(encoding="utf-8"))
    bones = rig["bones"]
    rw_bind = [tuple(q) for q in rig["rw"]]
    pw_bind = [tuple(p) for p in rig["world"]]
    skin = json.loads(SKIN.read_text(encoding="utf-8"))[RIG]

    verts, uvs, faces, vbone, part_of = [], [], [], [], []
    for pi, p in enumerate(skin["parts"]):
        vbase = len(verts)                  # vertex offset, NOT the face count
        for v in p["verts"]:
            b = v["bones"][0]
            Rb, pb = rotmat(rw_bind[b]), pw_bind[b]
            vx, vy, vz = v["pos"]
            d = (vx - pb[0], vy - pb[1], vz - pb[2])
            verts.append((Rb[0][0] * d[0] + Rb[1][0] * d[1] + Rb[2][0] * d[2],
                          Rb[0][1] * d[0] + Rb[1][1] * d[1] + Rb[2][1] * d[2],
                          Rb[0][2] * d[0] + Rb[1][2] * d[1] + Rb[2][2] * d[2]))
            uvs.append(tuple(v.get("uv") or (0.0, 0.0)))
            vbone.append(b)
        for f in p["faces"]:
            faces.append((f[0] + vbase, f[1] + vbase, f[2] + vbase))
            part_of.append(pi)
    return bones, verts, uvs, faces, vbone, part_of


def locals_for(clip, frame):
    """clip keyframe -> {bone: (t_metres, q_local_inverse)}."""
    out = {}
    for tr in clip["tracks"]:
        k = tr["keys"][frame]
        t = (k[1] * SCALE, k[2] * SCALE, k[3] * SCALE)
        q = (k[4], k[5], k[6], k[7])
        out[tr["bone"]] = (t, qconj(q))
    return out


def pose(bones, verts, vbone, locals_by_bone):
    rw, pw = bone_world(bones, locals_by_bone)
    out = []
    for (x, y, z), b in zip(verts, vbone):
        R, p = rotmat(rw[b]), pw[b]
        out.append((p[0] + R[0][0] * x + R[0][1] * y + R[0][2] * z,
                    p[1] + R[1][0] * x + R[1][1] * y + R[1][2] * z,
                    p[2] + R[2][0] * x + R[2][1] * y + R[2][2] * z))
    return out


def bind_textures(part_names, spec_key="骑宠_机甲摩托1阶_Z"):
    """part name -> (w, h, rgb, alpha) using the SysFileVer asset spec.

    The spec carries `tex=<material>,tex/<guid>.jpg` pairs; skin parts are named
    after those materials (`机甲摩托_车身`).  Two wrinkles: the four wheel
    entries are all called `机甲摩托_轮子` while the mesh splits them into
    前轮/后轮, and the bike ships no material table of its own — the spec is the
    only place the binding exists.
    """
    from varfile import load as load_var
    reg = {}
    for r in load_var(str(ROOT / "extracted/netpak/SysFileVer.var"))["value"]:
        if len(r) > 1 and isinstance(r[0], str) and isinstance(r[1], str):
            reg.setdefault(r[0], r[1])
    spec = reg.get(spec_key, "")
    by_tail = {}
    for part in spec.split(";"):
        toks = [t.strip() for t in part.split(",")]
        name = path = None
        for t in toks:
            if t.startswith("tex="):
                name = t[4:]
            elif t.startswith("tex/"):
                path = t
        if name and path:
            by_tail.setdefault(name.rsplit("_", 1)[-1], path)

    out, report = {}, []
    for pn in part_names:
        tail = pn.rsplit("_", 1)[-1]
        path = by_tail.get(tail)
        if path is None and tail.endswith("轮"):
            path = by_tail.get("轮子")          # 前轮/后轮 share one material
        if path is None:
            report.append(f"  {pn}: no spec entry")
            continue
        p = ROOT / "extracted/netpak" / path
        tex = load_texture(p) if p.exists() else None
        if tex is None:
            report.append(f"  {pn}: {path} missing or unreadable")
            continue
        out[pn] = tex
        report.append(f"  {pn}: {path}  {tex[0]}x{tex[1]}")
    print("textures:\n" + "\n".join(report))
    return out


def cam_frame(pts, yaw, pitch, ctr):
    """View-space frame (ox, oy, ext) that fits every point in `pts`.

    Computed once over *all* frames so an animation strip shares one camera --
    fitting per frame would make the model appear to breathe.  ext is the
    larger projected span, which is what render() scales against.
    """
    m = rot(math.radians(yaw), math.radians(pitch), "z")
    rxs, rys = [], []
    for (x, y, z) in pts:
        x, y, z = x - ctr[0], y - ctr[1], z - ctr[2]
        rxs.append(m[0][0] * x + m[0][1] * y + m[0][2] * z)
        rys.append(m[1][0] * x + m[1][1] * y + m[1][2] * z)
    return ((max(rxs) + min(rxs)) / 2, (max(rys) + min(rys)) / 2,
            max(max(rxs) - min(rxs), max(rys) - min(rys)) or 1.0)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bones, verts, uvs, faces, vbone, part_of = load()
    print(f"rig {RIG}: {len(bones)} bones, {len(verts)} verts, {len(faces)} faces")

    skin = json.loads(SKIN.read_text(encoding="utf-8"))[RIG]
    part_names = [p["name"] for p in skin["parts"]]
    tex_by_part = bind_textures(part_names)

    # UV index == vertex index, so every corner carries its own uv.
    tris = [((a, a), (b, b), (c, c)) for a, b, c in faces]
    texes, tri_tex = [], []
    slot = {}
    for pn in part_names:
        t = tex_by_part.get(pn)
        if t is None:
            slot[pn] = None
            continue
        key = pn
        if key not in slot:
            slot[key] = len(texes)
            texes.append(t)
    for p in part_of:
        tri_tex.append(slot.get(part_names[p]))
    if not texes:
        tri_tex = None
    # white tint so the texture keeps its own colours; the part palette is
    # only used for the flat fallback.
    tri_rgb = ([(255, 255, 255)] * len(faces) if texes
               else [PART_COLORS[p % len(PART_COLORS)] for p in part_of])
    print(f"textured faces {sum(1 for k in (tri_tex or []) if k is not None)}"
          f"/{len(faces)}  atlases {len(texes)}")

    if "--check" in sys.argv:
        # fast invariant pass: the numbers below are what a wrong UV index or a
        # wrong vertex base would move.  No rasterising.
        u0 = min(min(u) for u in uvs)
        u1 = max(max(u) for u in uvs)
        bad = sum(1 for p in part_of
                  if max(faces[p]) >= len(verts) or min(faces[p]) < 0)
        print(f"uv range {u0:+.3f}..{u1:+.3f}   faces out of vertex range {bad}")
        ok = (len(tex_by_part) == len(part_names) and not bad
              and -0.01 <= u0 and u1 <= 1.01
              and all(k is not None for k in (tri_tex or [])))
        print("anim check passed" if ok else "anim check FAILED")
        return 0 if ok else 1

    clips = {}
    for cp in sorted(CAF.glob("*.json")):
        a = json.loads(cp.read_text(encoding="utf-8"))
        clips[a["name"]] = a

    # a shared camera: same framing for every frame so motion reads as motion
    allp = []
    for name, a in clips.items():
        n = len(a["tracks"][0]["keys"])
        for f in range(n):
            allp.extend(pose(bones, verts, vbone, locals_for(a, f)))
    lo = [min(p[k] for p in allp) for k in range(3)]
    hi = [max(p[k] for p in allp) for k in range(3)]
    print("world bbox over all frames: "
          + " ".join(f"{lo[k]:+.2f}..{hi[k]:+.2f}" for k in range(3)))

    # One camera for every clip and frame: fit the *projected* silhouette of
    # all posed vertices at the chosen yaw/pitch.  render()'s default fit is
    # the 3D bbox diagonal, which pads a 4.05 x 1.28 x 2.35 m bike with dead
    # margin; fitting per frame instead would make the model breathe.
    YAW, PITCH = 90.0, 8.0          # degrees; render() wants radians
    ctr = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2]
    frame = cam_frame(allp, YAW, PITCH, ctr)
    print(f"camera yaw {YAW:.0f} pitch {PITCH:.0f}  view-space frame "
          f"{frame[2]:.2f} m")

    cell = 460
    for name, a in clips.items():
        n = len(a["tracks"][0]["keys"])
        cols = min(n, 8)
        picks = [round(i * (n - 1) / max(1, cols - 1)) for i in range(cols)]
        ims = []
        for f in picks:
            pv = pose(bones, verts, vbone, locals_for(a, f))
            # centre on the shared bbox so frames do not jitter
            pv = [(x - ctr[0], y - ctr[1], z - ctr[2]) for x, y, z in pv]
            pix = render(pv, tris, cell, math.radians(YAW), math.radians(PITCH),
                         zoom=1.86, frame=frame,
                         bg=(15, 16, 21), up="z", uvs=uvs, texes=texes,
                         tri_tex=tri_tex, tri_rgb=tri_rgb)
            ims.append(pix)
        strip = _strip(ims, cell, f"{name}  ({n} keys, {n/30:.2f}s @30fps)")
        path = OUT / f"{name}_strip.png"
        strip.save(path)
        print(f"wrote {path}  frames {picks}")

    # one large frame per clip for detail
    for name, a in clips.items():
        n = len(a["tracks"][0]["keys"])
        f = n // 3
        pv = pose(bones, verts, vbone, locals_for(a, f))
        pv = [(x - ctr[0], y - ctr[1], z - ctr[2]) for x, y, z in pv]
        pix = render(pv, tris, 760, math.radians(YAW), math.radians(PITCH),
                     zoom=1.86, frame=frame,
                     bg=(15, 16, 21), up="z", uvs=uvs, texes=texes,
                     tri_tex=tri_tex, tri_rgb=tri_rgb)
        p = OUT / f"{name}_f{f:02d}.png"
        _write(p, 760, 760, pix)
        print("wrote", p)

    # 3/4 hero contact sheet: the broadside strip hides the front three-quarter
    # form, and a mount is usually judged from there.
    HY, HP = 132.0, 14.0
    hframe = cam_frame(allp, HY, HP, ctr)
    tiles, labels = [], []
    for name, a in clips.items():
        n = len(a["tracks"][0]["keys"])
        f = n // 3
        pv = pose(bones, verts, vbone, locals_for(a, f))
        pv = [(x - ctr[0], y - ctr[1], z - ctr[2]) for x, y, z in pv]
        pix = render(pv, tris, cell, math.radians(HY), math.radians(HP),
                     zoom=1.86, frame=hframe,
                     bg=(15, 16, 21), up="z", uvs=uvs, texes=texes,
                     tri_tex=tri_tex, tri_rgb=tri_rgb)
        tiles.append(pix)
        labels.append(name)
    sheet = _strip(tiles, cell, "mech mount (b056501a)  3/4 view   "
                               + "   ".join(labels))
    sheet.save(OUT / "hero.png")
    print("wrote", OUT / "hero.png")


def _strip(pixs, cell, label):
    from PIL import Image, ImageDraw, ImageFont
    n = len(pixs)
    pad = 6
    head = 26
    W = n * cell + (n + 1) * pad
    H = cell + head + 2 * pad
    im = Image.new("RGB", (W, H), (10, 11, 15))
    for i, pix in enumerate(pixs):
        im.paste(Image.frombytes("RGB", (cell, cell), bytes(pix)),
                 (pad + i * (cell + pad), head + pad))
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    dr.text((pad, 7), label, fill=(210, 214, 226), font=font)
    return im


def _write(path, w, h, pix):
    from PIL import Image
    Image.frombytes("RGB", (w, h), bytes(pix)).save(path)


if __name__ == "__main__":
    main()
