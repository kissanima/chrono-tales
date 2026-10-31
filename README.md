# Chrono Tales — reverse engineering

Reverse engineering of the 2013 **Chrono Tales** (`时光之刃`-era) Windows client, a
native D3D9 game on the in-house **GBox2** engine. The game ships as two `.gpak`
archives plus a launcher; this repo reconstructs the archive format, the embedded
script language, the entity database, the animation clips, and the
mesh/skeleton/texture formats.

The client binary is `gbox/lm_en/chronotales.exe` (4.1 MB, statically linked
zlib 1.1.3/1.1.4, debug paths pointing at `E:\GBox2\src\...`).

**Start here:** [`tools/GPAK_FORMAT.md`](tools/GPAK_FORMAT.md) is the full format
specification and findings log. This README is only an index.
[`tools/SYSFILEVER.md`](tools/SYSFILEVER.md) covers the `.var` asset registry.

## Status

| Piece | State |
|---|:--:|
| `.gpak` container (pages, cipher, CRC, MPQ name hash, wrappers) | solved |
| Filename recovery (hash → path) | 433 / 484 (89.5%) — the 51 left are all netpak *containers* (42 `varbin` bundles, 8 `BE119407` tables, `SysFileVer.var`); every asset in both archives now has a path |
| `.gbc` script source (uncompiled GBK DSL, 235 files) | solved |
| `varbin` entity database (48 blobs, 19,640 records) | solved (byte-exact) |
| `varbin` blob → game table (42 tables, 19,634 rows exported) | 42 / 48 (37 editors + 5 editor-less) |
| `varbin` map payloads → `.zmap` scenes (6 tiles = one 768 × 512 m world block; height + layers + placements + scenery) | solved |
| **Write** path: `varbin` encoder + `.zmap` writer | solved — 48/48 payloads re-encode byte-exactly; a map round-trips payload-exact and an edited terrain cell survives the trip. Only the deflate stream differs (2013 zlib vs 1.3.1) |
| `.53wf` mesh geometry | solved |
| `.cmz` skeleton + bind pose | solved |
| `.cmz` `sbus` skinned geometry + index buffer | solved |
| `.caf` character animation (Cal3D `CalCoreAnimation`, 5 clips) | solved (byte-exact); model → clip binding solved — 481 NPC models, 366 name an action pack, and the alias cross-check pins the parser (`tools/action_link.py`); only `骑宠_机甲摩托1阶_Z` ships both a skeleton and a pack, so it is the one end-to-end test case |
| `SysFileVer.var` asset registry (9,975 records) | solved |
| Packer manifest (`0xBE119407`-free `.var`, seed 100) | solved — holds the pack's intended file list |
| Texture path convention (`model/<GUID>.fw3`, `tex/<GUID>.<ext>`) | solved |
| Texture GUID → filename | solved (5,672 pairs, `tools/guid_names.csv`) |
| Unified browser over every recovered dataset | `extracted/explorer/index.html` |

## Layout

```
gbox/lm_en/          original game files (not committed - 23 MB of binaries)
extracted/           all derived output (not committed - 88 MB)
  netpak/            archive 1: scripts, entity DB, meshes
  sysnet/            archive 2: UI, tables, textures
  tables/            42 CSVs, 19,634 rows, real column names
  maps/              decoded .zmap scenes (JSON + height/layer PNGs)
  models/            decoded meshes, renders, skin JSON
  gbc_index/         browsable script index
  explorer/          one page over all of the above
work/                scratch (not committed)
tools/               the actual work: analysis + decode scripts
```

## Repo conventions

Only `tools/` is tracked. Everything large or regenerable is gitignored, because
it can all be rebuilt from these scripts and the original archives:

- `extracted/` (88 MB) — output of the pipeline
- `work/`, `gbox/` (23 MB) — scratch and the original game binaries
- `unins000.*`, `gboxwebruner.dll`, `npgboxruner.dll` — installer leftovers
- `.workbuddy-ai/` — agent session state

