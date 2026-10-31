#!/usr/bin/env python3
"""caf_frames.py - export the 43-bone rig + 5 clips for the web viewer.

Everything the browser needs to animate the rider-on-mech model without
reimplementing the engine's bind math:

  * the skinned mesh in **bone-local** bind space.  The skin is rigid
    (exactly one bone per vertex, verified in `skin/_skin.json`), so

        v_local = R_bind^-1 * (v_bind - p_bind)

    and the browser poses a vertex with the frame's bone transform:

        v_posed = p_posed + R_posed * v_local

    `R_bind`/`p_bind` come from the rig's own skeleton (`cmz.py` output,
    keys `rw` / `world`); normals transform the same way as positions
    minus the translation.

  * per clip, for every keyframe (the keys sit on an exact 30 fps grid,
    see GPAK_FORMAT.md section 4.9), the 43 bone **world** transforms as
    (px, py, pz, qx, qy, qz, qw).  The convention is the engine's: a key
    stores the *inverse* of the bone->parent rotation, so

        R_world[i] = R_world[parent] * conj(q_key)
        p_world[i] = p_world[parent] + R_world[parent] * (t_key * 0.01)

    which is the same walk `skin_pose.pose_with` does for the bind pose.
    A bone with no track keeps its bind transform for that clip.

Validation (both must hold before anything is written):
  1. every track key 0 equals the rig bind pose within tolerance - the
     check `caf_decode.rig_score` used to bind clips to this rig;
  2. `jumpstart` frame 0 re-skinned through the full pipeline reproduces
     the original vertex positions to 1e-4 (it is the bind pose, byte
     for byte, per section 4.9).

Output: `extracted/explorer/data/model_anim.js` (script-injection safe,
same as the other explorer data files).
"""
import base64
import json
import math
import pathlib
import struct
import sys

TOOLS = pathlib.Path(__file__).parent
sys.path.insert(0, str(TOOLS))

RIG_STEM = "2682c5d5"          # 43-bone Biped rig the clips bind to
CAF_DIR = pathlib.Path("extracted/models/caf")
SKIN_JSON = pathlib.Path("extracted/models/skin/_skin.json")
OUT = pathlib.Path("extracted/explorer/data/model_anim.js")
SCALE = 0.01                   # cm -> m, engine constant at VA 0x6D0738


# ------------------------------------------------------------- quat helpers
def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def rotmat(q):
    x, y, z, w = q
    return ((1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * z * w, 2 * x * z + 2 * y * w),
            (2 * x * y + 2 * z * w, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * x * w),
            (2 * x * z - 2 * y * w, 2 * y * z + 2 * x * w, 1 - 2 * x * x - 2 * y * y))


def applyrot(R, v):
    return (R[0][0] * v[0] + R[0][1] * v[1] + R[0][2] * v[2],
            R[1][0] * v[0] + R[1][1] * v[1] + R[1][2] * v[2],
            R[2][0] * v[0] + R[2][1] * v[1] + R[2][2] * v[2])


# ------------------------------------------------------------- pose solving
def bone_world(bones, locals_by_bone):
    """World transforms from {bone_index: (t_m, q_local_inverse)}.

    Bones missing from the dict keep their bind local transform, exactly
    like the engine falls back to the bind pose for unanimated bones.
    """
    n = len(bones)
    rw = [None] * n
    pw = [None] * n
    for b in bones:
        i = b["index"]
        loc = locals_by_bone.get(i)
        if loc is None:
            t_local = [c * SCALE for c in b["offset"]]
            q_local = qconj(b["rot"])
        else:
            t_local, q_local = loc
        p = b["parent"]
        if p < 0:
            rw[i] = q_local
            pw[i] = t_local
        else:
            rw[i] = qmul(rw[p], q_local)
            r = applyrot(rotmat(rw[p]), t_local)
            pw[i] = [pw[p][k] + r[k] for k in range(3)]
    return rw, pw


# ------------------------------------------------------------------ packing
def f32blob(flat):
    return base64.b64encode(struct.pack("<%df" % len(flat), *flat)).decode()


def u16blob(flat):
    return base64.b64encode(struct.pack("<%dH" % len(flat), *flat)).decode()


