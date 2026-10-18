#!/usr/bin/env python3
"""cmz.py - decode Chrono Tales .cmz skeletons.

Container (recovered from the loader at 0x5992FD / 0x5ABB50 / 0x598F80):

    CMZ\\0 <u32 version=102>
    skel   bone hierarchy
    MESH   skinned mesh parts   (u32 n, then n x (name, u32 m, m x 136 B))
    MAP1   attachment name list (u16 n, then length-prefixed GBK names)

Chunk tags are stored byte-reversed (`leks` = skel, `HSEM` = MESH, `1PAM` = MAP1,
`.DNE` = "END."), and the loader accepts exactly those four - everything else in
the file that looks like a tag is a false positive from payload data.

`skel` stream:

    u16 n
    n x {
        u32 name_len ; char[name_len]      GBK, NUL terminated
        f32 x14                            see below
        s16 parent                         -1 = root
        u16 n_children ; u16[n_children]   child indices
    }

The 14 floats are two (position, rotation) pairs:

    [0:3]   local offset, centimetres, along the parent's +X (bone length)
    [3:7]   local rotation, quaternion xyzw, **inverse** of the bone->parent
            rotation
    [7:10]  second offset   (exporter bookkeeping; not needed to pose)
    [10:14] second rotation (same)

The inverse-quaternion detail is not cosmetic: composing the quaternions
directly gives a skeleton whose left and right limbs do not mirror.  Taking the
conjugate makes the mirror error exactly zero, and on the humanoid rigs it
leaves the arm and leg chains straight to within 1-3 degrees - i.e. an A-pose,
which is what a bind pose should look like.

Posing:

    R_world[i] = R_world[parent] * conj(q[i])
    P_world[i] = P_world[parent] + R_world[parent] * offset[i]

Units are centimetres; the engine multiplies by 0.01 (the same constant at VA
0x6D0738 that the .53wf vertex decoder uses).  Axes are **Z up**, X lateral.

Usage:
    python cmz.py [--root DIR] [--out DIR]
"""
import argparse
import json
import math
import pathlib
import struct

MAGIC = b"CMZ\x00"
TAGS = {"skel", "MESH", "MAP1", "END."}
SCALE = 0.01                     # engine constant at VA 0x6D0738


# ---------------------------------------------------------------- container
def walk(d):
    """(offset, tag, end) for each chunk, in file order."""
    hits = []
    for i in range(8, len(d) - 4):
        s = d[i:i + 4][::-1]
        try:
            t = s.decode("latin1")
        except UnicodeDecodeError:
            continue
        if t in TAGS and (not hits or i >= hits[-1][0] + 4):
            hits.append((i, t))
    out = []
    for k, (off, t) in enumerate(hits):
        end = hits[k + 1][0] if k + 1 < len(hits) else len(d)
        out.append((off, t, end))
    return out


def gbk(b):
    return b.split(b"\x00")[0].decode("gbk", "replace")


# -------------------------------------------------------------------- skel
def parse_skel(d, off):
    n = struct.unpack_from("<H", d, off)[0]
    off += 2
    bones = []
    for i in range(n):
        ln = struct.unpack_from("<I", d, off)[0]
        off += 4
        name = gbk(d[off:off + ln])
        off += ln
        fl = struct.unpack_from("<14f", d, off)
        off += 56
        parent = struct.unpack_from("<h", d, off)[0]
        off += 2
        nc = struct.unpack_from("<H", d, off)[0]
        off += 2
        kids = list(struct.unpack_from(f"<{nc}H", d, off)) if nc else []
        off += nc * 2
        bones.append(dict(index=i, name=name, parent=parent, children=kids,
                          offset=fl[0:3], rot=fl[3:7],
                          offset2=fl[7:10], rot2=fl[10:14]))
    return bones, off


def find_sbus(d, start, end):
    """All `sbus` (reversed `subs`) record markers inside [start,end)."""
    out = []
    i = start
    while True:
        j = d.find(b"sbus", i, end)
        if j < 0:
            break
        out.append(j)
        i = j + 1
    return out


