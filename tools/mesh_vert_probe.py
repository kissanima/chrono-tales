#!/usr/bin/env python3
"""mesh_vert_probe.py - characterise the .53wf VERT stream.

`VERT` is the one chunk in the GBox2 mesh format that does not decode as a
plain vertex buffer.  This script answers three questions with evidence
rather than guesswork:

  1. How many bytes does VERT spend per vertex?
  2. Is VERT a fixed-stride float buffer?
  3. If it is a quantised buffer, does it carry coherent geometry?

Method
------
The chunk chain is walked by scanning for byte-reversed FourCCs.  Chunk
payloads have no explicit length field, so a chunk's extent is the distance to
the next tag.  For each submesh we take the paired INDX (u16 triangle indices)
and ask how `len(VERT) / (max_index + 1)` behaves for several candidate
strides.  A true stride shows up as a near-constant ratio just above 1.0 (the
buffer is usually allocated slightly larger than the referenced range).

Then, for the stride that fits, we test whether the decoded values are real
geometry: for every triangle edge we compare the length in decoded space
against the length between two *randomly chosen* vertices.  Genuine geometry
makes referenced pairs much closer than random ones (ratio well below 1.0).
A ratio near 1.0 means the bytes are noise with respect to the index buffer,
i.e. the payload is encoded and the length is the only structural signal.

Usage:
    python mesh_vert_probe.py [--root DIR]
"""
import argparse
import collections
import math
import pathlib
import random
import statistics
import struct

TAGS = {"MAT3", "MAT2", "MESH", "INDX", "TEUV", "VERT"}
STRIDES = (3, 4, 6, 8, 12, 16, 24, 32)


def rev4(d, i):
    try:
        s = d[i:i + 4][::-1].decode("ascii")
    except UnicodeDecodeError:
        return None
    return s if s in TAGS else None


def chain_of(d):
    """Tag chain, keeping strictly increasing offsets."""
    out = []
    for i in range(len(d) - 4):
        t = rev4(d, i)
        if t and (not out or i >= out[-1][0] + 4):
            out.append((i, t))
    return out


def submeshes(path):
    """Yield (indices, vert_bytes) for every INDX that has a VERT sibling."""
    d = path.read_bytes()
    ch = chain_of(d)
    for k, (off, tag) in enumerate(ch):
        if tag != "INDX":
            continue
        end = ch[k + 1][0] if k + 1 < len(ch) else len(d)
        n = (end - off - 4) // 2
        idx = struct.unpack_from(f"<{n}H", d, off + 4)
        vb = None
        for j in range(k + 1, min(k + 4, len(ch))):
            if ch[j][1] == "VERT":
                ve = ch[j + 1][0] if j + 1 < len(ch) else len(d)
                vb = d[ch[j][0] + 4:ve]
                break
        if vb:
            yield path, idx, vb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path,
                    default=pathlib.Path("extracted/netpak"))
    a = ap.parse_args()

    meshes = sorted(a.root.rglob("*.53wf"))
    if not meshes:
        raise SystemExit(f"no .53wf under {a.root}")

    print(f"{len(meshes)} meshes under {a.root}\n")

    # ---- 1. which stride fits -------------------------------------------
    ratios = collections.defaultdict(list)
    rows = []
    for path in meshes:
        for _, idx, vb in submeshes(path):
            if not idx:
                continue
            mx = max(idx) + 1
            rows.append((path.name, mx, len(vb), len(idx) // 3))
            for s in STRIDES:
                ratios[s].append((len(vb) / s) / mx)

    print("stride  median  min    max    within 1.00-1.25x")
    best = None
    for s in STRIDES:
        r = ratios[s]
        if not r:
            continue
        med = statistics.median(r)
        good = sum(1 for x in r if 1.0 <= x <= 1.25)
        print(f"{s:5}   {med:6.2f}  {min(r):5.2f}  {max(r):5.2f}   {good:3}/{len(r)}")
        if best is None or good > best[1]:
            best = (s, good, med)
    print(f"\n-> best stride {best[0]} bytes/vertex "
          f"({best[1]}/{len(ratios[best[0]])} submeshes within 1.25x)\n")

    # ---- 2. is it a float buffer? ---------------------------------------
    div = {s: sum(1 for _, _, n, _ in rows if n % s == 0) for s in STRIDES}
    print("payload divisible by stride (of %d submeshes):" % len(rows))
    print("   " + "  ".join(f"{s}:{div[s]}" for s in STRIDES))
    print("   a fixed float32 xyz buffer would be 100% at 12\n")

    # ---- 3. does it carry geometry? -------------------------------------
    stride = best[0]
    random.seed(0)
    print(f"geometry control test at stride {stride} "
          f"(referenced edges vs random pairs):")
    print(f"  {'mesh':18} {'verts':>6} {'real med':>9} {'ctrl med':>9} {'ratio':>6}")
    allr = []
    for path, idx, vb in submeshes(pathlib.Path(meshes[0])):
        pass
    for path in meshes:
        for _, idx, vb in submeshes(path):
            if len(vb) % stride:
                continue
            vals = struct.unpack_from(f"<{len(vb)}b", vb, 0)
            pts = [vals[i:i + stride] for i in range(0, len(vals), stride)]
            if len(pts) < 8 or len(idx) < 3:
                continue
            real = []
            for t in range(len(idx) // 3):
                a_, b_, c_ = idx[t * 3:t * 3 + 3]
                if max(a_, b_, c_) < len(pts):
                    real += [math.dist(pts[a_], pts[b_]),
                             math.dist(pts[b_], pts[c_]),
                             math.dist(pts[c_], pts[a_])]
            if not real:
                continue
            ctrl = [math.dist(random.choice(pts), random.choice(pts))
                    for _ in range(len(real))]
            mr, mc = statistics.median(real), statistics.median(ctrl)
            allr.append(mr / mc)
            print(f"  {path.name:18} {len(pts):6} {mr:9.1f} {mc:9.1f} {mr/mc:6.2f}")

    if allr:
        med = statistics.median(allr)
        print(f"\n  median ratio {med:.2f}")

        # Decisive check: are the three byte channels actually distributed like
        # the coordinates of a compact object?  A uniform spread over the whole
        # signed range (std ~= 74, the value for U(-128,127)) means the bytes
        # carry no positional information, whatever the edge-length ratio says.
        print("\nchannel spread (a compact object is far from uniform; "
              "U(-128,127) gives std=74):")
        allstd = []
        for path in meshes:
            for _, _, vb in submeshes(path):
                if len(vb) % stride:
                    continue
                vals = struct.unpack_from(f"<{len(vb)}b", vb, 0)
                ch = [[vals[i] for i in range(c, len(vals), stride)]
                      for c in range(stride)]
                s = [statistics.pstdev(c) for c in ch]
                allstd.extend(s)
                print(f"  {path.name:18} " +
                      "  ".join(f"c{i}={v:5.1f}" for i, v in enumerate(s)))
        if allstd:
            m = statistics.median(allstd)
            print(f"\n  median channel std = {m:.1f}")
            if m > 60:
                print("  -> channels are essentially uniform over the full range:")
                print("     these bytes are NOT positions.  VERT stays undecoded.")
            else:
                print("  -> channels are concentrated, consistent with positions.")


if __name__ == "__main__":
    main()
