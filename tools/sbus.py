"""Decode .cmz MESH-chunk sbus records (Cal3D CoreSubmesh streams).

Format reverse-engineered from chronotales.exe loadCoreSubmesh (FUN_0059a640,
decompiled with Ghidra; source path E:\\GBox2\\src\\client\\cal3d\\src\\coremesh.cpp).

MESH chunk layout:
    u32 nParts
    per part:
        u32 nameLen, GBK name
        u32 nSubmeshes
        nSubmeshes x sbus stream (see parse_sbus)

sbus stream:
    u32  'sbus'
    u32  submeshIndex            (-> CoreSubmesh+0x0c)
    u32  vertexCount0
    if vertexCount0 == 0:        (versioned format)
        u8   version (== 1)
        u32  vertexCount
    else:
        vertexCount = vertexCount0
    u32  faceCount
    u32  f4                      (-> CoreSubmesh+0x10, observed 0)
    u32  perVertexExtra          (if > 0, 4 bytes skipped per vertex)
    u32  nUVSets
    u8   inflU16 (versioned only; else 0)   - nonzero => per-vertex influence
                                             counts are u16 instead of u8
    f32  min_x, scale_x, min_y, scale_y, min_z, scale_z   (cm; loader x 0.01)
    per vertex:
        3 x u16 position   (pos = u16*scale + min, after x0.01)
        3 x u16 normal     (norm = u16/32767 - 1.0)
        nUV x 2 x u16 uv
        u8|u16 nInfl
        nInfl x { u8 boneId, u16 weight }   (weight = u16 / 10000)
        u32 extra if perVertexExtra
    per face:
        vertexCount < 1024: u32 packed (i0 & 0x3ff, i1 = >>10 & 0x3ff, i2 = >>20)
        else:               3 x u16
"""
import struct
import sys

POS_UNIT = 0.01          # DAT_006d0738
NORM_SCALE = 3.0518509447574615e-05   # DAT_006d073c = 1/32767
NORM_OFF = 1.0           # DAT_006cec4c
WEIGHT_UNIT = 1e-4       # DAT_006d457c


class Reader:
    def __init__(self, buf, off=0, end=None):
        self.b = buf
        self.o = off
        self.end = len(buf) if end is None else end

    def need(self, n):
        if self.o + n > self.end:
            raise ValueError("out of data at %#x (need %d)" % (self.o, n))

    def u8(self):
        self.need(1)
        v = self.b[self.o]
        self.o += 1
        return v

    def u16(self):
        self.need(2)
        v = struct.unpack_from("<H", self.b, self.o)[0]
        self.o += 2
        return v

    def u32(self):
        self.need(4)
        v = struct.unpack_from("<I", self.b, self.o)[0]
        self.o += 4
        return v

    def f32(self):
        self.need(4)
        v = struct.unpack_from("<f", self.b, self.o)[0]
        self.o += 4
        return v

    def raw(self, n):
        self.need(n)
        v = self.b[self.o:self.o + n]
        self.o += n
        return v


