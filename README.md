# Chrono Tales — reverse engineering

Reverse engineering of the 2013 **Chrono Tales** (`时光之刃`-era) Windows client, a
native D3D9 game on the in-house **GBox2** engine. The game ships as two `.gpak`
archives plus a launcher; this repo reconstructs the archive format, the embedded
script language, the entity database, and the mesh/skeleton/texture formats.

The client binary is `gbox/lm_en/chronotales.exe` (4.1 MB, statically linked
zlib 1.1.3/1.1.4, debug paths pointing at `E:\GBox2\src\...`).

**Start here:** [`tools/GPAK_FORMAT.md`](tools/GPAK_FORMAT.md) is the full format
specification and findings log. This README is only an index.
[`tools/SYSFILEVER.md`](tools/SYSFILEVER.md) covers the `.var` asset registry.

## Status

| Piece | State |
|---|:--:|
| `.gpak` container (pages, cipher, CRC, MPQ name hash, wrappers) | solved |
| Filename recovery (hash → path) | 327 / 484 (68%) — the other 157 are 50 netpak varbin containers + 107 sysnet assets |
| `.gbc` script source (uncompiled GBK DSL, 235 files) | solved |
| `varbin` entity database (48 blobs, 19,640 records) | solved (byte-exact) |
| `varbin` blob → game table (42 tables, 19,634 rows exported) | 42 / 48 (37 editors + 5 editor-less) |
| `varbin` map payloads → `.zmap` scenes (6 maps, height + layers + placements) | solved |
| `.53wf` mesh geometry | solved |
| `.cmz` skeleton + bind pose | solved |
| `.cmz` `sbus` skinned geometry + index buffer | solved |
| `SysFileVer.var` asset registry (9,975 records) | solved |
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
```

All paths are relative to the repo root.

## Browsing it

`extracted/explorer/index.html` is one static page over everything above: the 42
tables with their real column names, the 9,975 asset records, the six decoded
maps with height and terrain previews, the mesh and texture galleries, and the
script index.

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
