#!/usr/bin/env python3
"""Validate every decoded .cmz `sbus` skin blob - the regression test.

This is what proves the decode in §4.6 is right rather than merely
self-consistent.  Four independent checks, all of which must hold:

  1. bone index range     every bone index is in [0, n_bones) for that rig
  2. weight sum           every vertex's influence weights sum to 1.0
  3. unit normals         every decoded normal has |n| == 1
  4. index range          every face index is in [0, n_verts)

Checks 2-4 need no external ground truth: they are properties the format
guarantees, so a wrong stride or a misaligned header breaks them immediately.
Check 1 uses the bone counts recovered from the `skel` chunk.

    python sbus_validate.py [--root DIR] [--strict]

Exit code 1 under --strict if anything fails (default: report only).
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from sbus_decode import chunk_bounds, blobs, decode  # noqa: E402

# bone counts from the `skel` chunk of each shipped .cmz
BONES = {"2682c5d5": 43, "482f1708": 109, "5995da06": 119, "5e493068": 119,
         "72b5ee25": 119, "a7b5710e": 119, "d1500c0c": 119, "f2d259a2": 119,
         "ff725ba4": 119}

TOL = 1e-3


def check_file(path):
    """-> list of failure strings (empty means the file is clean)."""
    d = path.read_bytes()
    nb = BONES.get(path.stem)
    mesh = next((b for b in chunk_bounds(d) if b[1] == "MESH"), None)
    if not mesh:
        return [f"{path.stem}: no MESH chunk"]
    fails, rows = [], []
    off, _, end = mesh
    for k, blob in enumerate(blobs(d, off + 8, end)):
        r = decode(blob)
        if r is None:
            fails.append(f"{path.stem} part{k}: decode failed")
            continue
        v = r["verts"]
        nv = r["n_verts"]
        if len(v) != nv:
            fails.append(f"{path.stem} part{k}: {len(v)} verts, header says {nv}")

        bones = [b for q in v for b in q["bones"]]
        if nb is not None:
            bad = [b for b in bones if b >= nb]
            if bad:
                fails.append(f"{path.stem} part{k}: {len(bad)} bone indices "
                             f">= n_bones({nb}), max={max(bad)}")

        wmin = min((sum(q["weights"]) for q in v), default=1.0)
        wmax = max((sum(q["weights"]) for q in v), default=1.0)
        if abs(wmin - 1.0) > TOL or abs(wmax - 1.0) > TOL:
            fails.append(f"{path.stem} part{k}: weight sum "
                         f"[{wmin:.4f},{wmax:.4f}] != 1")

        nl = [(q["nrm"][0] ** 2 + q["nrm"][1] ** 2 + q["nrm"][2] ** 2) ** 0.5
              for q in v]
        if nl and (min(nl) < 1 - TOL or max(nl) > 1 + TOL):
            fails.append(f"{path.stem} part{k}: |normal| "
                         f"[{min(nl):.4f},{max(nl):.4f}] != 1")

        imax = max((max(f) for f in r["faces"]), default=-1)
        imin = min((min(f) for f in r["faces"]), default=0)
        if imax >= nv or imin < 0:
            fails.append(f"{path.stem} part{k}: face index "
                         f"[{imin},{imax}] outside [0,{nv})")
        rows.append((k, nv, len(r["faces"]), len(bones) and max(bones),
                     r["packed_indices"]))
    return fails, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path,
                    default=pathlib.Path("extracted/netpak"))
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any check fails")
    a = ap.parse_args()

    print(f'{"file":10} {"part":>4} {"verts":>6} {"faces":>6} '
          f'{"maxbone":>7}  idx')
    allfails = []
    tot_v = tot_f = 0
    for p in sorted(a.root.rglob("*.cmz")):
        fails, rows = check_file(p)
        allfails += fails
        for k, nv, nf, mb, packed in rows:
            print(f"{p.stem:10} {k:4} {nv:6} {nf:6} "
                  f"{(mb if mb is not None else '-'):>7}  "
                  f"{'10-bit' if packed else 'u16'}")
            tot_v += nv
            tot_f += nf
    print(f"\ntotal: {tot_v} verts, {tot_f} faces")
    if allfails:
        print(f"\n{len(allfails)} FAILURES:")
        for f in allfails:
            print("  " + f)
    else:
        print("\nall checks passed: bone range, weight sum, "
              "unit normals, index range")
    if allfails and a.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