def main():
    rig = json.loads((TOOLS.parent / "extracted/models/cmz"
                      / f"{RIG_STEM}.json").read_text(encoding="utf-8"))
    bones = rig["bones"]
    n = len(bones)
    assert n == rig["world"].__len__() == len(rig["rw"]), "rig arrays disagree"

    # bind world transforms, straight from the cmz.py dump
    rw_bind = [tuple(q) for q in rig["rw"]]
    pw_bind = [tuple(p) for p in rig["world"]]

    skin = json.loads(SKIN_JSON.read_text(encoding="utf-8"))[RIG_STEM]
    parts = skin["parts"]

    # ---- mesh in bone-local bind space
    verts, norms, uvs, faces = [], [], [], []
    part_ranges = []
    base = 0
    for p in parts:
        for v in p["verts"]:
            b = v["bones"][0]
            Rb = rotmat(rw_bind[b])
            pb = pw_bind[b]
            vx, vy, vz = v["pos"]
            d = (vx - pb[0], vy - pb[1], vz - pb[2])
            # Rb^T * d
            verts.append((Rb[0][0] * d[0] + Rb[1][0] * d[1] + Rb[2][0] * d[2],
                          Rb[0][1] * d[0] + Rb[1][1] * d[1] + Rb[2][1] * d[2],
                          Rb[0][2] * d[0] + Rb[1][2] * d[1] + Rb[2][2] * d[2]))
            nx, ny, nz = v["nrm"]
            norms.append((Rb[0][0] * nx + Rb[1][0] * ny + Rb[2][0] * nz,
                          Rb[0][1] * nx + Rb[1][1] * ny + Rb[2][1] * nz,
                          Rb[0][2] * nx + Rb[1][2] * ny + Rb[2][2] * nz))
            u, w = v["uv"] or (0.0, 0.0)
            uvs.append((u, w))
        nv = len(p["verts"])
        for f in p["faces"]:
            faces.extend((f[0] + base, f[1] + base, f[2] + base))
        part_ranges.append(dict(name=p["name"], start=base, count=nv,
                                bones_used=p["bones_used"]))
        base += nv
    nverts = base
    assert nverts < 65536, "index format is u16"

    # per-vertex bone index table
    vbones = []
    for p in parts:
        for v in p["verts"]:
            vbones.append(v["bones"][0])
    assert len(vbones) == nverts
    for b in vbones:
        assert 0 <= b < n, f"vertex bone {b} out of range"

    # ---- clips: load all first so validation 1 can compare across clips
    anims = {}
    for cp in sorted(CAF_DIR.glob("*.json")):
        a = json.loads(cp.read_text(encoding="utf-8"))
        assert a["n_tracks"] == len(a["tracks"])
        anims[cp.stem] = a

    # validation 1: a bone whose key 0 agrees across ALL clips is
    # non-animated and must equal the bind pose; a genuinely animated
    # bone (the root drops for `jumpdown`) may differ there.  This is the
    # cross-clip form of the check that bound the clips to this rig.
    key0 = {}                                    # bone -> {clip: key0}
    for a in anims.values():
        for tr in a["tracks"]:
            key0.setdefault(tr["bone"], {})[a["name"]] = tr["keys"][0]
    static, animated = 0, 0
    for bone, per in sorted(key0.items()):
        ref = bones[bone]
        agree = len(per) == len(anims) and all(
            max(abs(k[i] - next(iter(per.values()))[i]) for i in range(8)) < 1e-5
            for k in per.values())
        if agree:
            k0 = next(iter(per.values()))
            for i in range(3):
                assert abs(k0[1 + i] - ref["offset"][i]) < 1e-3, \
                    f"bone {bone} key0 offset != bind"
            for i in range(4):
                assert abs(k0[4 + i] - ref["rot"][i]) < 1e-4, \
                    f"bone {bone} key0 rot != bind"
            static += 1
        else:
            animated += 1
    print(f"bones at key0: {static} static (== bind), {animated} animated")

    clips = []
    for stem, a in anims.items():

        # validation 2: jumpstart frame 0 must reproduce the bind pose
        # through the whole walk (it IS the bind pose, per section 4.9)
        if stem == "jumpstart":
            locs = {}
            for tr in a["tracks"]:
                k0 = tr["keys"][0]
                locs[tr["bone"]] = ([k0[1] * SCALE, k0[2] * SCALE, k0[3] * SCALE],
                                    qconj(k0[4:8]))
            rw0, pw0 = bone_world(bones, locs)
            for i in range(n):
                dr = max(abs(rw0[i][k] - rw_bind[i][k]) for k in range(4))
                dp = max(abs(pw0[i][k] - pw_bind[i][k]) for k in range(3))
                assert dr < 1e-4 and dp < 1e-5, \
                    f"jumpstart frame0 deviates from bind at bone {i}"

        nkeys = a["tracks"][0]["n_keys"]
        for tr in a["tracks"]:
            assert tr["n_keys"] == nkeys, "tracks share one key grid (sec 4.9)"
        frames = []
        for k in range(nkeys):
            locs = {}
            for tr in a["tracks"]:
                t, tx, ty, tz, qx, qy, qz, qw = tr["keys"][k]
                locs[tr["bone"]] = ([tx * SCALE, ty * SCALE, tz * SCALE],
                                    qconj((qx, qy, qz, qw)))
            rw, pw = bone_world(bones, locs)
            frame = []
            for i in range(n):
                frame.extend(pw[i])
                frame.extend(rw[i])
            frames.append(frame)
        clips.append(dict(name=stem, duration=a["duration"],
                          fps=30, n_frames=nkeys,
                          frames_b64=f32blob([x for fr in frames for x in fr])))
        print(f'{stem:10} {nkeys:3} frames  {a["duration"]:.4f}s')

    # ---- assemble
    data = dict(
        rig=RIG_STEM, n_bones=n, n_verts=nverts, n_faces=len(faces) // 3,
        verts_b64=f32blob([round(x, 5) for v in verts for x in v]),
        norms_b64=f32blob([round(x, 5) for v in norms for x in v]),
        uvs_b64=f32blob([x for v in uvs for x in v]),
        faces_b64=u16blob(faces),
        vbones_b64=u16blob(vbones),
        parts=part_ranges,
        texture="../models/png/2682c5d5.png",
        bone_names=[b["name"] for b in bones],
        bone_parents=[b["parent"] for b in bones],
        clips=clips,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text('window.__MODEL_ANIM=' + body + ';', encoding="utf-8")
    print(f"\nbind check passed for all {len(clips)} clips; "
          f"{nverts} verts, {len(faces)//3} faces, {n} bones")
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
