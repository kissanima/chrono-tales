#!/usr/bin/env python3
"""caf_validate.py - independent checks that the .caf decode in §4.9 is right.

Eight checks, none of which merely re-read the decoder's own assumptions.
Six are properties the *format* guarantees, so a wrong stride, a swapped
field or a misaligned header breaks them immediately:

  1. stream consumption     the last key of the last track ends on the last
                            byte of the file - no slack, no padding
  2. version / duration     version in 699..1100, duration > 0, and the last
                            key time equals the header duration to f32
                            precision (Cal3D stores duration as its own field
                            *and* as the final key time; if +8 were something
                            else the two would not agree)
  3. uniform key grid       every track has the same n_keys and the same
                            per-key times, and the step is 1/30 to within f32
                            precision - this is the condition the loader
                            enforces itself at 0x5AC0E4 with the assert
                            "loadCoreAnimation track delay err"
  4. monotonic times        keyframe times strictly increase inside a track
  5. unit quaternions       |q| == 1 for every keyframe rotation
  6. bone ids               every bone id is in [0, n_bones) for the rig the
                            clip binds to, and no bone id repeats
  7. track counts           n_tracks <= n_bones, and n_tracks == the rig's
                            animated-bone count where the two can be compared
  8. static tracks == bind   a track whose motion across the clip is below
                            1e-3 must equal the `.cmz` bind pose for that
                            bone.  The rig is an independent source, so this
                            is a real assertion, and it catches exactly the
                            semantic errors the rest of the validator cannot:
                            a doubled translation or a swapped quaternion
                            component on a non-animating bone.

**Scope limit - read this before trusting a pass.**  Checks 1-8 validate
*structure and rig consistency*; that is what can be falsified from outside
the file.  They do **not** validate decoded semantics for bones a clip
actually animates: there is no ground truth for a moving bone, so a wrong
quaternion convention or a swapped translation axis applied to an animated
track would pass.  Layout correctness therefore rests on
`caf_decode.py check`, whose byte-exact consumption cannot survive a wrong
stride or a wrong field role.  A bound-reachability check was tried and
**deliberately removed**: `jumping.caf` rotates the rig 21.23 deg about +X,
which swings 2.05 m of +Y bone length into +Z and legitimately lifts the
bounding box from 1.565 m to 2.548 m, so a height bound measures the pose
rather than the decode.

Verified by injection - each of these produces exit 1: shifting one key by
4 ms, forcing a bone id to 999, scaling a quaternion 1.5x, duplicating a bone
id, doubling a static bone's translation, and swapping qy/qz on a static bone.
Scaling the *root* translation is correctly not a failure: a root's
translation is the character's world motion and has no bind-pose constraint.

    python caf_validate.py [--strict] [--zip PATH] [--cmz DIR]

Exit code 1 under --strict if anything fails (default: report only).
"""
import argparse
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from caf_decode import (CAF_ZIP, CafError, decode, iter_clips,  # noqa: E402
                        load_rigs, resolve_rig)

TOL_Q = 1e-4            # quaternion norm, stored as f32
TOL_T = 1e-5            # duration vs final key time
STEP = 1.0 / 30.0       # the only key step in the shipped clips
TOL_STEP = 1e-5
TOL_WORLD_Q = 1e-3
TOL_BIND = 1e-5         # replayed pose vs the .cmz bind pose
AMP_STATIC = 1e-3       # a track moving less than this is "not animated"
TOL_STATIC = 1e-3       # ... and must then sit on the rig's bind pose


# ------------------------------------------------------------------- helpers
def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def qnorm(q):
    return math.sqrt(sum(c * c for c in q))


def qrot(q, v):
    x, y, z, w = q
    cx = y * v[2] - z * v[1]
    cy = z * v[0] - x * v[2]
    cz = x * v[1] - y * v[0]
    return (v[0] + 2 * (w * cx + y * cz - z * cy),
            v[1] + 2 * (w * cy + z * cx - x * cz),
            v[2] + 2 * (w * cz + x * cy - y * cx))


def replay(anim, bones, key_index):
    """World pose at one key index, using the §4.4 bind-pose recurrence.

    Every track shares one key grid (checked separately), so key `k` across
    tracks is one instant of the clip.  Bones with no track keep the bind
    pose.
    """
    local = []
    for b in bones:
        local.append([b["offset"], b["rot"]])
    for tr in anim["tracks"]:
        local[tr["bone"]] = [tr["keys"][key_index][1:4],
                             tr["keys"][key_index][4:8]]
    n = len(bones)
    rw = [None] * n
    pw = [None] * n
    for b in bones:
        i = b["index"]
        q = qconj(tuple(local[i][1]))
        if b["parent"] < 0:
            rw[i] = q
            pw[i] = [c * 0.01 for c in local[i][0]]
        else:
            p = b["parent"]
            rw[i] = qmul(rw[p], q)
            r = qrot(rw[p], local[i][0])
            pw[i] = [pw[p][k] + r[k] * 0.01 for k in range(3)]
    return rw, pw


