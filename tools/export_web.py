#!/usr/bin/env python3
"""export_web.py - write the decoded world and the player to glTF 2.0 + three.js.

Everything here is derived, nothing is guessed: the terrain is the solved
MAP_HEIGHTDATA grid, the ground is `Scene.bake_ground()` (the real six-layer
weighted blend), the props are the `.fw3` meshes with their 18-field
MAP_MODELDATA transforms, and the character is the shipped 43-bone Biped
retargeted onto the 119-bone body rig the equipment pieces are skinned to.

Coordinate conversion.  The game is Z up (+Z up, +X east, +Y north); glTF is
+Y up.  The map is a pure rotation about X by -90 degrees,

    M(x, y, z) = (x, z, -y)          det M = +1

so it never mirrors the model.  Vectors and points are mapped directly; a
quaternion q becomes qM * q * qM^-1 with qM the quaternion of M, because a
rotation's axis rotates with the frame and its angle does not change.

Retargeting.  The five shipped `.caf` clips (stand01 / run / jumpstart /
jumping / jumpdown) bind the 43-bone rig `2682c5d5`; the wearable body parts
are skinned to a 119-bone rig whose Biped bones carry the *same names*.  Every
real bone matches, so a clip drives the body bone of the same name and keeps
its own bind translation -- rotations carry the pose, the target rig keeps its
proportions.  The twelve clip bones that do not exist on the body are the
bike's own (penqi* = jet nozzles, *Lun = wheels, single-letter helpers).

Output (extracted/web/):
    scene.gltf / scene.bin / tex/...   the stitched 768 x 512 m world
    actor.gltf / actor.bin             skinned character + 5 animations
    height.js                          world height sampler for the browser
    index.html / main.js               third-person client
"""
import base64
import json
import math
import pathlib
import shutil
import struct
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import render_scene3d as R  # noqa: E402
import cmz  # noqa: E402

OUT = ROOT / "extracted" / "web"
TEX = OUT / "tex"

# the body rig the wearable parts are skinned to, and the clips' rig
BODY_RIG = "5995da06"
CLIP_RIG = "2682c5d5"
BODY_PARTS = [                      # (skin key, part name) -- one rig
    "人类_男_战士7档_衣服_M",
    "人类_男_战士7档_护腿_M",
    "人类_男_战士7档_护臂_M",
    "人类_男_战士7档_手套_M",
    "人类_男_战士7档_鞋子_M",
    "人类_男_脸型09_M",
]
HAIR_RIG = "482f1708"
HAIR_PART = "b_faxing00"

CLIPS = ["stand01", "run", "jumpstart", "jumping", "jumpdown"]

# quaternion of M: rotation about X by -90 deg  ->  (x,y,z,w)
QM = (-0.7071067811865476, 0.0, 0.0, 0.7071067811865476)


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def qmap(q):
    """Rotate a quaternion's axis into the glTF frame."""
    return qmul(qmul(QM, q), qconj(QM))


def vmap(v):
    """(x, y, z) game -> (x, z, -y) glTF."""
    return (v[0], v[2], -v[1])