def parse_mesh(d, off, end):
    """MESH chunk = an array of n_parts skinned parts.

    Serialized layout (recovered from the loader at 0x199307 / 0x19a2b0):
      u32 n_parts
      [per part] either:
         u32 name_len (GBK, NUL term) + name + u32 record_count + sbus blob
      or, for index-only parts (e.g. the mech's wheels):
         sbus blob directly
    Each `sbus` blob begins with the 4-byte tag `sbus`, then a 32-byte header:
      u32 part_index | u32 0 | u32 A | u32 B | u32 0 | u32 0 | u32 bonecap
    (bonecap = 256 for mounts/mechs, 512 for humans = the skeleton's max-bone
    class).  The rest of the blob is the per-vertex skin data.  The in-memory
    part struct is 136 bytes (stride confirmed by indexer 0x19a2b0 = idx*136),
    but the *file* blob is variable length, so we segment by the `sbus` tags.
    """
    if off + 4 > end:
        return [], False
    n = struct.unpack_from("<I", d, off)[0]
    off += 4
    if n > 4096 or n == 0:
        return [], False
    tags = find_sbus(d, off, end)
    if len(tags) < n:
        # fall back: maybe n counts something else; take what we have
        n = max(n, len(tags))
    # collect length-prefixed GBK names anywhere in the chunk (u32 len)
    names = []
    i = off
    while i + 4 <= end:
        ln = struct.unpack_from("<I", d, i)[0]
        if 1 <= ln <= 64 and i + 4 + ln <= end:
            raw = d[i + 4:i + 4 + ln]
            if all((c == 0 or 32 <= c < 0x80 or c >= 0x81) for c in raw):
                try:
                    s = raw.decode("gbk")
                    if any(0x80 <= ord(c) or ("a" <= c.lower() <= "z") for c in s):
                        names.append((i, s))
                except UnicodeDecodeError:
                    pass
        i += 1
    parts = []
    for k in range(min(n, max(n, len(tags)))):
        t = tags[k] if k < len(tags) else None
        nxt = tags[k + 1] if k + 1 < len(tags) else end
        if t is None:
            break
        blob = d[t:nxt]
        # header: 4-byte tag + 7 u32
        hdr = struct.unpack_from("<7I", blob, 4) if len(blob) >= 32 else (0,) * 7
        part_index, _, A, B, _, _, bonecap = (hdr + (0,) * 7)[:7]
        parts.append(dict(name=None, part_index=part_index, A=A, B=B,
                          bonecap=bonecap, blob_len=len(blob),
                          blob_off=t - off))
    # assign discovered names to the parts that sit just after them
    for ni, (no, s) in enumerate(names):
        # the part whose blob starts right after this name position
        for p in parts:
            if p["blob_off"] and no < off + p["blob_off"] <= no + 4 + len(s) + 8:
                if p["name"] is None:
                    p["name"] = s
                    break
    if not parts:
        return parts, False
    return parts, True


def parse_map1(d, off, end):
    """u16 n, then length-prefixed GBK names with a small flag block each."""
    names = []
    p = off + 2
    while p + 4 <= end:
        ln = struct.unpack_from("<I", d, p)[0]
        if ln == 0 or ln > 200 or p + 4 + ln > end:
            break
        raw = d[p + 4:p + 4 + ln]
        if not all(c == 0 or 32 <= c < 0x80 or c >= 0x81 for c in raw):
            break
        names.append(gbk(raw))
        p += 4 + ln
        while p + 4 <= end and d[p:p + 4] == b"\x00\x00\x00\x00":
            p += 4
        if p + 1 < end and d[p] in (0, 1):
            p += 1
        if p + 1 < end and d[p] in (0, 1):
            p += 1
    return names


def write_skeleton_obj(info, path):
    """Octahedral bone shapes, Z up, metres."""
    world = info["world"]
    if not world:
        return False
    out = [f'# {info["name"]}.cmz skeleton  ({len(info["bones"])} bones)',
           '# bind pose from conj(q) accumulation, Z up, metres']
    nv = 0
    faces = []
    for b in info["bones"]:
        if b["parent"] < 0:
            continue
        h = world[b["parent"]]
        t = world[b["index"]]
        d = [t[k] - h[k] for k in range(3)]
        L = math.sqrt(sum(c * c for c in d))
        if L < 1e-6:
            continue
        d = [c / L for c in d]
        # a stable perpendicular
        up = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
        u = [d[1] * up[2] - d[2] * up[1],
             d[2] * up[0] - d[0] * up[2],
             d[0] * up[1] - d[1] * up[0]]
        ul = math.sqrt(sum(c * c for c in u)) or 1.0
        u = [c / ul for c in u]
        v = [d[1] * u[2] - d[2] * u[1],
             d[2] * u[0] - d[0] * u[2],
             d[0] * u[1] - d[1] * u[0]]
        r = max(L * 0.11, 0.004)
        mid = [h[k] + d[k] * L * 0.18 for k in range(3)]
        ring = [[mid[k] + u[k] * r for k in range(3)],
                [mid[k] + v[k] * r for k in range(3)],
                [mid[k] - u[k] * r for k in range(3)],
                [mid[k] - v[k] * r for k in range(3)]]
        out.append(f'g {info["name"]}_{b["index"]}_{b["name"].replace(" ", "_")}')
        for p in [h] + ring + [t]:
            out.append('v %.5f %.5f %.5f' % tuple(p))
        a = nv + 1
        for k in range(4):
            k2 = (k + 1) % 4
            faces.append((a, a + 1 + k, a + 1 + k2))          # head cap
            faces.append((a + 5, a + 1 + k2, a + 1 + k))      # tail cap
        nv += 6
    for f in faces:
        out.append('f %d %d %d' % f)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True


