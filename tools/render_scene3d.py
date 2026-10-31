#!/usr/bin/env python3
"""render_scene3d.py - full 3D render of a decoded intro .zmap scene.

Composites, in world space (metres, +Z up):
  * terrain mesh from the MAP_HEIGHTDATA grid (129 x 129 u16 in two byte
    planes, 2 m spacing, z = minz + h16*(maxz-minz)/65530 from the header);
  * MAP_WIDGETDATA scenery instances (tools/zmap.py decode): each placed at
    (x, y, terrain(x,y)+dz), yawed by its direction pair, scaled by its u8;
  * MAP_MODELDATA fixed placements ([id, name, x, y, z, size, aspect, ...]),
    model resolved through SysFileVer.var.

A `.zmap` is one 256 m tile, not a whole map: `--world` stitches the whole
shipped grid (six tiles = 768 x 512 m) into one scene via the World class.

Geometry comes straight from the shipped .53wf props (GPAK_FORMAT.md 4.3):
`VERT` is 6 header floats (base, step per axis, centimetres) + u8 triples,
`TEUV` is one float32 uv pair *per index* (per corner), and the material table
(`MAT3`, or `MAT2` without the 8 trailing zero bytes) names the texture for
submesh i.  Textures are bound through extracted/models/texmap.json, whose
entries are hash-verified against the intro zmaps.

Usage:
  python tools/render_scene3d.py [stem] [xmax] [--close|--world]
Output: extracted/maps/<stem>_3d[_tex][_close]_{oblique,plan,horizon}.png
"""
import io
import json
import math
import pathlib
import struct
import sys
import zipfile

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import varfile  # noqa: E402
from obj_render import render, load_texture  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAPS = ROOT / "extracted" / "maps"

TERRAIN_STEP = 2.0        # metres between height-grid vertices
HEIGHT_N = 129            # 129 x 129 height grid -> 256 m x 256 m tile
TILE_M = 256.0            # TerrainLodSIZE (def.gbc): one .zmap covers this
GROUND_TILE = 6.0         # metres per ground-texture repeat
TERR_DOWNSAMPLE = 1       # render-mesh decimation (z sampling stays full-res)
# Terrain z range is NOT a constant: it is read per map from the
# MAP_HEIGHTDATA header (f32 minz, f32 maxz) and is -100/900 in all six
# shipped maps.  See Scene.__init__.  ZMIN/ZMAX survive only as the fallback
# used when a record is missing its header.
ZMIN, ZMAX = -100.0, 900.0
HEIGHT_DIV = 65530.0      # exe: z = minz + h16 * (maxz-minz) * 1/65530

TAGS = ("MAT3", "MAT2", "MESH", "INDX", "TEUV", "VERT", "VERH", "ENDM")


def _gbk(b):
    return b.decode("gbk", "replace")


def fw3_materials(d, off, tag, count):
    """Parse a MAT3/MAT2 table into [{name, texture}].  MAT2 has no tail pad."""
    mats, i = [], off + 4
    for _ in range(count):
        if i + 2 > len(d):
            break
        n = struct.unpack_from("<H", d, i)[0]
        if not (0 < n <= 128) or i + 2 + n > len(d):
            break
        name = _gbk(d[i + 2:i + 2 + n])
        j = i + 2 + n + 16          # u16,u16,u32,u32,u16,u16
        tex = ""
        if j + 2 <= len(d):
            tl = struct.unpack_from("<H", d, j)[0]
            if 0 < tl <= 260 and j + 2 + tl <= len(d):
                tex = _gbk(d[j + 2:j + 2 + tl])
                j += 2 + tl
            else:
                j += 2
        if tag == "MAT3":
            j += 8                  # MAT3 only: 8 trailing zero bytes
        mats.append({"name": name, "texture": tex})
        i = j
    return mats