# ---------------------------------------------------------------- glTF writer
class Gltf:
    """Minimal glTF 2.0 writer: one bin buffer, external images."""

    def __init__(self, name):
        self.name = name
        self.bin = bytearray()
        self.g = {
            "asset": {"version": "2.0",
                      "generator": "export_web.py (Chrono Tales RE)"},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [], "meshes": [], "materials": [], "textures": [],
            "images": [], "samplers": [], "accessors": [], "bufferViews": [],
            "skins": [], "animations": [],
        }

    # -- buffer plumbing
    def _bv(self, data, target):
        off = (len(self.bin) + 3) & ~3
        self.bin.extend(b"\0" * (off - len(self.bin)))
        self.bin.extend(data)
        self.g["bufferViews"].append(
            {"buffer": 0, "byteOffset": off, "byteLength": len(data),
             "target": target})
        return len(self.g["bufferViews"]) - 1

    def _acc(self, data, comp, ctype, count, mn=None, mx=None):
        """comp = glTF componentType code, ctype = 'SCALAR'/'VEC3'/..."""
        off = (len(self.bin) + 3) & ~3
        self.bin.extend(b"\0" * (off - len(self.bin)))
        self.bin.extend(data)
        self.g["bufferViews"].append(
            {"buffer": 0, "byteOffset": off, "byteLength": len(data)})
        a = {"bufferView": len(self.g["bufferViews"]) - 1,
             "componentType": comp, "count": count, "type": ctype}
        if mn is not None:
            a["min"] = mn
        if mx is not None:
            a["max"] = mx
        self.g["accessors"].append(a)
        return len(self.g["accessors"]) - 1

    def f32(self, arr, ctype, count, mn=None, mx=None):
        d = np.asarray(arr, dtype="<f4").tobytes()
        return self._acc(d, 5126, ctype, count, mn, mx)   # FLOAT

    def u16(self, arr, count):
        d = np.asarray(arr, dtype="<u2").tobytes()
        return self._acc(d, 5123, "SCALAR", count)        # UNSIGNED_SHORT

    def ubyte(self, arr, count, ctype):
        d = np.asarray(arr, dtype=np.uint8).tobytes()
        return self._acc(d, 5121, ctype, count)           # UNSIGNED_BYTE

    # -- materials / textures
    def texture(self, src):
        """Copy an image into tex/ and return a material texture index."""
        src = pathlib.Path(src)
        TEX.mkdir(parents=True, exist_ok=True)
        dst = TEX / src.name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copyfile(src, dst)
        self.g["images"].append({"uri": "tex/" + src.name})
        ii = len(self.g["images"]) - 1
        self.g["samplers"].append(
            {"magFilter": 9729, "minFilter": 9987, "wrapS": 10497,
             "wrapT": 10497})
        si = len(self.g["samplers"]) - 1
        self.g["textures"].append({"sampler": si, "source": ii})
        return len(self.g["textures"]) - 1

    def material(self, src, alpha=None, double=True):
        ti = self.texture(src)
        m = {"name": pathlib.Path(src).stem,
             "pbrMetallicRoughness": {"baseColorTexture": {"index": ti},
                                      "metallicFactor": 0.0,
                                      "roughnessFactor": 1.0}}
        if alpha:
            m["alphaMode"] = alpha
            m["alphaCutoff"] = 0.5
        if double:
            m["doubleSided"] = True
        self.g["materials"].append(m)
        return len(self.g["materials"]) - 1

    # -- meshes / nodes
    def mesh(self, prims, name):
        self.g["meshes"].append({"name": name, "primitives": prims})
        return len(self.g["meshes"]) - 1

    def node(self, **kw):
        self.g["nodes"].append(kw)
        return len(self.g["nodes"]) - 1

    def write(self, path):
        self.g["buffers"] = [{"byteLength": len(self.bin)}]
        path.write_text(json.dumps(self.g, indent=1), encoding="utf-8")
        pathlib.Path(str(path).replace(".gltf", ".bin")).write_bytes(
            bytes(self.bin))
        print(f"  {path.name}: {len(self.g['nodes'])} nodes, "
              f"{len(self.g['meshes'])} meshes, {len(self.g['materials'])} "
              f"materials, {len(self.g['accessors'])} accessors, "
              f"{len(self.bin) / 1e6:.1f} MB bin")


