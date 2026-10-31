#!/usr/bin/env python3
"""zmap.py - parse GBox2 intro scene maps (.zmap).

Container: 4-byte tag 0x602376D7 (client compression tag, see GPAK_FORMAT.md
2.7) + ZIP archive holding one entry `varbin` = plain varbin (no magic), i.e.
the same grammar tools/varfile.py implements via load_varbin().

Value layout (verified against all 6 shipped maps):
    [ [version, terrain_blob, terrain_grid, world_name, ground_textures,
       objects] ]

    version          = 100
    terrain_blob     = 0x32 blob: header (02 00, u16 w, u16 h, u32?) + u8 tiles
    terrain_grid     = [version, n_x, n_y, u16 vertex-y data]
    world_name       = ["w100"]
    ground_textures  = [[tex_name, tex_name, "0"], ...]  (splat layers)
    objects          = [[id, model_name, x, y, z, ...], ...]

Object model_name resolves through SysFileVer.var to a spec string
    modle=,model/<GUID>.fw3;tex=<texname>,tex/<GUID>.<ext>;...
which is the authoritative name->GUID mapping.
"""
import io
import json
import math
import pathlib
import struct
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import varfile  # noqa: E402

TAG = 0x602376D7


def parse(path):
    d = pathlib.Path(path).read_bytes()
    tag = struct.unpack_from("<I", d, 0)[0]
    if tag != TAG:
        raise ValueError(f"{path}: magic {tag:#010x} != {TAG:#010x}")
    z = zipfile.ZipFile(io.BytesIO(d[4:]))
    names = z.namelist()
    if names != ["varbin"]:
        raise ValueError(f"{path}: unexpected zip contents {names}")
    v = varfile.load_varbin(z.read("varbin"))["value"]
    if not (isinstance(v, list) and len(v) == 1 and isinstance(v[0], list)):
        raise ValueError(f"{path}: unexpected varbin shape")
    inner = v[0]
    if len(inner) != 6:
        raise ValueError(f"{path}: expected 6 fields, got {len(inner)}")
    version, terrain, grid, world, ground, objs = inner
    scenery = []
    if isinstance(world, list):
        for e in world[1:]:
            if isinstance(e, list) and len(e) >= 7 and isinstance(e[6], (bytes, bytearray)):
                try:
                    inst = decode_instances(bytes(e[6]))
                except ValueError:
                    inst = None
                scenery.append(dict(type=e[0], name=e[1], model=e[2], tex=e[3],
                                    count=e[5], instances=inst))
    return dict(
        path=str(path),
        version=version,
        world_name=world[0] if isinstance(world, list) and world else None,
        scenery=scenery,
        terrain=dict(
            blob_len=len(terrain), blob_head=bytes(terrain[:12]).hex(),
            grid=dict(n=list(grid[:3]) if isinstance(grid, list) else grid),
        ),
        ground_textures=ground,
        objects=[
            dict(id=o[0], name=o[1], x=o[2], y=o[3], z=o[4], raw=o[5:])
            for o in objs
        ],
    )


def asset_spec(reg_path):
    """Split a SysFileVer 组合 spec into (model_guid, [(tex_name, guid, ext)])."""
    model, texs = None, []
    for part in (reg_path or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("modle="):
            continue
        if part.startswith("model/"):
            model = part.split("/")[1].split(".")[0]
        elif part.startswith("tex="):
            spec = part[4:]
            name, _, rest = spec.partition(",")
            rest = rest.strip()
            if rest.startswith("tex/"):
                guid = rest.split("/")[1].split(".")[0]
                ext = rest.split(".", 1)[1] if "." in rest else ""
                texs.append((name, guid, ext))
    return model, texs


def decode_instances(blob):
    """Decode a scenery instance blob (GObj3dWidgetBsp::SetAsVar format).

    Confirmed by reading the client's own reader, writer, snap and renderer:

      reader  FUN_004e1960  (0x4e1960)  -- reads the blob
      writer  FUN_004e16a0  (0x4e16a0)  -- writes it back out
      snap    FUN_004e1660  (0x4e1660)  -- z = terrain(x,y) + dz
      render  FUN_004e1ed0  (0x4e1ed0)  -- size and facing

    Layout: a 16-byte header of four float32, then `count` records of 9 bytes:

      u16  x      * 0.01    -> metres      (_DAT_006d0408 = 0.01)
      u16  y      * 0.01    -> metres
      i16  dz     * 0.01    -> metres above the terrain surface
      i8   dir_x  / 127                 (_DAT_006d158c = 1/127)
      i8   dir_y  / 127
      u8   scale  * 0.0625              (_DAT_006d1588 = 1/16)

    `dz` is **signed** -- the reader does `(int)(short)puVar10[2]` -- and the
    snap then makes the final z absolute: `z = terrain(x,y) + dz`.

    The 4-float header is **not** a size.  FUN_004e1e80 multiplies its first
    two floats by the frame delta (FUN_0044c560 returns elapsed milliseconds)
    and copies the other two through unchanged, and the result is handed to
    the renderer as a vec4 uniform (FUN_004bdf40).  It is a per-decoration
    *animation* parameter -- the wind sway of grass and foliage.  All nine
    shipped decorations use either all-zero or all-0.2, and the same asset
    (奇幻灌木001) carries 0.2 in one tile and 0.0 in the next, so it is
    authored per placement group, not per asset.

    A version-1 record layout also exists in the client (14-byte stride, no
    dz, dz defaults to -0.01); only version 2 (9-byte stride) ships.
    """
    if len(blob) < 16:
        raise ValueError("blob too short")
    hdr = struct.unpack_from("<4f", blob, 0)
    n = (len(blob) - 16) // 9
    if len(blob) != 16 + n * 9:
        raise ValueError(f"blob not 16+9n: {len(blob)}")
    out = []
    for i in range(n):
        o = 16 + 9 * i
        x, y = struct.unpack_from("<HH", blob, o)
        dz, dx_, dy_ = struct.unpack_from("<hbb", blob, o + 4)
        scale = blob[o + 8]
        out.append(dict(
            x=x * 0.01, y=y * 0.01, dz=dz * 0.01,
            dir=(dx_ / 127.0, dy_ / 127.0),
            yaw=math.atan2(dx_, dy_) if (dx_ or dy_) else 0.0,
            scale=scale * 0.0625,
        ))
    return dict(anim_params=hdr, instances=out)


def main():
    scenes = {}
    for f in sorted(pathlib.Path("extracted/netpak/intro").glob("*.zmap")):
        s = parse(f)
        scenes[f.stem] = s
        print(f"{f.name}: world={s['world_name']} ground={len(s['ground_textures'])} "
              f"objects={len(s['objects'])} scenery={len(s['scenery'])}")
    out = pathlib.Path("extracted/maps/scenes")
    out.mkdir(parents=True, exist_ok=True)
    (out / "intro_scenes.json").write_text(
        json.dumps(scenes, ensure_ascii=False, indent=1, default=repr),
        encoding="utf-8")
    print(f"wrote {out/'intro_scenes.json'} ({len(scenes)} scenes)")


if __name__ == "__main__":
    main()