`.gitattributes` marks `*.gpak *.gbc *.bin *.png *.jpg *.exe *.dll *.dat` binary;
`* text=auto` means commits normalise to LF while the working copy keeps CRLF, so
the CRLF warnings on Windows are expected and harmless.

## Running it

Python 3.12+ with `pefile`, `capstone` (disassembly helpers only) and optional
`Pillow` (textured rendering). Everything else is stdlib.

```bash
# archive
python tools/gpak_extract.py verify  gbox/lm_en/netpak.gpak
python tools/gpak_extract.py wrap    gbox/lm_en/netpak.gpak   # compression tags
python tools/gpak_extract.py extract gbox/lm_en/netpak.gpak extracted

# entity database
python tools/varbin_tree.py check        # decode all 48 payloads, assert byte-exact
python tools/varbin_tree.py show 0580853e
python tools/varbin_identify.py          # blob -> game table
python tools/varbin_tables.py            # -> extracted/tables/*.csv
python tools/map_decode.py               # 6 .zmap maps -> extracted/maps/
python tools/build_explorer.py           # -> extracted/explorer/index.html

# asset manifest -> filenames
python tools/asset_manifest.py check     # both manifests decode byte-exactly
python tools/asset_manifest.py stats     # listed vs shipped, per directory
python tools/asset_manifest.py names     # dry run; --apply merges into names.json
python tools/apply_names.py --apply      # rename the extracted tree in place

# asset registry
python tools/varfile.py stats extracted/netpak/SysFileVer.var
python tools/varfile.py csv   extracted/netpak/SysFileVer.var -o manifest.csv
python tools/varfile.py guids extracted/netpak/SysFileVer.var -o guids.csv
python tools/varfile.py xref  extracted/netpak/SysFileVer.var \
                              extracted/netpak/_manifest.csv --merge tools/names.json

# scripts
python tools/gbc_index.py                # -> extracted/gbc_index/index.html

# meshes
python tools/mesh_vert_decode.py         # .53wf -> OBJ
python tools/cmz.py                      # .cmz skeletons -> JSON + bone OBJ
python tools/sbus_decode.py              # .cmz skin -> raw decode
python tools/sbus_obj.py                 # .cmz skin -> OBJ (with UVs)
python tools/sbus_validate.py --strict   # regression test (exit 1 on failure)
python tools/obj_render.py --obj extracted/models/skin_obj \
                           --sheet out.png --up z --size 320 --cols 3

# animation
python tools/caf_decode.py               # .caf clips -> extracted/models/caf/*.json
python tools/caf_decode.py check         # assert byte-exact, all 5 clips
python tools/caf_validate.py --strict    # rig/structural regression test

# maps
python tools/map_decode.py               # 6 .zmap maps -> extracted/maps/
python tools/zmap.py intro518494         # decode one .zmap scene, all sections

# writing maps back (the only write path for map data)
python tools/varbin_write.py check       # 48/48 payloads re-encode byte-exactly
python tools/zmap_write.py check         # 6/6 maps round-trip: payload + decode
python tools/zmap_write.py demo          # edit one cell, prove exactly one changed
python tools/zmap_write.py edit in.zmap out.zmap --raise-cell 64,64,500

# composed scene renders (terrain + scenery + fixed placements, textured)
python tools/render_scene3d.py intro518494            # full tile, 3 angles
python tools/render_scene3d.py intro518494 --close    # crop to the props
python tools/render_scene3d.py --world                # stitch all six tiles
python tools/render_scene3d.py --check                # 4 invariants, exit 1
python tools/render_scenes.py            # flat top-down map of every scene

# web client (three.js): export the world + actor, then verify the export
python tools/export_web.py               # -> extracted/web/ (glTF + height.js + client)
python tools/export_web.py --check       # 4 invariants, exit 1 (silent-failure guard)

# everything at once
python tools/check_all.py                # 21 invariants, exit 1 on any failure
```

All paths are relative to the repo root.

## Browsing it

`extracted/explorer/index.html` is one static page over everything above: the 42
tables with their real column names, the 9,975 asset records, the six decoded
maps with height and terrain previews (led by `extracted/maps/world_intro_layout.png`,
the six tiles stitched into one 768 × 512 m world), the mesh and texture
galleries, and the script index.