def prim(g, pos, nrm, uv, idx, mat, joints=None, weights=None):
    """One primitive: POSITION (+NORMAL, TEXCOORD_0, indices, material)."""
    p = {"attributes": {
            "POSITION": g.f32(pos, "VEC3", len(pos),
                              [round(min(c[i] for c in pos), 4) for i in range(3)],
                              [round(max(c[i] for c in pos), 4) for i in range(3)]),
            "TEXCOORD_0": g.f32(uv, "VEC2", len(uv))},
         "indices": g.u16(idx, len(idx)),
         "material": mat}
    if nrm is not None:
        p["attributes"]["NORMAL"] = g.f32(nrm, "VEC3", len(nrm))
    if joints is not None:
        p["attributes"]["JOINTS_0"] = g.ubyte(joints, len(joints), "VEC4")
        p["attributes"]["WEIGHTS_0"] = g.f32(weights, "VEC4", len(weights))
    return p


# --------------------------------------------------------------------- world
def terrain_prim(g, hf, ox, oy, atlas, step=2.0):
    """One tile's height grid as a mesh with its baked ground atlas."""
    rows, cols = hf.shape
    # vertex grid (row = +y, col = +x)
    ys, xs = np.mgrid[0:rows, 0:cols]
    wx = xs * step + ox
    wy = ys * step + oy
    wz = hf
    pos = np.stack([wx, wz, -wy], axis=-1).reshape(-1, 3)      # glTF frame
    # normals from the height gradient (world frame, then mapped)
    dzdx = np.zeros_like(wz)
    dzdy = np.zeros_like(wz)
    dzdx[:, 1:-1] = (wz[:, 2:] - wz[:, :-2]) / (2 * step)
    dzdy[1:-1, :] = (wz[2:, :] - wz[:-2, :]) / (2 * step)
    n = np.stack([-dzdx, np.ones_like(wz), -dzdy], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    nrm = n.reshape(-1, 3)
    # uv covers this tile's own atlas
    u = xs / (cols - 1)
    v = 1.0 - ys / (rows - 1)
    uvs = np.stack([u, v], axis=-1).reshape(-1, 2)
    # two triangles per quad; skip degenerate (flat) quads far outside
    idx = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            a = r * cols + c
            b = a + 1
            d = a + cols
            e = d + 1
            idx += [a, d, b, b, d, e]
    mi = g.material(atlas, double=False)
    return prim(g, pos.tolist(), nrm.tolist(), uvs.tolist(), idx, mi)


def export_world(g):
    w = R.World("intro")
    print(f"world: {w.nx}x{w.ny} tiles = {w.world_w:.0f} x {w.world_h:.0f} m")
    roots = []
    # --- terrain, one mesh per tile (each has its own ground atlas) -------
    tnodes = []
    for sc, ox, oy in w.tiles:
        atlas = sc.bake_ground(2048)
        if atlas is None:
            continue
        p = terrain_prim(g, sc.hf, ox, oy, atlas)
        mi = g.mesh([p], f"terrain_{sc.stem}")
        ni = g.node(mesh=mi, name=sc.stem)
        tnodes.append(ni)
    ti = g.node(name="terrain", children=tnodes)
    roots.append(ti)

    # --- props and scenery, baked into vertices, grouped by material ------
    buckets = {}

    def add_mesh(mats, geoms, pos, yaw, scale):
        """Bake one instance of a prop into its material bucket.

        Same transform as render_scene3d.add_mesh: yaw about +Z, uniform
        scale, translate to (x, y, terrain+dz), then convert to the glTF
        frame -- all baked, so there is no rotation convention to get wrong.
        """
        ca, sa = math.cos(yaw), math.sin(yaw)
        px, py, pz = pos
        for si, (pts, idxs, suv) in enumerate(geoms):
            if not idxs or not pts:
                continue
            tn = mats[si]["texture"] if si < len(mats) else ""
            tex = w.tex_for(tn) if tn else None
            if tex is None:
                continue
            b = buckets.setdefault(tex, {"v": [], "uv": [], "i": [],
                                         "alpha": tex.suffix.lower() == ".png"})
            base = len(b["v"])
            for (x, y, z) in pts:
                X = (x * ca - y * sa) * scale
                Y = (x * sa + y * ca) * scale
                Z = z * scale
                b["v"].append((px + X, Z, -(py + Y)))
            b["uv"].extend(list(suv)[:len(idxs)])
            b["i"].extend(i + base for i in idxs)

    for m, mox, moy in w.placements():
        if len(m) < 7:
            continue
        spec = w.reg.get(m[1], "")
        guid = None
        for tok in spec.replace(";", ",").split(","):
            tok = tok.strip()
            if tok.startswith("model/"):
                guid = tok.split("/")[1].split(".")[0]
        if not guid:
            continue
        mats, geoms = w.mesh(guid)
        if not geoms:
            continue
        x, y = m[2] + mox, m[3] + moy
        zf, size, aspect = m[4], m[5] or 1.0, m[6] or 0.0
        zt = w.height_at(x, y) + (zf or 0.0)
        add_mesh(mats, geoms, (x, y, zt), math.radians(aspect), size or 1.0)

    for s, sox, soy in w.scatter():
        if len(s) <= 6 or not isinstance(s[6], (bytes, bytearray)):
            continue
        try:
            inst = R.zmap_instances(bytes(s[6]))
        except ValueError:
            continue
        if not inst:
            continue
        guid = s[2].split("/")[1].split(".")[0] if len(s) > 2 else ""
        mats, geoms = w.mesh(guid)
        if not geoms:
            continue
        for p in inst["instances"]:
            px, py = p["x"] + sox, p["y"] + soy
            zt = w.height_at(px, py) + p["dz"]
            add_mesh(mats, geoms, (px, py, zt), p["yaw"], p["scale"])

    pn = []
    for tex, b in buckets.items():
        if not b["i"]:
            continue
        alpha = "MASK" if b["alpha"] else None
        mi = g.material(tex, alpha=alpha)
        p = prim(g, b["v"], None, b["uv"], b["i"], mi)
        mi2 = g.mesh([p], f"props_{tex.stem}")
        pn.append(g.node(mesh=mi2, name=f"props_{tex.stem}"))
    if pn:
        roots.append(g.node(name="props", children=pn))
    print(f"  props: {len(buckets)} materials, "
          f"{sum(len(b['v']) for b in buckets.values())} verts, "
          f"{sum(len(b['i']) // 3 for b in buckets.values())} tris")
    g.g["scenes"][0]["nodes"] = roots
    return w


# --------------------------------------------------------------------- actor
def rotmat(q):
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def mat4_inv(R, p):
    """column-major 4x4 of inverse(R | p) -- R is orthonormal.

    inverse([R p][0 1]) = [R^T | -R^T p][0 1];  glTF stores column-major.
    """
    out = []
    for c in range(4):
        for r in range(4):
            if c < 3:
                if r < 3:
                    out.append(round(R[c][r], 7))       # R^T[r][c] = R[c][r]
                else:
                    out.append(0.0)
            else:
                if r < 3:
                    out.append(round(-(R[0][r] * p[0] + R[1][r] * p[1]
                                       + R[2][r] * p[2]), 7))
                else:
                    out.append(1.0)
    return out


def part_tex(reg, part_name):
    """material name -> texture path, via the _Z spec the registry carries."""
    cands = [part_name]
    if part_name.endswith("_M"):
        cands.append(part_name[:-2] + "_Z")
    for cand in cands:
        for tk in reg.get(cand, "").replace(";", ",").split(","):
            tk = tk.strip()
            if tk.startswith("tex/"):
                q = ROOT / "extracted/netpak" / tk
                if q.exists():
                    return q
    for nm, spec in reg.items():                     # any tex= pair naming it
        for part in spec.split(";"):
            toks = [t.strip() for t in part.split(",")]
            if len(toks) >= 2 and toks[0].startswith("tex=") \
                    and toks[0][4:] == part_name and toks[1].startswith("tex/"):
                q = ROOT / "extracted/netpak" / toks[1]
                if q.exists():
                    return q
    return None


def export_actor(g):
    import varfile
    reg = {}
    for r in varfile.load(str(ROOT / "extracted/netpak/SysFileVer.var"))["value"]:
        if len(r) > 1 and isinstance(r[0], str) and isinstance(r[1], str):
            reg.setdefault(r[0], r[1])

    body = json.loads((ROOT / "extracted/models/cmz" / f"{BODY_RIG}.json")
                      .read_text(encoding="utf-8"))
    bones = body["bones"]
    rw = body["rw"]                      # bind world rotation (quaternion)
    pw = body["world"]                   # bind world position (metres)
    nb = len(bones)

    # -- skeleton nodes: local TRS from the bind pose, converted to glTF
    for b in bones:
        t = vmap([c * cmz.SCALE for c in b["offset"]])
        q = qmap(qconj(b["rot"]))
        g.node(name=b["name"], translation=[round(c, 6) for c in t],
               rotation=[round(c, 7) for c in q])
    # glTF node indices are the bone indices: the parts reference them too
    roots = [i for i, b in enumerate(bones) if b["parent"] < 0]
    for b in bones:
        if b["parent"] >= 0:
            g.g["nodes"][b["index"]].setdefault("children", []).append(b["parent"])
    # children were appended onto parents; rebuild properly instead
    for i in range(nb):
        g.g["nodes"][i].pop("children", None)
    for b in bones:
        if b["parent"] >= 0:
            g.g["nodes"][b["parent"]].setdefault("children", []).append(b["index"])
    skel_root = g.node(name="Bip01_root", children=roots)

    # -- inverse bind matrices (glTF frame, column-major 4x4)
    ibm = []
    for i in range(nb):
        ibm.extend(mat4_inv(rotmat(qmap(rw[i])), vmap(pw[i])))
    ibm_acc = g.f32(ibm, "MAT4", nb)
    g.g["skins"].append({"inverseBindMatrices": ibm_acc, "joints": list(range(nb)),
                         "skeleton": skel_root})

    # -- skinned mesh: one primitive per part (each part binds one texture).
    # Every wearable piece is its own .cmz with its own copy of the 119-bone
    # rig, so the parts are spread across the skin atlas's rig keys.
    skin = json.loads((ROOT / "extracted/models/skin/_skin.json")
                      .read_text(encoding="utf-8"))
    want = set(BODY_PARTS) | {HAIR_PART}
    primlist, nverts, ntris, nparts = [], 0, 0, 0
    for key, rig in skin.items():
        for part in rig["parts"]:
            if part["name"] not in want:
                continue
            tex = part_tex(reg, part["name"])
            if tex is None:
                print(f"  ! no texture for {part['name']}")
                continue
            base = nverts
            pv = [vmap(v["pos"]) for v in part["verts"]]
            pu = [(v.get("uv") or (0.0, 0.0)) for v in part["verts"]]
            pj = [(v["bones"][0], 0, 0, 0) for v in part["verts"]]
            pwts = [(1.0, 0.0, 0.0, 0.0) for _ in part["verts"]]
            pidx = []
            for f in part["faces"]:
                pidx.extend((f[0] + base, f[1] + base, f[2] + base))
            pmi = g.material(tex, alpha=("MASK" if tex.suffix.lower() == ".png"
                                         else None))
            primlist.append(prim(g, pv, None, pu, pidx, pmi,
                                 joints=pj, weights=pwts))
            nverts += len(pv)
            ntris += len(pidx) // 3
            nparts += 1
    print(f"  actor: {nverts} verts, {ntris} tris, {nparts} parts")
    mesh_node = g.node(mesh=g.mesh(primlist, "player"), skin=0, name="player_mesh")
    g.g["scenes"][0]["nodes"] = [skel_root, mesh_node]

    # -- animations: the clip rig's rotations, matched to body bones by name
    cliprig = json.loads((ROOT / "extracted/models/cmz" / f"{CLIP_RIG}.json")
                         .read_text(encoding="utf-8"))["bones"]
    bname = {b["name"]: b["index"] for b in bones}
    unmatched = set()

    for clip_name in CLIPS:
        cp = ROOT / "extracted/models/caf" / f"{clip_name}.json"
        if not cp.exists():
            print(f"  ! missing clip {clip_name}")
            continue
        clip = json.loads(cp.read_text(encoding="utf-8"))
        samplers, chans = [], []
        for tr in clip["tracks"]:
            nm = cliprig[tr["bone"]]["name"]
            bi = bname.get(nm)
            if bi is None:
                unmatched.add(nm)
                continue
            keys = tr["keys"]
            times = [round(k[0], 6) for k in keys]
            tacc = qacc = None
            if nm == "Bip01":                    # root: carry the translation
                tv = []
                for k in keys:
                    tv.extend(vmap((k[1] * cmz.SCALE, k[2] * cmz.SCALE,
                                    k[3] * cmz.SCALE)))
                tacc = g.f32(tv, "VEC3", len(keys))
            qv = []
            for k in keys:
                qv.extend(qmap(qconj((k[4], k[5], k[6], k[7]))))
            qacc = g.f32(qv, "VEC4", len(keys))
            ti = g.f32(times, "SCALAR", len(times))
            sbase = len(samplers)
            if tacc is not None:
                samplers.append({"input": ti, "interpolation": "LINEAR",
                                 "output": tacc})
                chans.append({"target": {"node": bi, "path": "translation"},
                              "sampler": sbase})
            samplers.append({"input": ti, "interpolation": "LINEAR",
                             "output": qacc})
            chans.append({"target": {"node": bi, "path": "rotation"},
                          "sampler": sbase if tacc is None else sbase + 1})
        if chans:
            g.g["animations"].append({"name": clip_name, "channels": chans,
                                      "samplers": samplers})
            print(f"  animation {clip_name}: {len(chans)} channels, "
                  f"{len(clip['tracks'])} tracks")
    if unmatched:
        print(f"  (clip bones with no body counterpart, skipped: "
              f"{sorted(unmatched)})")


# ------------------------------------------------------------------- browser
HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Chrono Tales — intro world</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 html,body{margin:0;height:100%;overflow:hidden;background:#0b0e13;color:#cfe}
 #app{height:100%}
 #hud{position:fixed;left:10px;top:10px;font:13px/1.5 ui-monospace,Consolas,monospace;
      background:#0b0e13cc;padding:8px 10px;border:1px solid #2a3140;border-radius:6px}
 #hud b{color:#7ee0b0}
</style></head><body>
<div id="app"></div>
<div id="hud"><b>Chrono Tales</b> · intro world (768×512 m, six tiles)<br>
 WASD / arrows move · Space jump · click ground to walk · drag to orbit · wheel zoom<br>
 <span id="stat"></span></div>
<script type="importmap">{"imports":{"three":"./vendor/three.module.js"}}</script>
<script type="module" src="./main.js"></script></body></html>
"""

JS = r"""import * as THREE from 'three';
import {GLTFLoader} from './vendor/GLTFLoader.js';

const app = document.getElementById('app');
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = false;
app.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const SKY = 0x9fc4e8;
scene.background = new THREE.Color(SKY);
scene.fog = new THREE.Fog(SKY, 220, 620);

const sun = new THREE.DirectionalLight(0xfff2dd, 2.0);
sun.position.set(-180, 260, 140);
scene.add(sun);
scene.add(new THREE.HemisphereLight(0xbfd8ff, 0x506048, 1.1));

const camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 0.3, 2200);

// ---- world height sampler (from height.js, generated by export_web.py) ----
const H = window.HEIGHT;                     // {x0,y0,step,cols,rows,data}
function groundY(x, y){
  const c = Math.min(Math.max((x - H.x0)/H.step, 0), H.cols-1.001);
  const r = Math.min(Math.max((y - H.y0)/H.step, 0), H.rows-1.001);
  const c0 = c|0, r0 = r|0, fc = c-c0, fr = r-r0, d = H.data;
  const i = r0*H.cols+c0;
  return (d[i]*(1-fc)+d[i+1]*fc)*(1-fr) + (d[i+H.cols]*(1-fc)+d[i+H.cols+1]*fc)*fr;
}

// game frame is Z up; three.js is Y up.  Everything was exported already
// converted, so the sampler has to match: world (x,y) -> three (x, -y).
const player = new THREE.Object3D();
scene.add(player);

let mixer = null, actions = {}, current = null;
const loader = new GLTFLoader();
const stat = document.getElementById('stat');

Promise.all([
  new Promise(res => loader.load('./scene.gltf', g => res(g), undefined, e => {console.error(e); res(null);})),
  new Promise(res => loader.load('./actor.gltf', g => res(g), undefined, e => {console.error(e); res(null);})),
]).then(([w, a]) => {
  if (w) scene.add(w.scene);
  if (a){
    const obj = a.scene;
    obj.traverse(o => { if (o.isMesh){ o.frustumCulled = false; } });
    player.add(obj);
    mixer = new THREE.AnimationMixer(obj);
    for (const clip of a.animations){
      actions[clip.name] = mixer.clipAction(clip);
      actions[clip.name].play();
      actions[clip.name].setEffectiveWeight(0);
    }
    current = 'stand01';
    if (actions[current]) actions[current].setEffectiveWeight(1);
  }
  player.position.set(128, groundY(128, 128), -128);
  stat.textContent = 'loaded';
});

function play(name){
  if (!actions[name] || current === name) return;
  const from = actions[current], to = actions[name];
  to.reset(); to.setEffectiveWeight(1); to.play();
  if (from){ from.crossFadeTo(to, 0.18, false); }
  current = name;
}

// ---- controls -------------------------------------------------------------
const keys = {};
addEventListener('keydown', e => { keys[e.code] = true; if (e.code==='Space') e.preventDefault(); });
addEventListener('keyup',   e => { keys[e.code] = false; });

let camYaw = 0, camPitch = 0.42, camDist = 14, drag = false, lx = 0, ly = 0;
renderer.domElement.addEventListener('mousedown', e => { drag = true; lx = e.clientX; ly = e.clientY; });
addEventListener('mouseup', () => drag = false);
addEventListener('mousemove', e => {
  if (!drag) return;
  camYaw   -= (e.clientX - lx) * 0.006;
  camPitch  = Math.min(1.35, Math.max(0.05, camPitch + (e.clientY - ly) * 0.005));
  lx = e.clientX; ly = e.clientY;
});
renderer.domElement.addEventListener('wheel', e => {
  camDist = Math.min(60, Math.max(3, camDist * (e.deltaY > 0 ? 1.12 : 0.89)));
});
const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
let walkTo = null;
renderer.domElement.addEventListener('click', e => {
  ndc.set(e.clientX/innerWidth*2-1, -(e.clientY/innerHeight)*2+1);
  ray.setFromCamera(ndc, camera);
  const h = new THREE.Vector3();
  // analytic ground plane walk: sample along the ray
  let t = 0, best = null;
  for (let s = 2; s < 900; s += 2){
    const px = camera.position.x + ray.ray.direction.x*s;
    const pz = camera.position.z + ray.ray.direction.z*s;
    const py = camera.position.y + ray.ray.direction.y*s;
    const g = groundY(-pz, px);       // three (x,z) -> world (y, x)
    if (py <= g){ best = {x:-pz, y:px}; break; }
  }
  if (best) walkTo = best;
});

const vel = new THREE.Vector3();
let vy = 0, grounded = true, jumpPhase = 0;
const clock = new THREE.Clock();

function animate(){
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.05);

  // camera-relative movement
  let mx = 0, mz = 0;
  if (keys.KeyW || keys.ArrowUp) mz -= 1;
  if (keys.KeyS || keys.ArrowDown) mz += 1;
  if (keys.KeyA || keys.ArrowLeft) mx -= 1;
  if (keys.KeyD || keys.ArrowRight) mx += 1;
  const moving = (mx || mz || walkTo);
  const speed = 7.2;                                  // m/s, the run clip's pace
  if (moving){
    if (mx || mz){
      walkTo = null;
      const len = Math.hypot(mx, mz); mx /= len; mz /= len;
      const c = Math.cos(camYaw), s = Math.sin(camYaw);
      vel.set(mx*c + mz*s, 0, -mx*s + mz*c);
    } else if (walkTo){
      const dx = walkTo.x - player.position.x, dz = walkTo.z - player.position.z;
      const d = Math.hypot(dx, dz);
      if (d < 0.6) walkTo = null;
      else vel.set(dx/d, 0, dz/d);
    }
    if (walkTo || (mx || mz)){
      player.position.x += vel.x * speed * dt;
      player.position.z += vel.z * speed * dt;
      const want = Math.atan2(vel.x, vel.z);
      let d = want - player.rotation.y;
      while (d >  Math.PI) d -= Math.PI*2;
      while (d < -Math.PI) d += Math.PI*2;
      player.rotation.y += d * Math.min(1, dt*12);
    }
  }
  if (keys.Space && grounded){ vy = 6.2; grounded = false; jumpPhase = 0; play('jumpstart'); }
  const gy = groundY(-player.position.z, player.position.x);
  if (!grounded){
    vy -= 16 * dt;
    player.position.y += vy * dt;
    jumpPhase += dt;
    if (player.position.y <= gy){
      player.position.y = gy; vy = 0; grounded = true;
    } else if (vy < 0) play('jumping');
  } else {
    player.position.y = gy;
  }
  if (grounded) play(moving ? 'run' : 'stand01');
  if (mixer) mixer.update(dt);

  // third-person camera
  const cx = player.position.x + Math.sin(camYaw)*Math.cos(camPitch)*camDist;
  const cz = player.position.z + Math.cos(camYaw)*Math.cos(camPitch)*camDist;
  const cy = player.position.y + Math.sin(camPitch)*camDist + 2.2;
  camera.position.lerp(new THREE.Vector3(cx, cy, cz), 1 - Math.pow(0.0015, dt));
  camera.lookAt(player.position.x, player.position.y + 1.5, player.position.z);
  renderer.render(scene, camera);
}
animate();
addEventListener('resize', () => {
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # ---------------- world ----------------
    gw = Gltf("scene")
    w = export_world(gw)
    gw.write(OUT / "scene.gltf")

    # ---------------- height sampler ----------------
    hf = w.hf.astype(np.float32)
    js = ("window.HEIGHT={x0:0,y0:0,step:%r,cols:%d,rows:%d,data:%s};"
          % (R.TERRAIN_STEP, hf.shape[1], hf.shape[0],
             "[" + ",".join("%g" % v for v in hf.reshape(-1)) + "]"))
    (OUT / "height.js").write_text(js, encoding="utf-8")
    print(f"  height.js: {hf.shape[1]}x{hf.shape[0]} @ {R.TERRAIN_STEP} m")

    # ---------------- actor ----------------
    ga = Gltf("actor")
    export_actor(ga)
    ga.write(OUT / "actor.gltf")

    (OUT / "index.html").write_text(HTML, encoding="utf-8")
    (OUT / "main.js").write_text(JS, encoding="utf-8")
    print(f"  index.html + main.js")


if __name__ == "__main__":
    main()