def fw3_mesh(path):
    """-> (materials, [(pts, idxs, uvs)]) with pts in metres, +Z up."""
    d = pathlib.Path(path).read_bytes()
    if d[:4] != b"53WF":
        raise ValueError(f"{path}: not 53WF")
    nmesh = struct.unpack_from("<H", d, 10)[0]

    chain, last = [], -1
    for i in range(len(d) - 4):
        try:
            s = d[i:i + 4][::-1].decode("ascii")
        except UnicodeDecodeError:
            continue
        if s in TAGS and i > last:
            chain.append((i, s))
            last = i

    mats = []
    for off, t in chain:
        if t in ("MAT3", "MAT2"):
            nm = struct.unpack_from("<H", d, 8)[0]
            mats = fw3_materials(d, off, t, nm)
            break

    def end_of(k):
        return chain[k + 1][0] if k + 1 < len(chain) else len(d)

    groups = []
    for k, (off, t) in enumerate(chain):
        if t != "INDX":
            continue
        end = end_of(k)
        ib = d[off + 4:end]
        idxs = list(struct.unpack_from("<%dH" % (len(ib) // 2), ib, 0))
        uvs, pts = [], []
        for k2 in range(k + 1, min(k + 4, len(chain))):
            off2, t2 = chain[k2]
            if t2 == "TEUV":
                body = d[off2 + 4:end_of(k2)]
                nu = len(body) // 8
                f = struct.unpack_from("<%df" % (nu * 2), body, 0)
                uvs = [(f[i * 2], f[i * 2 + 1]) for i in range(nu)]
            elif t2 in ("VERT", "VERH"):
                body = d[off2 + 4:end_of(k2)]
                if t2 == "VERH":
                    nv = len(body) // 12
                    vs = struct.unpack_from("<%df" % (nv * 3), body, 0)
                    pts = [tuple(vs[i * 3:i * 3 + 3]) for i in range(nv)]
                elif len(body) >= 24:
                    bx, sx, by, sy, bz, sz = struct.unpack_from("<6f", body, 0)
                    rest = body[24:]
                    nv = len(rest) // 3
                    pts = [(rest[i * 3] * sx + bx, rest[i * 3 + 1] * sy + by,
                            rest[i * 3 + 2] * sz + bz) for i in range(nv)]
        pts = [(x * 0.01, y * 0.01, z * 0.01) for x, y, z in pts]
        # one uv per corner; pad/truncate so the index aligns with INDX
        if len(uvs) != len(idxs):
            uvs = (uvs + [(0.0, 0.0)] * len(idxs))[:len(idxs)]
        groups.append((pts, idxs, uvs))
        if len(groups) >= nmesh:
            break
    return mats, groups


class Scene:
    def __init__(self, stem):
        self.stem = stem
        f = ROOT / "extracted/netpak/intro" / (stem + ".zmap")
        d = f.read_bytes()
        z = zipfile.ZipFile(io.BytesIO(d[4:]))
        rec = varfile.load_varbin(z.read("varbin"))["value"][0]
        self.ver, terr, layer, widget, dataex, models = rec
        self.terr = terr
        # MAP_HEIGHTDATA: 12-byte header, then TWO 129x129 byte planes, then a
        # u32.  The two planes are the high and low byte of one 16-bit height
        # per vertex -- they are not two fields.  Proved in chronotales.exe
        # (the .zmap record loader at 0x4ee4f9, 0x8212 = 33,298 = the exact
        # record length it demands):
        #
        #   cmp  [edi], 0xfab00002      ; tag
        #   mov  [esi+0x134], 0x81      ; 129 cols
        #   mov  [esi+0x138], 0x81      ; 129 rows
        #   mov  edx, [edi]             ; minz   (float, straight from header)
        #   fld  [edi+4]                ; maxz   (float)
        #   fcom [esp+0x30] / jz        ; "SetHeightData minz>maxz err"
        #   sub  eax, ecx ... add ecx, edi   ; ecx = edi + 129*129 -> plane 2
        #   mov  dh, [edi] / mov dl, [ecx]   ; h16 = plane1<<8 | plane2
        #   fild / fmul [0x6d17e8]      ; * 1/65530
        #   fadd [esp+0x38]             ; + minz
        #
        # 0x6d17e8 is 1.5260186e-05 = 1/65530, and the map *saver* at 0x4ee45a
        # quantises with the reciprocal 65.53 (0x6d17d8) after clamping to
        # -100.01 .. 900.0 -- i.e. a 1000 m span, 65,530 steps.  The header
        # floats are therefore live data, not a comment: the z range really is
        # -100..900 and the quantisation step is 15 mm, not 3.9 m.
        #
        # Consequence: the old 8-bit read (high byte only, over -50..450) was
        # off by a factor of two in relief and stair-stepped at 3.9 m; the
        # 2-pass blur that used to hide those stairs is no longer needed.
        tag, hmin, hmax = struct.unpack_from("<Iff", self.terr, 0)
        if tag != 0xFAB00002 or not (hmax > hmin):
            hmin, hmax = ZMIN, ZMAX
        self.zmin, self.zmax = float(hmin), float(hmax)
        body = self.terr[12:-4]
        self.cols = self.rows = HEIGHT_N
        self.world_w = self.world_h = (HEIGHT_N - 1) * TERRAIN_STEP
        n = HEIGHT_N * HEIGHT_N
        hi = np.frombuffer(body[:n], dtype=np.uint8).reshape(HEIGHT_N, HEIGHT_N)
        lo = np.frombuffer(body[n:2 * n], dtype=np.uint8).reshape(HEIGHT_N, HEIGHT_N)
        h16 = (hi.astype(np.int32) << 8) | lo.astype(np.int32)
        self.h16 = h16
        self.hf = self.zmin + h16 * (self.zmax - self.zmin) / HEIGHT_DIV
        self.hf = self._smooth(self.hf, passes=0)
        self.widget = [e for e in (widget[1:] if isinstance(widget, list) else [])
                       if isinstance(e, list)]
        self.models = [e for e in (models if isinstance(models, list) else [])
                       if isinstance(e, list)]
        # name -> model path from the registry
        self.reg = {}
        regf = ROOT / "extracted/netpak/SysFileVer.var"
        if regf.exists():
            for r in varfile.load(str(regf))["value"]:
                if isinstance(r[0], str) and isinstance(r[1], str):
                    self.reg.setdefault(r[0], r[1])
        self._mesh_cache = {}
        # material filename -> texture path (hash-verified links)
        self.texmap = {}
        tm = ROOT / "extracted/models/texmap.json"
        if tm.exists():
            for name, v in json.load(open(tm, encoding="utf-8")).get("by_name", {}).items():
                ent = v.get("tex_entry") if isinstance(v, dict) else None
                if ent:
                    p = ROOT / "extracted" / ent[0] / ent[1]
                    if p.exists():
                        self.texmap[name] = p

        # ground: MAP_DATAEX layer1..6 are 地表_* names, resolved to
        # surface/<guid>.jpg through SysFileVer; MAP_LAYERDATA is a 1 px/m
        # RGB weight map whose channels select layer 1/2/3.
        self.ground = []
        for e in (dataex[:6] if isinstance(dataex, list) else []):
            nm = e[0] if isinstance(e, list) and e else None
            p = None
            if isinstance(nm, str):
                for tok in self.reg.get(nm, "").replace(";", ",").split(","):
                    tok = tok.strip()
                    if tok.startswith("surface/"):
                        q = ROOT / "extracted/netpak" / tok
                        if q.exists():
                            p = q
            self.ground.append((nm, p))
        self.splat = None
        if isinstance(layer, list) and len(layer) == 4 and isinstance(layer[3], bytes):
            self.splat = (layer[1], layer[2], layer[3])

    def blend_ground(self, res):
        """Blend the six weighted ground layers into one res x res RGB array.

        `res` is the pixel size of THIS tile's square atlas.  Kept separate from
        bake_ground() so a World can blend each tile with its own layer set --
        the six intro tiles do not share one (slot 1 is 沙地 in one and
        地表_路面007 in another), so a single global blend is impossible.
        """
        import numpy as np
        from PIL import Image

        paths = [p for _, p in self.ground[:6]]
        if sum(1 for p in paths if p) < 3 or self.splat is None:
            return None
        have = [i for i, p in enumerate(paths) if p]
        w, h, pix = self.splat
        # The splat is 256 x 512 = TWO 256-row halves of the same 256 m world,
        # not one map plus padding: the engine normalises a cell in the top half
        # against the cell 256 rows below it (FUN_004edb40 sums all six bytes and
        # scales them to 0xff), so R/G/B of the top half weight layers 0/1/2 and
        # R/G/B of the bottom half weight layers 3/4/5.
        src = Image.frombytes("RGB", (w, h), pix)
        half = h // 2
        wt = np.zeros((res, res, 6), dtype=np.float32)
        for k in range(2):
            part = src.crop((0, k * half, w, (k + 1) * half)).resize(
                (res, res), Image.BILINEAR)
            wt[:, :, k * 3:(k + 1) * 3] = np.asarray(part, dtype=np.float32)
        tile = max(2, int(round(res * GROUND_TILE / self.world_w)))
        rep = int(np.ceil(res / tile))
        layers = []
        for i in have:
            t = Image.open(paths[i]).convert("RGB").resize((tile, tile),
                                                           Image.LANCZOS)
            layers.append(np.tile(np.asarray(t, dtype=np.float32), (rep, rep, 1))
                          [:res, :res])
        # a layer whose surface/<guid>.jpg is not shipped keeps no weight, so
        # the remaining layers are renormalised by the sum over `have`
        wt = wt[:, :, have]
        tot = wt.sum(axis=2)
        nz = tot > 0
        safe = np.where(nz, tot, 1.0)[:, :, None]
        rgb = np.zeros((res, res, 3), dtype=np.float32)
        for i in range(len(layers)):
            rgb += layers[i] * wt[:, :, i:i + 1]
        rgb /= safe
        rgb[~nz] = layers[0][~nz]          # unpainted -> layer 0
        return rgb

    def bake_ground(self, size=2048, force=False):
        """Blend the weighted ground layers into one world-aligned atlas.

        MAP_LAYERDATA is 256x512 RGB where channel *i* is the weight of ground
        layer *i* -- it is weights, not an index: the map is dominated by
        0x000000 (unpainted) and 0xFF0000 (full layer 0), with 22,370 distinct
        colours overall because the edges are anti-aliased.  The rasteriser can
        only bind one texture per triangle, which collapsed that splat to 6 m
        stair-steps, so the blend is baked once here instead.

        Each layer is tiled at GROUND_TILE metres before the blend, otherwise a
        256x256 texture stretched over a 256 m world loses all its detail.
        """
        import numpy as np
        from PIL import Image

        outdir = MAPS / "ground"
        outdir.mkdir(parents=True, exist_ok=True)
        out = outdir / f"{self.stem}_ground{size}.png"
        if out.exists() and not force:
            return out
        rgb = self.blend_ground(size)
        if rgb is None:
            return None
        Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(out)
        print(f"  baked ground {out.name}  {size}x{size}  "
              f"tile {max(2, int(round(size * GROUND_TILE / self.world_w)))}px"
              f"/{GROUND_TILE:.0f} m")
        return out

    def placements(self):
        """-> (row, ox, oy) in world metres.  A tile is its own frame (ox=oy=0)."""
        for e in self.models:
            yield e, 0.0, 0.0

    def scatter(self):
        for e in self.widget:
            yield e, 0.0, 0.0

    def ground_layer(self, x, y):
        """Dominant ground layer (0..2) at world metres, from the RGB splat."""
        if not self.splat:
            return 0
        w, h, pix = self.splat
        cx = min(w - 1, max(0, int(x)))
        cy = min(h - 1, max(0, int(y)))
        o = (cy * w + cx) * 3
        r, g, b = pix[o], pix[o + 1], pix[o + 2]
        if g > r and g >= b:
            return 1
        if b > r and b > g:
            return 2
        return 0

    def _smooth(self, field, passes=0):
        """Optional tent-blur of the height field, in metres.

        Kept only as an escape hatch.  It used to run 2 passes to hide the
        8-bit quantisation staircase (3.9 m per step, which turned every 4 m
        render quad into a dark tile); the 16-bit decode gives a 15 mm step, so
        the field is already smooth and blurring now only softens real relief.
        """
        if passes <= 0:
            return field
        a = np.asarray(field, dtype=np.float32)
        for _ in range(passes):
            q = np.pad(a, 1, mode="edge")
            a = (q[0:-2, 0:-2] + 2 * q[0:-2, 1:-1] + q[0:-2, 2:]
                 + 2 * q[1:-1, 0:-2] + 4 * q[1:-1, 1:-1] + 2 * q[1:-1, 2:]
                 + q[2:, 0:-2] + 2 * q[2:, 1:-1] + q[2:, 2:]) / 16.0
        return a

    def height_at(self, x, y):
        c, r = x / TERRAIN_STEP, y / TERRAIN_STEP
        c = min(max(c, 0), self.cols - 1.001)
        r = min(max(r, 0), self.rows - 1.001)
        c0, r0 = int(c), int(r)
        fc, fr = c - c0, r - r0
        g = self.hf
        h00 = g[r0, c0]
        h10 = g[r0, c0 + 1]
        h01 = g[r0 + 1, c0]
        h11 = g[r0 + 1, c0 + 1]
        v = (h00 * (1 - fc) + h10 * fc) * (1 - fr) + \
            (h01 * (1 - fc) + h11 * fc) * fr
        return float(v)

    def mesh(self, guid):
        if guid not in self._mesh_cache:
            p = ROOT / "extracted/netpak/model" / (guid.lower() + ".fw3")
            self._mesh_cache[guid] = fw3_mesh(p) if p.exists() else ([], [])
        return self._mesh_cache[guid]

    def tex_for(self, name):
        """Material filename -> texture path, exact then stem match."""
        if name in self.texmap:
            return self.texmap[name]
        stem = pathlib.Path(name).stem
        for k, v in self.texmap.items():
            if pathlib.Path(k).stem == stem:
                return v
        return None


def height_tint(z, zmin=ZMIN, zmax=ZMAX):
    """Near-white height tint for *textured* ground.

    terrain_color() below is a 5-band palette, which is right for the flat
    fallback but multiplies the ground texture into hard rectangular bands --
    adjacent 4 m quads land in different bands.  This keeps only a gentle
    darkening with height so the texture reads through.
    """
    t = min(1.0, max(0.0, (z - zmin) / (zmax - zmin)))
    g = 0.86 + 0.14 * t
    return (int(255 * g), int(255 * g), int(250 * g))


def terrain_color(z, zmin=ZMIN, zmax=ZMAX):
    t = (z - zmin) / (zmax - zmin)
    if t < 0.25:
        return (46, 74, 44)
    if t < 0.45:
        return (66, 92, 50)
    if t < 0.65:
        return (96, 100, 66)
    if t < 0.85:
        return (118, 116, 104)
    return (152, 152, 150)


TYPE_COLORS = {
    "蘑菇": (214, 88, 74), "灌木": (62, 122, 58), "花": (232, 196, 72),
    "草": (88, 134, 60), "树": (52, 110, 48), "石": (128, 128, 126),
    "木箱": (146, 106, 62), "建筑": (168, 148, 118),
}


def color_for(name):
    for k, c in TYPE_COLORS.items():
        if k in name:
            return c
    return (180, 180, 180)


class World:
    """The tile grid the world is actually made of.

    A `.zmap` is not a whole map, it is one **256 m tile** of one.  `def.gbc`
    spells the scheme out:

        #define TerrainLodSIZE 256
        #define WPOS2BPOS(s) (((s)+128000)/TerrainLodSIZE)
        #define BPOS2WPOS(s) ((s)*TerrainLodSIZE-128000)
        #define MAKEMAPID(ix,iy) ((ix)+(iy)*1000)   // 每块场景产生一个ID

    and `world.gbc::CheckWorldCenterPos` streams a (2*maprs+1)^2 neighbourhood of
    `OnGetCopyName() + MAKEMAPID(ix,iy)` around the player, 600 m out.  So the
    file name carries the grid cell: `intro517493` is ix=493, iy=517.  The six
    shipped tiles are a 3 x 2 block (ix 493..495, iy 517..518) = 768 x 512 m.

    This also explains the "six copies of one scene" oddity recorded earlier:
    they are six *adjacent tiles of one world*, all carrying the internal name
    `w100`, and their heights agree across every shared edge to <= 2 units of
    65,530 (<= 30 mm) -- see World.seam_max.
    """

    def __init__(self, copy="intro", tiles=None):
        self.copy = copy
        cells = {}
        for p in sorted((ROOT / "extracted/netpak" / copy).glob("*.zmap")):
            stem = p.stem
            rest = stem[len(copy):] if stem.startswith(copy) else ""
            if not rest.isdigit():
                continue
            mid = int(rest)
            cells[(mid % 1000, mid // 1000)] = stem
        if tiles:
            cells = {k: v for k, v in cells.items() if v in tiles}
        if not cells:
            raise SystemExit(f"no tiles under netpak/{copy}")
        self.cells = cells
        self.ix0 = min(k[0] for k in cells)
        self.iy0 = min(k[1] for k in cells)
        self.nx = max(k[0] for k in cells) - self.ix0 + 1
        self.ny = max(k[1] for k in cells) - self.iy0 + 1
        missing = [(self.ix0 + x, self.iy0 + y)
                   for y in range(self.ny) for x in range(self.nx)
                   if (self.ix0 + x, self.iy0 + y) not in cells]
        if missing:
            print(f"  warning: {len(missing)} grid cells not shipped: {missing}")

        self.tiles = []
        for (ix, iy), stem in sorted(cells.items()):
            self.tiles.append((Scene(stem), (ix - self.ix0) * TILE_M,
                               (iy - self.iy0) * TILE_M))

        step = HEIGHT_N - 1
        self.cols = self.nx * step + 1
        self.rows = self.ny * step + 1
        self.world_w = self.nx * TILE_M
        self.world_h = self.ny * TILE_M
        self.zmin = self.tiles[0][0].zmin
        self.zmax = self.tiles[0][0].zmax
        self.hf = np.zeros((self.rows, self.cols), dtype=np.float32)
        self.seam_max = 0.0
        for sc, ox, oy in self.tiles:
            r0 = int(round(oy / TERRAIN_STEP))
            c0 = int(round(ox / TERRAIN_STEP))
            for r in range(HEIGHT_N):
                for c in range(HEIGHT_N):
                    g = sc.hf[r, c]
                    if self.hf[r0 + r, c0 + c] != 0.0:
                        self.seam_max = max(
                            self.seam_max,
                            abs(float(self.hf[r0 + r, c0 + c] - g)))
                    self.hf[r0 + r, c0 + c] = g
        # a tile is its own coordinate frame; the offsets are applied in
        # placements()/scatter() so build() can treat a World like a Scene
        self.models = []
        self.widget = []
        self.ground = []
        self.reg = self.tiles[0][0].reg
        self.texmap = self.tiles[0][0].texmap
        self._mesh_cache = self.tiles[0][0]._mesh_cache

    # -- Scene-compatible surface -------------------------------------------
    def height_at(self, x, y):
        c, r = x / TERRAIN_STEP, y / TERRAIN_STEP
        c = min(max(c, 0), self.cols - 1.001)
        r = min(max(r, 0), self.rows - 1.001)
        c0, r0 = int(c), int(r)
        fc, fr = c - c0, r - r0
        g = self.hf
        return float((g[r0, c0] * (1 - fc) + g[r0, c0 + 1] * fc) * (1 - fr)
                     + (g[r0 + 1, c0] * (1 - fc) + g[r0 + 1, c0 + 1] * fc) * fr)

    def mesh(self, guid):
        return self.tiles[0][0].mesh(guid)

    def tex_for(self, name):
        return self.tiles[0][0].tex_for(name)

    def placements(self):
        for sc, ox, oy in self.tiles:
            for e in sc.models:
                yield e, ox, oy

    def scatter(self):
        for sc, ox, oy in self.tiles:
            for e in sc.widget:
                yield e, ox, oy

    def bake_ground(self, res=1024, force=False):
        """One atlas for the whole grid, each tile blended with its own layers."""
        import numpy as np
        from PIL import Image

        outdir = MAPS / "ground"
        outdir.mkdir(parents=True, exist_ok=True)
        out = outdir / f"world_{self.copy}_ground{res}.png"
        if out.exists() and not force:
            return out
        canvas = np.zeros((self.ny * res, self.nx * res, 3), dtype=np.float32)
        done = 0
        for sc, ox, oy in self.tiles:
            rgb = sc.blend_ground(res)
            if rgb is None:
                continue
            cx = int(round(ox / TILE_M)) * res
            cy = int(round(oy / TILE_M)) * res
            canvas[cy:cy + res, cx:cx + res] = rgb
            done += 1
        if not done:
            return None
        Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8)).save(out)
        print(f"  baked world ground {out.name}  {canvas.shape[1]}x"
              f"{canvas.shape[0]}  {done} tiles")
        return out


def build(scene, terrain_down=TERR_DOWNSAMPLE, region=None, textured=True):
    """-> verts, tris, rgb, uvs, texes, tri_tex.

    tris are ((vert_index, uv_index_or_None), ...); tri_tex is a slot into
    texes (None = shade with tri_rgb instead).
    """
    verts, tris, rgb = [], [], []
    uvs, texes, tri_tex = [], [], []
    tex_cache, tex_slot = {}, {}

    xmin, ymin, xmax, ymax = region or (-1e9, -1e9, 1e9, 1e9)

    # ---- terrain (render mesh decimated; z sampling uses the full grid) ----
    step = TERRAIN_STEP * terrain_down
    c0 = max(0, int(xmin / step))
    r0 = max(0, int(ymin / step))
    c1 = min((scene.cols - 1) // terrain_down, int(xmax / step))
    r1 = min((scene.rows - 1) // terrain_down, int(ymax / step))
    nc = c1 - c0 + 1
    nr = r1 - r0 + 1
    base = len(verts)
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            x, y = c * step, r * step
            verts.append((x, y, scene.height_at(x, y)))

    def slot_of(p):
        if p is None or not textured:
            return None
        if p not in tex_slot:
            if p not in tex_cache:
                tex_cache[p] = load_texture(p)
            if tex_cache[p] is None:
                return None
            tex_slot[p] = len(texes)
            texes.append(tex_cache[p])
        return tex_slot[p]

    gslots = [slot_of(p) for _, p in scene.ground]
    any_ground = any(s is not None for s in gslots)
    baked = slot_of(scene.bake_ground()) if textured else None

    for r in range(nr - 1):
        for c in range(nc - 1):
            a = base + r * nc + c
            cx, cy = (c + 0.5) * step, (r + 0.5) * step
            slot = gslots[scene.ground_layer(cx, cy)] if any_ground else None
            for tri in (((a, None), (a + 1, None), (a + nc, None)),
                        ((a + 1, None), (a + nc + 1, None), (a + nc, None))):
                tris.append(tri)
                zt = verts[tri[0][0]][2]
                rgb.append(height_tint(zt, scene.zmin, scene.zmax)
                           if baked is not None
                           else terrain_color(zt, scene.zmin, scene.zmax))
                if baked is not None:
                    # World-aligned atlas: one uv per corner, no tiling.  The v
                    # axis is inverted because obj_render samples at
                    # ty = (1-v)*h, and row 0 of the baked atlas is world y=0
                    # (the splat is row-major from y=0).  Passing v = y/world
                    # would read row 0 at y=256 instead, which mirrors the
                    # whole ground field about the middle row and puts every
                    # road on the wrong side of its valley.  With v = 1-y/world
                    # the ground stays glued to the terrain whichever way up
                    # the camera looks.
                    ub = len(uvs)
                    for vi, _ in tri:
                        vx, vy, _ = verts[vi]
                        uvs.append((vx / scene.world_w,
                                    1.0 - vy / scene.world_h))
                    tris[-1] = ((tri[0][0], ub), (tri[1][0], ub + 1),
                                (tri[2][0], ub + 2))
                    tri_tex.append(baked)
                    continue
                if slot is None:
                    tri_tex.append(None)
                    continue
                ub = len(uvs)
                for vi, _ in tri:
                    vx, vy, _ = verts[vi]
                    uvs.append((vx / GROUND_TILE, vy / GROUND_TILE))
                tris[-1] = ((tri[0][0], ub), (tri[1][0], ub + 1),
                            (tri[2][0], ub + 2))
                tri_tex.append(slot)

    def slot_for(scene_, name):
        return slot_of(scene_.tex_for(name))

    def add_mesh(geoms, mats, pos, yaw, scale, color):
        ca, sa = math.cos(yaw), math.sin(yaw)
        for si, (pts, idxs, suv) in enumerate(geoms):
            if not idxs:
                continue
            texname = mats[si]["texture"] if si < len(mats) else ""
            slot = slot_for(scene, texname) if texname else None
            b, ub = len(verts), len(uvs)
            for (x, y, z) in pts:
                X = (x * ca - y * sa) * scale
                Y = (x * sa + y * ca) * scale
                verts.append((pos[0] + X, pos[1] + Y, pos[2] + z * scale))
            uvs.extend(suv)
            for i in range(0, len(idxs) - 2, 3):
                a, bb, c = idxs[i], idxs[i + 1], idxs[i + 2]
                if max(a, bb, c) < len(pts):
                    tris.append(((b + a, ub + i), (b + bb, ub + i + 1),
                                 (b + c, ub + i + 2)))
                    rgb.append(color)
                    tri_tex.append(slot)

    # ---- scenery instances ----
    for s, sox, soy in scene.scatter():
        name = s[1] if len(s) > 1 else ""
        if len(s) <= 6 or not isinstance(s[6], (bytes, bytearray)):
            continue
        try:
            inst = zmap_instances(bytes(s[6]))
        except ValueError:
            continue
        if not inst:
            continue
        guid = s[2].split("/")[1].split(".")[0] if len(s) > 2 else ""
        mats, geoms = scene.mesh(guid)
        col = color_for(name)
        # The blob's 4-float header is NOT a size -- it is a per-decoration
        # animation parameter (wind sway), scaled by the frame delta in
        # FUN_004e1e80 and handed to the renderer as a vec4 uniform.  The
        # instance's own scale byte (/16, _DAT_006d1588) is the size, and the
        # models are authored at their natural size: the flower is 0.76 m tall,
        # the shrub 1.73 m, the mushroom 1.31 m.  Applying the header here used
        # to shrink every grass/flower/shrub decoration to 20%.
        for p in inst["instances"]:
            px, py = p["x"] + sox, p["y"] + soy
            if not (xmin <= px <= xmax and ymin <= py <= ymax):
                continue
            zt = scene.height_at(px, py) + p["dz"]
            add_mesh(geoms, mats, (px, py, zt), p["yaw"], p["scale"], col)

    # ---- fixed placements (MAP_MODELDATA) ----
    for m, mox, moy in scene.placements():
        if len(m) < 7:
            continue
        name = m[1]
        spec = scene.reg.get(name, "")
        guid = None
        for part in spec.split(";"):
            for tok in part.split(","):
                tok = tok.strip()
                if tok.startswith("model/"):
                    guid = tok.split("/")[1].split(".")[0]
        if not guid:
            continue
        mats, geoms = scene.mesh(guid)
        x, y = m[2] + mox, m[3] + moy
        zf, size, aspect = m[4], m[5] or 1.0, m[6] or 0.0
        if not (xmin <= x <= xmax and ymin <= y <= ymax):
            continue
        zt = scene.height_at(x, y) + (zf or 0.0)
        add_mesh(geoms, mats, (x, y, zt), math.radians(aspect), size or 1.0,
                 color_for(name))
    return verts, tris, rgb, uvs, texes, tri_tex


def zmap_instances(blob):
    import zmap
    return zmap.decode_instances(blob)


def view_transform(verts, tilt_deg=55.0, yaw_deg=0.0):
    """Camera above the world, looking along +y from -y, up=+z.

    `tilt_deg` is the angle away from the zenith: 12 is a plan view (looking
    straight down), 55 an oblique bird's-eye, 72 a near-horizon shot.  The
    camera therefore looks *down* by (90 - tilt) degrees.

    obj_render.render() maps its input (p_x, p_y, p_z) into view space as
    (p_x, p_z, p_y) -- see obj_render.rot() with yaw=0, pitch=0, up="z" --
    so the input has to be (right, away, up), not (right, up, away):

        p_x = right
        p_y = away  = the component along the view axis (depth)
        p_z = up    = the screen-up component

    The previous version put +z into the depth term and -y into the up term,
    which pointed the camera *up from underneath the terrain*.  The ground was
    then the nearest surface in every direction, so it always won the z-buffer
    and nothing standing on it could ever be drawn: the village buildings were
    invisible, and every tree showed only the underside of its branch canopy
    (it read as a flat brown spider).  Everything that used to look like a
    data problem -- a model that would not draw, a model that was "buried" --
    was this one sign error.

    Returns view-space verts for obj_render.render(yaw=0, pitch=0).
    """
    t = math.radians(tilt_deg)
    ct, st = math.cos(t), math.sin(t)
    cy, sy = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    out = []
    for (x, y, z) in verts:
        # yaw about +z first
        X = x * cy - y * sy
        Y = x * sy + y * cy
        # then tilt away from the zenith: the camera looks down by (90-tilt),
        # so +y runs up the screen and +z comes toward the viewer
        vx = X
        vy = Y * st - z * ct       # away from the camera (depth)
        vz = Y * ct + z * st       # screen-up
        out.append((vx, vy, vz))
    return out


HEIGHT_RECLEN = 0x8212    # 33,298 -- the length the .zmap loader insists on


def world_check():
    """Verify that the six shipped .zmap files are tiles of one world.

    Three claims, each falsifiable:
      * the names decode as grid cells through MAKEMAPID (ix + iy*1000) and fill
        a rectangle with no hole;
      * every shared edge agrees to a few units of 65,530 while the *opposite*
        pairing does not -- so the adjacency direction is measured, not assumed;
      * props land on the stitched surface as well as they do on a single tile.
    """
    import statistics
    w = World("intro")
    bad = 0
    print(f"  grid {w.nx}x{w.ny} at ix {w.ix0}..{w.ix0 + w.nx - 1}, "
          f"iy {w.iy0}..{w.iy0 + w.ny - 1} -> "
          f"{w.world_w:.0f}x{w.world_h:.0f} m")
    if (w.nx, w.ny) != (3, 2) or len(w.tiles) != 6:
        print(f"  unexpected tile count {len(w.tiles)}")
        bad += 1
    worst = 0.0
    cache = {sc.stem: sc for sc, _, _ in w.tiles}

    def tile(stem):
        if stem not in cache:
            cache[stem] = Scene(stem)
        return cache[stem]

    for sc, ox, oy in w.tiles:
        cx, cy = int(round(ox / TILE_M)), int(round(oy / TILE_M))
        pairs = []
        if cx + 1 < w.nx:
            nb = w.cells.get((w.ix0 + cx + 1, w.iy0 + cy))
            if nb:
                pairs.append((f"{sc.stem}/{nb} x", sc.h16[:, -1],
                              tile(nb).h16[:, 0], sc.h16[:, 0],
                              tile(nb).h16[:, -1]))
        if cy + 1 < w.ny:
            nb = w.cells.get((w.ix0 + cx, w.iy0 + cy + 1))
            if nb:
                pairs.append((f"{sc.stem}/{nb} y", sc.h16[-1, :],
                              tile(nb).h16[0, :], sc.h16[0, :],
                              tile(nb).h16[-1, :]))
        for label, a, b, ra, rb in pairs:
            fwd = float(np.abs(a - b).mean())
            rev = float(np.abs(ra - rb).mean())
            worst = max(worst, fwd)
            if fwd > 4.0 or rev < 100.0:
                print(f"  {label}: fwd {fwd:.2f}, reversed {rev:.2f} -- "
                      f"not a clean directional seam")
                bad += 1
    if worst > 4.0:
        print(f"  seams disagree by mean {worst:.1f} of 65,530 units")
        bad += 1
    resid = []
    for sc, ox, oy in w.tiles:
        for e in sc.models:
            try:
                name = e[1] if isinstance(e[1], str) else ""
                x, y, z0 = float(e[2]), float(e[3]), float(e[4])
            except (TypeError, ValueError, IndexError):
                continue
            if not name.strip():
                continue
            resid.append(abs(z0 - w.height_at(x + ox, y + oy)))
    if resid:
        med = statistics.median(resid)
        print(f"  {len(resid)} props, median |z_authored - z_world| = {med:.2f} m")
        if med > 1.5:
            bad += 1
    print(f"world check {'passed' if bad == 0 else 'FAILED'} "
          f"(seam mean |dh16| = {worst:.2f}, {len(resid)} props)")
    return 0 if bad == 0 else 1



def height_check():
    """Verify the MAP_HEIGHTDATA decode against the six shipped maps.

    Four independent claims, all read out of chronotales.exe:
      * the record is 33,298 bytes (0x8212) -- header 12 + 2 x 16,641 + trailer;
      * the tag at +0 is 0xFAB00002 and +4/+8 are the minz/maxz floats;
      * the u32 trailer is the rolling hash h = (h << 1) ^ h16[i];
      * props authored at z = 0 must land on terrain at z ~ 0, which is only
        true for the 16-bit read over the header range.
    """
    import statistics
    stems = sorted(p.stem for p in (ROOT / "extracted/netpak/intro").glob("*.zmap"))
    if not stems:
        print("height check FAILED: no zmaps")
        return 1
    bad = 0
    resid = []
    modes = {}
    for stem in stems:
        sc = Scene(stem)
        rec = sc.terr
        if len(rec) != HEIGHT_RECLEN:
            print(f"  {stem}: record {len(rec)} != {HEIGHT_RECLEN}")
            bad += 1
        tag, mn, mx = struct.unpack_from("<Iff", rec, 0)
        if tag != 0xFAB00002 or mn != -100.0 or mx != 900.0:
            print(f"  {stem}: header {tag:#x} {mn} {mx}")
            bad += 1
        h16 = sc.h16.ravel().tolist()
        h = 0
        for v in h16:
            h = ((h << 1) ^ int(v)) & 0xFFFFFFFF
        tr = struct.unpack_from("<I", rec, len(rec) - 4)[0]
        if h != tr:
            print(f"  {stem}: trailer {tr:#010x} != rolling hash {h:#010x}")
            bad += 1
        if not (sc.zmin <= sc.hf.min() < sc.hf.max() <= sc.zmax):
            print(f"  {stem}: z range {sc.hf.min():.1f}..{sc.hf.max():.1f}")
            bad += 1
        vals, cnt = np.unique(sc.h16, return_counts=True)
        modes[stem] = (int(vals[cnt.argmax()]), int(cnt.max()))
        for e in sc.models:
            try:
                x, y, z0 = float(e[2]), float(e[3]), float(e[4])
            except (TypeError, ValueError, IndexError):
                continue
            if not (0.0 <= x <= 256.0 and 0.0 <= y <= 256.0):
                continue
            resid.append(abs(z0 - sc.height_at(x, y)))
    if resid:
        med = statistics.median(resid)
        print(f"  props: {len(resid)} placements, median |z_authored - z_terrain| "
              f"= {med:.2f} m")
        if med > 1.5:
            print("  props do not sit on the decoded surface")
            bad += 1
    else:
        print("  props: none to test")
        bad += 1
    # The unpainted fill is one value repeated over a third of a tile, and it
    # must land on DEFWORLD_MINZ (-50, def.gbc:114).  This is the one test that
    # separates the header range from every other candidate: over -50..450 the
    # same value decodes to -25.0, and over the old 8-bit read to -26.5.
    fill = max(modes.values(), key=lambda v: v[1])
    zfill = -100.0 + fill[0] * (900.0 - -100.0) / HEIGHT_DIV
    print(f"  fill: h16 {fill[0]} x{fill[1]} -> z {zfill:.2f} m "
          f"(DEFWORLD_MINZ -50)")
    if abs(zfill - -50.0) > 0.5:
        print("  the unpainted fill does not land on DEFWORLD_MINZ")
        bad += 1
    print(f"height check {'passed' if bad == 0 else 'FAILED'} "
          f"({len(stems)} maps, {len(resid)} props)")
    return 0 if bad == 0 else 1


def instance_check():
    """Verify the MAP_WIDGETDATA scenery-instance decode.

    The client's own reader (FUN_004e1960) demands a blob of 16 + 9n bytes
    and then reads count x 9; the decoration record carries the same count.
    Four things are asserted here, all of them consequences of the client code
    rather than of the data:

      * blob length is 16 + 9n and matches the decoration's count field;
      * the 4-float header is uniform -- the client multiplies header[0..1] by
        the frame delta and copies header[2..3] through, so it is an animation
        parameter, NOT a size.  A non-uniform header would mean the decode is
        reading the wrong bytes;
      * every instance lands inside its own 256 m tile;
      * dz stays small, because it is an offset above the terrain surface, not
        an absolute height (a large value would mean the signed i16 is being
        read as unsigned).
    """
    import struct
    stems = sorted(p.stem for p in (ROOT / "extracted/netpak/intro").glob("*.zmap"))
    if not stems:
        print("instance check FAILED: no zmaps")
        return 1
    bad = 0
    ndec = ninst = 0
    dzs = []
    for stem in stems:
        sc = Scene(stem)
        for w in sc.widget:
            if not isinstance(w, (list, tuple)) or len(w) < 7:
                continue
            blob = bytes(w[6])
            ndec += 1
            if len(blob) < 16 or (len(blob) - 16) % 9:
                print(f"  {stem}: blob {len(blob)} is not 16+9n")
                bad += 1
                continue
            n = (len(blob) - 16) // 9
            if n != w[5]:
                print(f"  {stem}: count {w[5]} != blob {n}")
                bad += 1
            hdr = struct.unpack_from("<4f", blob, 0)
            if len(set(hdr)) != 1:
                print(f"  {stem}: header {hdr} is not uniform")
                bad += 1
            for i in range(n):
                o = 16 + 9 * i
                x, y = struct.unpack_from("<HH", blob, o)
                dz, dx, dy = struct.unpack_from("<hbb", blob, o + 4)
                ninst += 1
                dzs.append(dz * 0.01)
                if not (0.0 <= x * 0.01 <= 256.0 and 0.0 <= y * 0.01 <= 256.0):
                    print(f"  {stem}: instance {i} outside the tile "
                          f"({x * 0.01:.2f}, {y * 0.01:.2f})")
                    bad += 1
                if abs(dz * 0.01) > 5.0:
                    print(f"  {stem}: instance {i} dz {dz * 0.01:.2f} m")
                    bad += 1
    if dzs:
        print(f"  {ndec} decorations, {ninst} instances, "
              f"dz {min(dzs):.2f}..{max(dzs):.2f} m")
    else:
        print("  no instances to test")
        bad += 1
    print(f"instance check {'passed' if bad == 0 else 'FAILED'}")
    return 0 if bad == 0 else 1


def camera_check():
    """Verify the scene camera looks *down* at the world from above.

    The composed renders once showed the world from underneath the terrain:
    `view_transform` put +z into the depth term and -y into the screen-up
    term, which pointed the camera up from below the ground.  The terrain was
    then the nearest surface in every direction, so it won the z-buffer
    everywhere and nothing standing on it could be drawn -- the village
    buildings were invisible and every tree showed only the underside of its
    canopy.

    Three properties of a camera above the world are asserted here, all of
    them falsifiable against the old transform:

      * a point 5 m above the ground is NEARER to the camera than the ground
        under it (the old transform made it farther);
      * a point 5 m above the ground is HIGHER on the screen (both
        transforms agree, so this pins the up axis);
      * a point farther away in +y is HIGHER on the screen (the old
        transform put it lower).
    """
    bad = 0
    t = math.radians(55.0)
    ct, st = math.cos(t), math.sin(t)

    def view(p):
        x, y, z = p
        return (x, y * st - z * ct, y * ct + z * st)

    ground = view((10.0, 10.0, 0.0))
    above = view((10.0, 10.0, 5.0))
    farther = view((10.0, 40.0, 0.0))
    # the tuple is (right, depth, up); render() reads p_y as depth, p_z as up
    if not above[1] < ground[1]:
        print(f"  a point 5 m above the ground is not nearer "
              f"(depth {above[1]:.3f} vs {ground[1]:.3f})")
        bad += 1
    if not above[2] > ground[2]:
        print(f"  a point 5 m above the ground is not higher on screen "
              f"(up {above[2]:.3f} vs {ground[2]:.3f})")
        bad += 1
    if not farther[2] > ground[2]:
        print(f"  a point farther away in +y is not higher on screen "
              f"(up {farther[2]:.3f} vs {ground[2]:.3f})")
        bad += 1
    print(f"camera check {'passed' if bad == 0 else 'FAILED'} "
          f"(tilt 55 deg: depth {ground[1]:.3f}->{above[1]:.3f}, "
          f"up {ground[2]:.3f}->{above[2]:.3f})")
    return 0 if bad == 0 else 1


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    stem = args[0] if args else "intro518494"
    xmax = float(args[1]) if len(args) > 1 else 300.0
    close = "--close" in flags
    textured = "--flat" not in flags

    if "--check" in flags:
        rc_h = height_check()
        rc_w = world_check()
        rc_i = instance_check()
        rc_c = camera_check()
        raise SystemExit(rc_h or rc_w or rc_i or rc_c)

    if "--world" in flags:
        raise SystemExit(render_world(textured=textured))

    sc = Scene(stem)

    region = None
    if close:
        # Crop to the built-up area, derived from the scene rather than fixed:
        # a hard-coded (0,0,120,140) window misses every map whose village is
        # not in the corner -- intro517494's props run x 131..225, y 209..249,
        # so that crop rendered empty grass.
        xs, ys = [], []
        for e in sc.models:
            try:
                name = e[1] if isinstance(e[1], str) else ""
                x, y = float(e[2]), float(e[3])
            except (TypeError, ValueError, IndexError):
                continue
            if not name.strip():
                continue        # sentinel rows sit at (0,0) and skew the bbox
            if 0.0 <= x <= 256.0 and 0.0 <= y <= 256.0:
                xs.append(x)
                ys.append(y)
        if not xs:
            # no fixed placements: fall back to the scattered decorations
            for w in sc.widget:
                inst = w.get("instances") if isinstance(w, dict) else None
                for pt in ((inst or {}).get("instances") or []):
                    try:
                        x, y = float(pt["x"]), float(pt["y"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if 0.0 <= x <= 256.0 and 0.0 <= y <= 256.0:
                        xs.append(x)
                        ys.append(y)
        if xs:
            pad = 32.0
            x0, x1 = max(0.0, min(xs) - pad), min(256.0, max(xs) + pad)
            y0, y1 = max(0.0, min(ys) - pad), min(256.0, max(ys) + pad)
            # keep it square-ish so the three cameras frame alike
            w, h = x1 - x0, y1 - y0
            side = max(w, h, 80.0)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            x0, x1 = max(0.0, cx - side / 2), min(256.0, cx + side / 2)
            y0, y1 = max(0.0, cy - side / 2), min(256.0, cy + side / 2)
            region = (x0, y0, x1, y1)
        else:
            region = (0.0, 0.0, min(xmax, 120.0), min(300.0, 140.0))
        print(f"{stem}: close crop x {region[0]:.0f}..{region[2]:.0f}, "
              f"y {region[1]:.0f}..{region[3]:.0f}")
    print(f"{stem}: terrain {sc.cols}x{sc.rows}, "
          f"scenery types {len(sc.widget)}, placements {len(sc.models)}, "
          f"textures {len(sc.texmap)}")
    verts, tris, rgb, uvs, texes, tri_tex = build(
        sc, region=region, textured=textured)
    ntex = sum(1 for t in tri_tex if t is not None)
    print(f"composed: {len(verts)} verts, {len(tris)} tris, "
          f"{len(texes)} textures bound, {ntex}/{len(tris)} tris textured")

    size = 1600 if close else 1400
    zoom = 1.9 if close else 1.55
    tag = ("_tex" if textured else "_flat") + ("_close" if close else "")
    for cam, tilt in (("oblique", 55.0), ("plan", 12.0), ("horizon", 72.0)):
        vv = view_transform(verts, tilt, 0.0)
        pix = render(vv, tris, size, 0.0, 0.0, zoom=zoom, bg=SKY_KEY,
                     up="z", uvs=uvs, texes=texes, tri_tex=tri_tex,
                     tri_rgb=rgb)
        add_sky(pix, size, size)
        out = MAPS / f"{stem}_3d{tag}_{cam}.png"
        write_png(out, size, size, pix)
        print("wrote", out)


def crop_to_content(pix, w, h, margin=18):
    """Crop a sentinel-sky canvas to what was actually drawn -> (pix, w, h).

    A 768 x 512 m world on a square canvas leaves most of the frame empty; the
    rasteriser is square-only (obj_render.render: w = h = size), so framing is
    fixed up here instead of in the shared rasteriser.
    """
    key = bytes(SKY_KEY)
    a = np.frombuffer(bytes(pix), dtype=np.uint8).reshape(h, w, 3)
    mask = ~((a[:, :, 0] == key[0]) & (a[:, :, 1] == key[1])
             & (a[:, :, 2] == key[2]))
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if not len(rows) or not len(cols):
        return pix, w, h
    y0, y1 = max(0, int(rows[0]) - margin), min(h - 1, int(rows[-1]) + margin)
    x0, x1 = max(0, int(cols[0]) - margin), min(w - 1, int(cols[-1]) + margin)
    nw, nh = x1 - x0 + 1, y1 - y0 + 1
    out = bytearray(nw * nh * 3)
    for y in range(nh):
        src = ((y0 + y) * w + x0) * 3
        out[y * nw * 3:(y + 1) * nw * 3] = pix[src:src + nw * 3]
    return out, nw, nh


def render_world(textured=True, res=768, size=2000):
    """Stitch the whole shipped tile grid into one scene and render it."""
    w = World("intro")
    print(f"world: {w.nx} x {w.ny} tiles, ix {w.ix0}..{w.ix0 + w.nx - 1}, "
          f"iy {w.iy0}..{w.iy0 + w.ny - 1} "
          f"-> {w.world_w:.0f} x {w.world_h:.0f} m, grid {w.cols}x{w.rows}")
    print(f"  seam agreement: max |dz| across shared edges = "
          f"{w.seam_max:.3f} m ({w.seam_max * HEIGHT_DIV / (w.zmax - w.zmin):.0f}"
          f" of 65530 units)")
    nprop = sum(len(s.models) for s, _, _ in w.tiles)
    nscat = 0
    for s, _, _ in w.tiles:
        for e in s.widget:
            try:
                nscat += len(zmap_instances(bytes(e[6]))["instances"])
            except (ValueError, IndexError, TypeError):
                pass
    print(f"  placements {nprop}, scenery instances {nscat}")
    verts, tris, rgb, uvs, texes, tri_tex = build(w, textured=textured)
    ntex = sum(1 for t in tri_tex if t is not None)
    print(f"composed: {len(verts)} verts, {len(tris)} tris, "
          f"{len(texes)} textures bound, {ntex}/{len(tris)} tris textured")

    tag = "_tex" if textured else "_flat"
    for cam, tilt, z in (("oblique", 55.0, 1.95), ("plan", 12.0, 1.95),
                         ("horizon", 72.0, 2.05)):
        vv = view_transform(verts, tilt, 0.0)
        pix = render(vv, tris, size, 0.0, 0.0, zoom=z, bg=SKY_KEY, up="z",
                     uvs=uvs, texes=texes, tri_tex=tri_tex, tri_rgb=rgb)
        pix, cw, ch = crop_to_content(pix, size, size)
        add_sky(pix, cw, ch)
        out = MAPS / f"world_{w.copy}_3d{tag}_{cam}.png"
        write_png(out, cw, ch, pix)
        print(f"wrote {out}  ({cw}x{ch})")
    return 0


SKY_KEY = (1, 2, 3)       # sentinel: nothing in the scene is this colour

def add_sky(pix, w, h):
    """Replace the sentinel background with a vertical gradient."""
    top, bot = (34, 40, 56), (11, 12, 16)
    row_col = [bytes((int(top[k] + (bot[k] - top[k]) * (y / max(1, h - 1)))
                      for k in range(3))) for y in range(h)]
    key = bytes(SKY_KEY)
    for y in range(h):
        base = y * w * 3
        for x in range(w):
            i = base + x * 3
            if pix[i] == 1 and pix[i + 1] == 2 and pix[i + 2] == 3:
                pix[i:i + 3] = row_col[y]


def write_png(path, w, h, pix):
    import zlib
    raw = b"".join(b"\x00" + bytes(pix[y * w * 3:(y + 1) * w * 3])
                   for y in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


if __name__ == "__main__":
    main()