# ------------------------------------------------------------------- maths
def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def qrot(q, v):
    x, y, z, w = q
    cx = y * v[2] - z * v[1]
    cy = z * v[0] - x * v[2]
    cz = x * v[1] - y * v[0]
    ccx = y * cz - z * cy
    ccy = z * cx - x * cz
    ccz = x * cy - y * cx
    return (v[0] + 2 * (w * cx + ccx),
            v[1] + 2 * (w * cy + ccy),
            v[2] + 2 * (w * cz + ccz))


def pose(bones):
    """World rotation and position per bone, in metres, Z up."""
    n = len(bones)
    rw = [None] * n
    pw = [None] * n
    for b in bones:
        i = b["index"]
        q = qconj(b["rot"])
        if b["parent"] < 0:
            rw[i] = q
            pw[i] = [c * SCALE for c in b["offset"]]
        else:
            p = b["parent"]
            rw[i] = qmul(rw[p], q)
            r = qrot(rw[p], b["offset"])
            pw[i] = [pw[p][k] + r[k] * SCALE for k in range(3)]
    return rw, pw


def load(path):
    d = path.read_bytes()
    if d[:4] != MAGIC:
        return None
    info = dict(path=str(path), name=path.stem, size=len(d),
                version=struct.unpack_from("<I", d, 4)[0],
                chunks=[], bones=[], mesh=[], map1=[], mesh_complete=False)
    for off, tag, end in walk(d):
        info["chunks"].append(dict(tag=tag, offset=off, size=end - off - 4))
        if tag == "skel":
            info["bones"], _ = parse_skel(d, off + 4)
        elif tag == "MESH":
            info["mesh"], info["mesh_complete"] = parse_mesh(d, off + 4, end)
        elif tag == "MAP1":
            info["map1"] = parse_map1(d, off + 4, end)
    info["rw"], info["world"] = pose(info["bones"]) if info["bones"] else ([], [])
    return info


def stats(info):
    w = info["world"]
    if not w:
        return {}
    xs = [p[0] for p in w]
    ys = [p[1] for p in w]
    zs = [p[2] for p in w]
    roots = [b["name"] for b in info["bones"] if b["parent"] < 0]
    return dict(bones=len(info["bones"]),
                roots=roots,
                extent=[max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)],
                height=zs and (max(zs) - min(zs)),
                z_range=[min(zs), max(zs)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path,
                    default=pathlib.Path("extracted/netpak"))
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path("extracted/models/cmz"))
    ap.add_argument("--obj", type=pathlib.Path,
                    default=pathlib.Path("extracted/models/skel_obj"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    a.obj.mkdir(parents=True, exist_ok=True)

    print(f'{"file":10} {"bones":>5} {"parts":>5} {"names":>5} {"chunks":22} '
          f'{"height (m)":>10}  root')
    for p in sorted(a.root.rglob("*.cmz")):
        info = load(p)
        if not info:
            continue
        st = stats(info)
        seq = " ".join(c["tag"] for c in info["chunks"])
        h = f'{st.get("height", 0):.3f}' if st else "-"
        print(f'{p.stem:10} {len(info["bones"]):5} {len(info["mesh"]):5} '
              f'{len(info["map1"]):5} {seq:22} {h:>10}  {",".join(st.get("roots", []))}')
        (a.out / (p.stem + ".json")).write_text(json.dumps(info, ensure_ascii=False,
                                                           indent=1), encoding="utf-8")
        write_skeleton_obj(info, a.obj / (p.stem + ".obj"))
    print(f'\nwrote {len(list(a.out.glob("*.json")))} JSON + '
          f'{len(list(a.obj.glob("*.obj")))} OBJ files')


if __name__ == "__main__":
    main()