def load_worlds(root="extracted/models/cmz"):
    """{stem: world positions} as `cmz.py` already posed them."""
    out = {}
    for p in sorted(pathlib.Path(root).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(d, dict) and d.get("world") and d.get("bones"):
            if len(d["world"]) == len(d["bones"]):
                out[p.stem] = d["world"]
    return out


# -------------------------------------------------------------------- checks
def check_clip(name, data, rigs, worlds):
    fails = []
    try:
        a = decode(data, name)
    except CafError as e:
        return [str(e)], None

    # 1 - consumption
    if a["end"] != len(data):
        fails.append(f'{name}: {len(data) - a["end"]} slack bytes after the '
                     f'last key (consumed {a["end"]}/{len(data)})')

    # 2 - version / duration agreement
    last_key_times = {tr["keys"][-1][0] for tr in a["tracks"]}
    if len(last_key_times) != 1:
        fails.append(f"{name}: tracks disagree on the final key time")
    else:
        lt = last_key_times.pop()
        if abs(lt - a["duration"]) > TOL_T:
            fails.append(f'{name}: header duration {a["duration"]:.9f} != final '
                         f'key time {lt:.9f}')

    # 3 - uniform key grid
    n_keys = {tr["n_keys"] for tr in a["tracks"]}
    if len(n_keys) != 1:
        fails.append(f"{name}: tracks have differing key counts {sorted(n_keys)}")
    ref = a["tracks"][0]["keys"]
    for tr in a["tracks"][1:]:
        for k, (kk, rk) in enumerate(zip(tr["keys"], ref)):
            if kk[0] != rk[0]:
                fails.append(f'{name}: track bone {tr["bone"]} key {k} time '
                             f'{kk[0]:.9f} != {rk[0]:.9f}')
                break
    steps = [ref[k + 1][0] - ref[k][0] for k in range(len(ref) - 1)]
    bad = [s for s in steps if abs(s - STEP) > TOL_STEP]
    if bad:
        fails.append(f"{name}: key step {bad[0]:.7f} != 1/30 "
                     f"({len(bad)} steps off)")

    # 4 - monotonic times
    for tr in a["tracks"]:
        ts = [k[0] for k in tr["keys"]]
        if any(ts[i + 1] <= ts[i] for i in range(len(ts) - 1)):
            fails.append(f'{name}: track bone {tr["bone"]} times not '
                         f"increasing")
            break
        if ts[0] < 0 or ts[-1] > a["duration"] + TOL_T:
            fails.append(f'{name}: track bone {tr["bone"]} key times '
                         f"[{ts[0]:.4f},{ts[-1]:.4f}] outside "
                         f'[0,{a["duration"]:.4f}]')
            break

    # 5 - unit quaternions
    worst = 0.0
    worst_at = None
    for tr in a["tracks"]:
        for k in tr["keys"]:
            e = abs(qnorm(k[4:8]) - 1.0)
            if e > worst:
                worst, worst_at = e, (tr["bone"], k[0])
    if worst > TOL_Q:
        fails.append(f"{name}: |q| error {worst:.2e} at bone {worst_at[0]} "
                     f"t={worst_at[1]:.4f}")

    # 6 / 7 - bone ids and track counts
    rig, hit, compared = resolve_rig(a, rigs)
    cmz_world = worlds.get(rig) if rig else None
    ids = [tr["bone"] for tr in a["tracks"]]
    if len(set(ids)) != len(ids):
        dup = sorted({b for b in ids if ids.count(b) > 1})
        fails.append(f"{name}: duplicate bone ids in the track table: {dup[:8]}")
    if rig is None:
        fails.append(f"{name}: no rig holds bone id {max(ids)} - the id range "
                     f"is outside every shipped skeleton")
    bones = rigs.get(rig) if rig else None
    if bones:
        nb = len(bones)
        if a["n_tracks"] != nb:
            fails.append(f"{name}: n_tracks {a['n_tracks']} != rig {rig} "
                         f"bone count {nb}")
        oob = [b for b in ids if not 0 <= b < nb]
        if oob:
            fails.append(f"{name}: bone ids out of [0,{nb}): {oob[:8]}")
    else:
        # still bound the ids by the largest shipped skeleton
        nb = max((len(v) for v in rigs.values()), default=0)
        oob = [b for b in ids if not 0 <= b < nb]
        if oob and not any("no rig holds" in f for f in fails):
            fails.append(f"{name}: bone ids out of [0,{nb}): {oob[:8]}")

    # 8 - a track that does not animate must sit on the rig's bind pose
    #
    # This is the one assertion with an external ground truth.  The clip's own
    # translations cannot testify about themselves, but the .cmz `skel` chunk
    # is independent: for a bone the clip leaves alone, key 0 must be the bind
    # offset and rotation.  Across the five shipped clips the non-animated
    # tracks land within 1.6e-05 of bind, while an injection moves them by
    # 6.3e+01 (doubled translation) and 2.6e-01 (swapped qy/qz).
    bind_err = None
    n_static = 0
    if bones and cmz_world and a["tracks"][0]["n_keys"] > 1:
        for tr in a["tracks"]:
            ks = tr["keys"]
            amp = max(max(max(abs(k[1 + j] - ks[0][1 + j]) for j in range(3)),
                          max(abs(k[4 + j] - ks[0][4 + j]) for j in range(4)))
                      for k in ks)
            if amp > AMP_STATIC:
                continue
            n_static += 1
            b = bones[tr["bone"]]
            t = ks[0]
            e = max(max(abs(t[1 + j] - b["offset"][j]) for j in range(3)),
                    max(abs(t[4 + j] - b["rot"][j]) for j in range(4)))
            if e > TOL_STATIC:
                fails.append(f'{name}: bone {b["index"]} ({b["name"]}) does not '
                             f'animate but is {e:.2e} off the bind pose - the '
                             f'rig is the independent reference here')
        # also keep the replay's unit world quaternions / NaN as a belt
        for ki in (0, a["tracks"][0]["n_keys"] - 1):
            rw, pw = replay(a, bones, ki)
            badq = [i for i, q in enumerate(rw)
                    if abs(qnorm(q) - 1.0) > TOL_WORLD_Q]
            if badq:
                fails.append(f"{name}: {len(badq)} world quaternions not unit "
                             f"length at key {ki} (first bone {badq[0]})")
            if any(math.isnan(c) for p in pw for c in p):
                fails.append(f"{name}: NaN in the replayed pose at key {ki}")
        rw, pw = replay(a, bones, 0)
        bind_err = max(math.dist(pw[i], cmz_world[i]) for i in range(len(bones)))
        if hit == compared and bind_err > TOL_BIND:
            # every key 0 already matches bind, so the replay must land on it
            fails.append(f"{name}: key 0 equals the bind pose on all "
                         f"{compared} bones but the replay is "
                         f"{bind_err:.2e} m off")

    stats = dict(clip=pathlib.Path(name).stem, duration=a["duration"],
                 n_tracks=a["n_tracks"],
                 n_keys=sum(tr["n_keys"] for tr in a["tracks"]),
                 keys_per_track=a["tracks"][0]["n_keys"],
                 rig=rig, bind_match=f"{hit}/{compared}",
                 quat_err=worst, n_static=n_static,
                 pose_vs_bind=None if bind_err is None else bind_err)
    return fails, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=CAF_ZIP)
    ap.add_argument("--root", default=None)
    ap.add_argument("--cmz", default="extracted/models/cmz")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rigs = load_rigs(a.cmz)
    if not rigs:
        print(f"no skeletons found in {a.cmz} - run tools/cmz.py first")
        return 1
    worlds = load_worlds(a.cmz)
    print(f"{len(rigs)} rigs from {a.cmz}: "
          f'{", ".join(f"{k}({len(v)})" for k, v in sorted(rigs.items()))}')

    all_fails, rows = [], []
    for name, data in iter_clips(a.zip, a.root):
        fails, stats = check_clip(name, data, rigs, worlds)
        all_fails += fails
        if stats:
            rows.append(stats)

    print(f'\n{"clip":10} {"dur":>8} {"tracks":>6} {"n_keys":>6} {"per":>4} '
          f'{"rig":>9} {"bind":>7} {"static":>6} {"|q|-1":>9} {"vs bind":>9}')
    for r in rows:
        vb = "-" if r["pose_vs_bind"] is None else f'{r["pose_vs_bind"]:.2e}'
        print(f'{r["clip"]:10} {r["duration"]:8.4f} {r["n_tracks"]:6} '
              f'{r["n_keys"]:6} {r["keys_per_track"]:4} {str(r["rig"]):>9} '
              f'{r["bind_match"]:>7} {r["n_static"]:6} '
              f'{r["quat_err"]:9.2e} {vb:>9}')

    if a.json:
        print(json.dumps(dict(rows=rows, failures=all_fails), indent=1))

    if all_fails:
        print(f"\n{len(all_fails)} FAILURE(S):")
        for f in all_fails:
            print("  -", f)
        return 1
    print(f"\nall checks passed on {len(rows)} clips")
    return 0


if __name__ == "__main__":
    sys.exit(main())
