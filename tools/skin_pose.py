#!/usr/bin/env python3
"""Pose the assembled character by re-skinning the bind-pose mesh.

For every shipped part, the per-vertex skin is **rigid** (exactly one bone
per vertex), so the skinning transform is:

    v_local = R_bind^-1 * (v_world - p_bind)
    v_posed = p_posed + R_posed * v_local

A pose is a list of (bone_name_substring, local_axis, degrees).  The local
axis is the axis in the bone's own bind frame (its +X is the bone length,
so rotating about +X is an axial twist that doesn't move children; +Z is
roughly the skeleton's up, so a rotation about +Z swings the children in
the bone's local XY plane).

Predefined poses:
    bind       no change
    head       turn the head 30 deg about its local +Z
    arms_out   raise both arms 90 deg about the upper-arm local +Z
    squat      tilt the thighs + calves + feet 15 deg forward

Usage:
    python skin_pose.py [--out OBJ] [--with-hair] [--pose NAME]
"""
import argparse
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from assemble_char import HUMAN, HAIR, MECH, ROOT, load_parts  # noqa: E402
import cmz  # noqa: E402
from sbus_decode import chunk_bounds  # noqa: E402


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def qfromaxis(axis, angle):
    h = angle * 0.5
    s = math.sin(h)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


def rotmat(q):
    x, y, z, w = q
    return ((1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * z * w, 2 * x * z + 2 * y * w),
            (2 * x * y + 2 * z * w, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * x * w),
            (2 * x * z - 2 * y * w, 2 * y * z + 2 * x * w, 1 - 2 * x * x - 2 * y * y))


def applyrot(R, v):
    return (R[0][0] * v[0] + R[0][1] * v[1] + R[0][2] * v[2],
            R[1][0] * v[0] + R[1][1] * v[1] + R[1][2] * v[2],
            R[2][0] * v[0] + R[2][1] * v[1] + R[2][2] * v[2])


PRESETS = {
    "bind": [],
    "head": [("head", (0, 0, 1), 30)],
    "arms_out": [("upperarm", (0, 0, 1), 90)],
    "squat": [("thigh", (0, 0, 1), 10),
              ("calf", (0, 0, 1), 15)],
}


def make_deltas(skel, preset):
    """Resolve preset entries to {bone_index: delta_quat}.

    Each preset entry's name substring is matched against every bone name
    (case-insensitive); a single delta quat is applied to **all** matches.
    This is how `arms_out` raises both arms at once.
    """
    deltas = {}
    for namepart, axis, deg in PRESETS[preset]:
        dq = qfromaxis(axis, deg * math.pi / 180)
        for b in skel["bones"]:
            if namepart in b["name"].lower():
                deltas[b["index"]] = dq
    return deltas


def pose_with(skel, deltas):
    n = len(skel["bones"])
    new_rw = [None] * n
    new_pw = [None] * n
    for b in skel["bones"]:
        i = b["index"]
        original_local = qconj(b["rot"])
        dq = deltas.get(i, (0, 0, 0, 1))
        new_local = qmul(original_local, dq) if dq != (0, 0, 0, 1) else original_local
        if b["parent"] < 0:
            new_rw[i] = new_local
            new_pw[i] = [c * cmz.SCALE for c in b["offset"]]
        else:
            p = b["parent"]
            new_rw[i] = qmul(new_rw[p], new_local)
            r = applyrot(rotmat(new_rw[p]), b["offset"])
            new_pw[i] = [new_pw[p][k] + r[k] * cmz.SCALE for k in range(3)]
    return new_rw, new_pw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path("extracted/models/character_posed.obj"))
    ap.add_argument("--with-hair", action="store_true")
    ap.add_argument("--pose", default="bind", choices=PRESETS)
    a = ap.parse_args()

    stems = HUMAN + ([HAIR] if a.with_hair else [])
    ref = cmz.load(ROOT / "unknown" / (HUMAN[0] + ".cmz"))
    deltas = make_deltas(ref, a.pose)
    new_rw, new_pw = pose_with(ref, deltas)

    lines = ["# Posed character - " + a.pose,
             "# skinning: v_local = R_bind^-1 * (v - p_bind), v_posed = p_posed + R_posed * v_local"]
    nv = 1
    for stem in stems:
        sk = cmz.load(ROOT / "unknown" / (stem + ".cmz"))
        # BIND transform always comes from this file's own skeleton; the
        # POSED transform only applies when it shares the 119-bone rig.
        rw_bind, pw_bind = sk["rw"], sk["world"]
        same_rig = len(sk["bones"]) == len(ref["bones"])
        rw_pose, pw_pose = (new_rw, new_pw) if same_rig else (rw_bind, pw_bind)
        for p in load_parts(stem):
            lines.append(f"g {stem}_{p['name']}")
            for v in p["verts"]:
                bv = v["bones"][0]
                Rb = rotmat(rw_bind[bv])
                p_bind = pw_bind[bv]
                vx, vy, vz = v["pos"]
                # Rb^T
                lx = Rb[0][0] * (vx - p_bind[0]) + Rb[1][0] * (vy - p_bind[1]) + Rb[2][0] * (vz - p_bind[2])
                ly = Rb[0][1] * (vx - p_bind[0]) + Rb[1][1] * (vy - p_bind[1]) + Rb[2][1] * (vz - p_bind[2])
                lz = Rb[0][2] * (vx - p_bind[0]) + Rb[1][2] * (vy - p_bind[1]) + Rb[2][2] * (vz - p_bind[2])
                Rnp = rotmat(rw_pose[bv])
                pnp = pw_pose[bv]
                px = pnp[0] + Rnp[0][0] * lx + Rnp[0][1] * ly + Rnp[0][2] * lz
                py = pnp[1] + Rnp[1][0] * lx + Rnp[1][1] * ly + Rnp[1][2] * lz
                pz = pnp[2] + Rnp[2][0] * lx + Rnp[2][1] * ly + Rnp[2][2] * lz
                lines.append("v %.5f %.5f %.5f" % (px, py, pz))
                lines.append("vt %.6f %.6f" % tuple(v["uv"] or (0, 0)))
                lines.append("vn %.4f %.4f %.4f" % tuple(v["nrm"]))
            for f in p["faces"]:
                a1, b1, c1 = f[0] + nv, f[1] + nv, f[2] + nv
                lines.append(f"f {a1}/{a1}/{a1} {b1}/{b1}/{b1} {c1}/{c1}/{c1}")
            nv += len(p["verts"])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"pose={a.pose}  deltas={len(deltas)} bones  wrote {a.out}")


if __name__ == "__main__":
    main()