def parse_sbus(r):
    """Parse one sbus (CoreSubmesh) stream from Reader r. Returns dict."""
    magic = r.raw(4)
    if magic != b"sbus":
        raise ValueError("bad magic %r at %#x" % (magic, r.o - 4))
    sm = {}
    sm["index"] = r.u32()
    vc0 = r.u32()
    versioned = vc0 == 0
    sm["versioned"] = versioned
    if versioned:
        sm["version"] = r.u8()
        if sm["version"] != 1:
            raise ValueError("loadCoreSubmesh ver err: %d" % sm["version"])
        vc = r.u32()
    else:
        vc = vc0
    sm["vertexCount"] = vc
    sm["faceCount"] = r.u32()
    sm["f4"] = r.u32()
    sm["perVertexExtra"] = r.u32()
    sm["nUV"] = r.u32()
    if versioned:
        sm["inflU16"] = r.u8() != 0
    else:
        sm["inflU16"] = False
    mn = [0.0] * 3
    sc = [0.0] * 3
    for k in range(3):          # interleaved: (offset, scale) per axis
        mn[k] = r.f32() * POS_UNIT
        sc[k] = r.f32() * POS_UNIT
    sm["min"] = mn
    sm["scale"] = sc

    verts = []
    for _ in range(vc):
        pos = [(r.u16() * sc[k] + mn[k]) for k in range(3)]
        nrm = [r.u16() * NORM_SCALE - NORM_OFF for _ in range(3)]
        uvs = [(r.u16(), r.u16()) for _ in range(sm["nUV"])]
        ninfl = r.u16() if sm["inflU16"] else r.u8()
        infl = []
        for _ in range(ninfl):
            bone = r.u8()
            w = r.u16() * WEIGHT_UNIT
            infl.append((bone, w))
        if sm["perVertexExtra"]:
            r.raw(4)
        verts.append((pos, nrm, uvs, infl))
    sm["vertices"] = verts

    packed = vc < 0x400
    faces = []
    for _ in range(sm["faceCount"]):
        if packed:
            v = r.u32()
            faces.append((v & 0x3ff, (v >> 10) & 0x3ff, (v >> 20) & 0x3ff))
        else:
            faces.append((r.u16(), r.u16(), r.u16()))
    sm["faces"] = faces
    return sm


def gbk(b):
    return b.split(b"\x00")[0].decode("gbk", "replace")


def parse_mesh_chunk(d, off, end):
    """Parse the MESH (HSEM) chunk body: parts of named submesh groups."""
    r = Reader(d, off, end)
    n = r.u32()
    parts = []
    for _ in range(n):
        ln = r.u32()
        name = gbk(r.raw(ln))
        nsub = r.u32()
        subs = [parse_sbus(r) for _ in range(nsub)]
        parts.append(dict(name=name, submeshes=subs))
    return parts, r.o


def parse_cmz(path):
    d = open(path, "rb").read()
    h = d.find(b"HSEM")
    end = d.find(b"1PAM", h)
    if end < 0:
        end = len(d)
    return parse_mesh_chunk(d, h + 4, end)


def report(path, bone_limit):
    parts, consumed = parse_cmz(path)
    ok = True
    total_verts = 0
    for p in parts:
        for sm in p["submeshes"]:
            bad_bone = 0
            wsum_err = 0.0
            for pos, nrm, uvs, infl in sm["vertices"]:
                s = sum(w for _, w in infl)
                if infl and abs(s - 1.0) > 0.01:
                    wsum_err += 1
                for b, w in infl:
                    if b >= bone_limit:
                        bad_bone += 1
            total_verts += sm["vertexCount"]
            print("  part %-14s sub %d: v=%-5d f=%-5d uv=%d inflU16=%d extra=%d "
                  "badBones=%d badWsum=%d" % (
                      p["name"], sm["index"], sm["vertexCount"], sm["faceCount"],
                      sm["nUV"], sm["inflU16"], sm["perVertexExtra"],
                      bad_bone, wsum_err))
            if bad_bone:
                ok = False
    return consumed, total_verts, ok


if __name__ == "__main__":
    import glob
    import json
    files = sys.argv[1:] or sorted(glob.glob("extracted/netpak/model/*.cmz"))
    for f in files:
        d = json.load(open(f.replace("netpak/model", "models/cmz").replace(
            "extracted/netpak/model", "extracted/models/cmz")
            if False else f, "rb")) if False else None
        # bone count from cmz.py output json, fallback guess
        import os
        stem = os.path.basename(f)[:-4]
        jp = "extracted/models/cmz/%s.json" % stem
        blimit = 256
        if os.path.exists(jp):
            j = json.load(open(jp))
            blimit = len(j["bones"])
        try:
            consumed, nv, ok = report(f, blimit)
            size = os.path.getsize(f)
            print("%s: bones=%d verts=%d walk_end=%#x filesize_related=ok=%s"
                  % (stem, blimit, nv, consumed, ok))
        except Exception as e:
            print("%s: FAILED: %s" % (stem, e))