The composed 3D scene renders land next to them and are browsable from the same
Maps view: `extracted/maps/<tile>_3d_tex_{oblique,plan,horizon}.png` per tile,
`--close` for a crop that frames the props, and `world_intro_3d_tex_*.png` for
the stitched block.

Two CSVs in `extracted/tables/` are **derived cross-references**, not decoded
varbin payloads — `action_links.csv` (481 NPC models → action packs) and
`action_packs.csv` (24 player pack references). They are browsable and marked
with a `derived` chip, but they are excluded from the 42-table / 19,634-row
payload count that `check_all.py` pins.

It has no build step, no dependencies and no server — **double-click it**. Data
is delivered as `window.__X = …` through injected `<script>` tags rather than
`fetch()`, because `fetch` is blocked by CORS on `file://` and script tags are
not. Each table gets its own `data/t_<id>.js` so the 5.7 MB of CSV is loaded on
demand instead of up front.

Views are hash-routed, so a specific table is linkable:
`index.html#t/itemtabedit`, `#maps`, `#assets`, `#scripts`.

Rebuild it after any re-export:

```bash
python tools/varbin_tables.py && python tools/build_explorer.py
```

## The web client

`extracted/web/` is a separate, interactive view: the stitched world and the
actor as glTF 2.0, driven by a three.js third-person client (WASD, jump, click
to walk, drag to orbit). `export_web.py` writes it; `height.js` is the world
height sampler the client uses to keep the player on the ground.

**It needs a server, unlike the explorer.** `GLTFLoader` uses `fetch`, which
`file://` blocks, so serve the directory:

```bash
python -m http.server 8731 --directory extracted/web   # then open 127.0.0.1:8731
```

three.js is vendored by hand in `vendor/` and is not produced by any script, so
a fresh `extracted/` needs `vendor/three.module.js`, `vendor/GLTFLoader.js` and
`utils/BufferGeometryUtils.js` (r160; the loader imports the latter by a
relative path, so it must sit at `web/utils/`). `export_web.py --check` reports
any that are missing.

That client fails *silently* — a blank canvas with no visible error — so every
structural property it needs is pinned by `--check`: buffers must carry a
`uri`, 100% of the terrain triangles must face up, `height.js` must be
referenced, and the three vendored modules must exist. To capture a frame
headlessly (the plain `--screenshot` flag fires before an async glTF load
finishes, giving a blank canvas):

```bash
python tools/cdp_shot.py "http://127.0.0.1:8731/index.html" out.png \
    --ready "document.getElementById('stat').textContent==='loaded'"
```

Every figure on the page is read from the generated data (manifests, `_index`
files, `gbc_index/data.json`) rather than hardcoded, so a stale count is
impossible.

## Reading the code

Scripts are named by what they attack. The chain that cracked the `sbus` skin
format is representative of the method used throughout — statistics first to find
the structure, then disassembly to confirm the field roles:

```
sbus_analyze.py   autocorrelation + token distance  -> 20-byte record stride
sbus_dump.py      hexdump the header and payload
sbus_fields.py    score every offset as f32 / u16   -> not raw coordinates
sbus_grid.py      lay the payload out on the grid   -> weight field at +0
sbus_decode.py    the decoder (from VA 0x59A640 in the client)
sbus_census.py    which header fields vary, which are constant
sbus_obj.py       OBJ export
sbus_validate.py  four independent checks that the decode is right
```

Two lessons recorded in `GPAK_FORMAT.md` §6 are worth keeping in mind before
trusting a statistical result:

- A test can give the right answer for the wrong reason. "Triangle edges are ~5×
  shorter than shuffled" was **confounded** — index buffers reference numerically
  adjacent vertices, which shortens edges regardless of shape.
- Characterising a byte stream cannot distinguish *encoded* from *quantised*.
  Uniform byte coverage is exactly what a per-mesh quantiser produces. Only the
  decode routine settles it.
