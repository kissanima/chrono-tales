# PORT_LOG

Handoff log. Newest entry on top. Keep each entry under 20 lines.

---

## 2026-10-31 (14) — every open item closed; the last three TODOs were stale

**Status:** DONE

**What was done:** Went after the three "Next Steps" left in (5) and (12) and
found that all three had already been overtaken by later work. The log now has
no outstanding items.
* **"Read the `effect/` particle INIs"** — done in entry (7). §4.10 documents
  the whole grammar (`//Particle 1.00 for GBoxII`, `:Atom`/`:Ribbon` elements,
  nested `:Emitter`/`:GWell`/`:ECircle`/`:Vortex`/`:Roto`/`:Gravity`/
  `:Ribbon`/`:R_Twist` behaviours, and the four value forms including
  piecewise-linear keyframe curves). `tools/effect_ini.py` parses all three
  shipped files (all `m_sName='Test'`, i.e. engine demos) into
  `extracted/netpak/effect/_parsed.json`.
* **"Pose the 3 static-prop `.cmz` with skeletons but no packs"** — **there
  are none.** Cross-referencing all 9 shipped `.cmz` against SysFileVer: only
  `b056501a` is a mount (机甲摩托, the one with a pack); the other 8 are all
  humanoid **equipment** (战士7档 shoes/greaves/bracers/gloves/armour, 发型01,
  脸型09, 波点泳装 shoes). Every one already has exported geometry in
  `extracted/models/skin_obj/` (24 OBJs) and appears in `skin_sheet.png`.
  The world's actual props — buildings, trees, rocks, decorations — are
  `.fw3` meshes with no skeleton, which is why none of them have packs.
* **"The 16,641-byte noise block" and "ground blending"** — closed in (13).

**State of the project:** every format in the shipped archives is decoded and
invariant-tested (18/18). The only unknowns left are two `sbus` header fields
(`+21`, `+25`) that are 0 in all 15 shipped parts and cannot be recovered —
the `.cmz` writer is an offline tool that is not shipped — and the 1,777
`effect/` paths that `SysFileVer.var` registers but the retail cut does not
ship. Both are documented as such, not left open.

**Next Steps:** none outstanding. The obvious extensions are authoring, not
analysis: rebuild a `.zmap`/`.gpak` the client accepts, or walk an NPC's spec
end-to-end into a rendered, animated scene.

**Blockers:** None.

---

## 2026-10-31 (13) — stale map TODOs closed; explorer Maps view gains the 3D renders

**Status:** DONE

**What was done:**
* **The "16,641-byte noise block after the height field" never existed.**
  Walking every `bytes` blob in all six varbins finds exactly three kinds:
  the height record (33,298 = 12 + 2·16,641 + 4), the layer grid (393,216 =
  256·512 RGB) and the scenery instance blobs. The "noise block" was the
  height record's **second 16,641-byte plane** — the low bytes — which is what
  the two-plane discovery already explained. Stale TODO removed.
* **Ground blending is already a real weighted blend**, not "dominant channel
  only": `blend_ground()` sums each layer's tiled texture times its
  normalised weight (engine semantics per `FUN_004edb40`, top half weights
  layers 0–2, bottom half 3–5). Evidence: 36,036 distinct colours in a 1/16
  sample of the baked atlas; a dominant-channel pick would produce a handful.
* **Explorer Maps view now shows the composed 3D renders** — an
  oblique/plan/horizon triple per tile plus the stitched world, each linking
  to the full-size PNG. This needed the tile **stem** (`intro518494`), not the
  archive **hash** (`4514d98f`) the relief/layer previews are keyed by; the
  two keys come from different stages and mixing them up is now caught.
* **`build_explorer.py` validates its own image references** — `map imgs 31
  referenced, missing 0` walks every PNG the Maps view can request and fails
  the run on a dangling path. Verified by injection (renaming one render →
  0/1, restoring → 1/1).
* **Fixed a count drift the explorer had picked up:** `action_link.py`'s two
  CSVs in `tables/` made it report 44 tables / 20,139 rows, inflating the
  varbin payload count that `check_all.py` pins at 42 / 19,634. They are now
  tagged `derived`, excluded from the payload stats, and shown with a
  `derived` chip in the UI. `explorer` invariant strengthened to pin both
  figures. Suite **18/18**.

**Next Steps:** the 3 static-prop `.cmz` with skeletons but no packs; the
1,777 absent `effect/` files (3 shipped, plaintext INI).

**Blockers:** None.

---

## 2026-10-31 (12) — MAP_WIDGETDATA closed from the client; the scene camera was upside-down

**Status:** DONE

**What was done:**
* **Scenery instances solved from the client, not inferred.** The instance
  blob is a 16-byte float4 header plus `count` × 9-byte records: `x,y` u16 cm
  (×0.01, `_DAT_006d0408`), `dz` **signed** i16 cm, `dir_x/dir_y` signed i8
  ÷127 (`_DAT_006d158c`), `scale` u8 ÷16 (`_DAT_006d1588`); final
  `z = terrain(x,y) + dz` (snap `FUN_004e1660`, reader `FUN_004e1960`).
  **The 4-float header is a wind/animation parameter, not a size** —
  `FUN_004e1e80` multiplies its first two floats by the frame delta from
  `FUN_0044c560` (elapsed ms) and hands the result to the renderer as a vec4
  uniform (`FUN_004bdf40`, `+0x74 = delta*header[0]*0.001`). Reading it as a
  scale shrank every grass/flower/shrub to 20%. Documented in §4.7; the
  18 `FIXMODEL_*` fields are there too (`Z` is terrain-relative,
  `ASPECT` in degrees).
* **Found and fixed the real reason the village buildings were invisible.**
  Not a format problem: `view_transform()` put +z into the depth term and −y
  into the screen-up term, pointing the camera **up from underneath the
  terrain**. The ground was then the nearest surface in every direction, so
  it won the z-buffer everywhere — nothing standing on it could be drawn, and
  every tree showed only the underside of its branch canopy (it read as a
  flat brown spider). Diagnosis by bisection: no-depth render still empty →
  not the z-buffer; building alone through `build()` painted 490 px → not
  the geometry; a two-stick camera test made the ground a solid wall.
  All six tiles + the world re-rendered: trees upright, houses and roofs
  visible under their canopies, roads continuous, terrain relief correct.
* **`建筑02` + `建筑201` are one treehouse**, placed at identical x/y/z/size/
  aspect: `E2297D8E` is the house + 29 m trunk + vines, `E753DD7C` is the
  53 m leaf canopy (z 10–30.5 m) over it. Same pairing for
  `新手村树1` / `新手村树101`.
* New invariants: `camera_check` (a point above the ground must be nearer
  *and* higher on screen), `instance_check` promoted into `check_all.py`.
  Suite is **18/18**.

**Next Steps:** the 3 static-prop `.cmz` with skeletons but no packs; ground
blending and the 16,641-byte noise block after the height field are still
unexplained.

**Blockers:** None.

---

## 2026-10-31 (11) — the z scale is proven at instruction level; the fill is -50

**Status:** DONE

**What was done:**
* Went back to `0x4ee70f` because the decode listing in §4.7 read as
  `z = h16 * (1/65530) + minz`, which is dimensionally wrong — it would give a
  **1 metre** total range. The multiplier is precomputed:
  `fsub [esp+0x30]` (ST0 = maxz, `[esp+0x30]` = minz) → `fmul 1/65530` →
  `[esp+0x20] = (maxz-minz)/65530`, and the `fadd` base `[esp+0x38]` is minz.
  So `z = minz + h16*(maxz-minz)/65530` is exact, and both header floats are
  live. Same routine scales x/y: `[esi+0x130] * 1.00001` = 2 m/cell.
* **A third of a tile is an unpainted fill**: `h16 = 3273` covers 39% of
  `intro518493` and 19% of `intro518495` (next value: 86 cells vs 6,458) and
  decodes to **-50.05 m**, i.e. 5 cm under `DEFWORLD_MINZ -50`. That is what the
  "crater" in 518493 is — a flat floor with a painted mound on it.
* New invariant inside `height-decode`: the most common h16 in the six maps must
  land within 0.5 m of -50. This is the one test that *discriminates* the header
  range from the alternatives (over -50..450 the same value is -25.0; over the
  old 8-bit read, -26.5). **16/16.**
* `tools/render_scenes.py --world` → `world_intro_layout.png` (1152x768): the
  stitched atlas with a hillshade of the stitched heights, tile outlines,
  `MAKEMAPID` labels, scatter and placements. The hillshade normalises the
  gradient by its 90th percentile — a fixed gain either flattened the plain or
  crushed the hills to black.

**Next Steps:** none on terrain. The layout is the first view that shows the
village straddling a tile boundary (props at y 210..249 north of it, 24..214
south of it).

**Blockers:** None.

---

## 2026-10-31 (10) — a .zmap is a 256 m TILE, and the six stitch into one world

**Status:** DONE

**What was done:**
* **"Six copies of one scene" was wrong.** `def.gbc:110` defines
  `MAKEMAPID(ix,iy) = ix + iy*1000`, and `world.gbc::CheckWorldCenterPos` streams
  a `(2*maprs+1)^2` neighbourhood of `copyName + MAKEMAPID(ix,iy)` around the
  player 600 m out — so the file stem is the **grid cell**. `intro517493` is
  ix=493, iy=517, and the six tiles are a **3×2 block = 768 × 512 m** of one
  world. Every file is named `w100` inside because they are the same map.
* **Adjacency measured, not assumed:** mean |Δh16| across shared edges is
  0.01–1.01 units of 65,530; the *reversed* pairing of the same edges is
  1,209–4,770. That 1,000× gap pins ix→+x, iy→+y.
* New `World` class + `--world` (247k tris, `world_intro_3d_tex_*`). Ground is
  blended **per tile** — the tiles have different surface sets (slot 1 is `沙地`
  vs `地表_路面007`), so a global blend is impossible; the 1-cell ground step at a
  boundary is authored data, not a stitching bug.
* **Real bug found on the way:** `obj_render` samples `ty = (1-v)*h`, so the old
  `v = y/world` mirrored the whole ground field about its middle row. Invisible
  on plausible grass, obvious once six tiles must join. Now `1 - y/world_h`;
  all six single-tile renders regenerated.
* New `world-tiling` invariant (grid shape, directional seams, props-on-surface);
  suite **16/16**.

**Next Steps:** the grid is 3×2 because that is all that ships; no other archive
entry is a tile.

**Blockers:** None.

---

## 2026-10-31 (9) — MAP_HEIGHTDATA is 16-bit; the z range is settled

**Status:** DONE

**What was done:**
* The 129×258 block is **one 16-bit grid**: bytes 0..16,640 are the high byte,
  16,641..33,281 the low byte, `z = minz + h16*(maxz-minz)/65530` with minz/maxz
  from the header. Found by chasing `SetHeightData minz>maxz err` to `0x4ee4f9`,
  the `.zmap` record reader — it demands a length of exactly **0x8212 = 33,298**
  = 12 + 2·16,641 + 4, which every shipped record is.
* The reciprocal `65.53` (`0x6d17d8`) is in the **saver** (`0x4ee45a`), which
  writes `dl=AH` to plane 1 and `al` to plane 2. 65.53×1000 = 65,530 = the
  loader's divisor. So the header floats are live: span 1000 m, step **15.3 mm**,
  not the 3.9 m the 8-bit read implied.
* The u32 trailer is a rolling hash `h = (h<<1) ^ h16[i]`, verified **6/6**.
* Three §4.7 claims reversed: plane 2 is the low byte (hence 0..255, zero
  correlation, the 20% plateau); the tag *is* compared; and `-50..450` only
  looked right because it crosses zero exactly where the village sits.
* Data confirms it: `精灵小镇_木箱_Z` authored at (137.93, 211.19, z=0.000) gets
  `height_at` = **+0.060 m**; 39 props, median residual 1.21 m. Relief is now
  72..124 m/map (was 36..62). The tent blur was hiding the decode error, not the
  data, and defaults to 0. New check `height-decode`; **15/15**.
* Also fixed the hard-coded `--close` crop `(0,0,120,140)`, which rendered empty
  grass for `intro517494` (village at x 131..225, y 209..249); it now derives
  from the placement bbox, skipping the blank sentinel rows at (0,0).

**Next Steps:** none on terrain. The teal foliage on the village trees is the
authored texture (`84364C81.png` averages RGB 30,176,121), not a binding bug.

**Blockers:** None.

---

## 2026-10-31 (8) — the layer map holds SIX layers, not three

**Status:** DONE

**What was done:**
* **The roads were being thrown away.** `MAP_LAYERDATA` is 256×512 = two 256-row
  halves of the *same* 256 m world. The previous entry called the second half
  "35% painted, i.e. padding". Wrong: `FUN_004edb40` reads a cell and the cell
  256 rows below it together, sums all six bytes and rescales them to `0xff` —
  so **top R/G/B weight layers 0/1/2 and bottom R/G/B weight layers 3/4/5**,
  six weights for the six `MAP_DATAEX` layers.
* Consequence: layer 3 is `地表_路面002` and its 450 cells in `intro518494` *are*
  the road network. `bake_ground()` now blends all six layers, and the renders
  show a proper dirt road plus a stone road branching across the map instead of
  vague brown gradients. Bake reports `painted 100%` (was 67%).
* `TERR_DOWNSAMPLE` 2 → 1: quads are now 2 m, matching the height grid's native
  spacing, so the terrain no longer decimates its own data (40k → 65k tris,
  ~50 s/scene).
* Found `FUN_004edb40` by scanning `E8 rel32` call targets for calls into the
  saver cluster (0x4ee1xx-0x4ee6xx) — Ghidra never disassembled 0x4ee403, so
  xref-based lookup failed but a raw call scan did not.

**Next Steps:** the z range is still open (see (7)); the second 129×129 height
plane is still unidentified. Both need the terrain loader.

**Blockers:** None.

---

## 2026-10-31 (7) — terrain shading fixed; header confirmed write-only

**Status:** DONE

**What was done:**
* **The blocky ground was a 2 m staircase, not a texture problem.** With a
  500 m z range one height byte is ~2 m, so a "gentle" plain arrives as a
  staircase; every 4 m render quad then gets a wildly different slope and flat
  shading paints the map as a quilt of dark rectangles. Both renderers now
  tent-blur the field twice (`Scene._smooth`, `render_scenes.smooth`) — that
  recovers the continuous surface rather than inventing relief.
* `terrain_color()`'s 5-band palette is now only the *untextured* fallback;
  textured terrain gets `height_tint()` (near-white, gentle height darkening),
  because the palette was being multiplied into the ground texture as hard
  colour steps.
* **Header is write-only.** `0xFAB00002` occurs exactly twice in the exe, both
  as stores in one routine at `0x4ee3xx`: `mov [esi],0xFAB00002` /
  `mov [esi],-100.0` / `mov [esi],900.0`, with `mov [esp+0x18],0x4101` (16641 =
  129²) nearby — the map *saver*. Nothing compares against the tag, so the
  loader does not validate the header and probably ignores the two floats. That
  is inference, not proof; the loader is still unfound.
* New Ghidra helpers: `tools/ghidra/decomp_at.java` (function containing/nearest
  preceding a VA) and `decomp_here.java` (disassemble + create function at a VA).
  `find_const.java` does byte-pattern → xref → decompile.

**Next Steps:** find the terrain loader to close the z range (candidates:
callers of the writer at `0x4ee3xx`; `FUN_004ee180` just before it builds a
4-bytes-per-cell splat buffer). Then the second 129×129 plane.

**Blockers:** None.

---

## 2026-10-31 (6) — ground blending baked; the second height plane re-examined

**Status:** DONE

**What was done:**
* **Ground is now really blended.** `MAP_LAYERDATA`'s RGB channels are *weights*
  for ground layers 0/1/2, not an index — the map is 67% painted with 22,370
  distinct colours because the edges are anti-aliased. Picking the dominant
  channel per quad (the old code) collapsed that to 6 m stair-steps because the
  rasteriser binds one texture per triangle. New `Scene.bake_ground()` resamples
  the splat to 2048² over the 256 m world, tiles each layer at `GROUND_TILE`
  (48 px per 6 m tile, still finer than the 0.18 m render pixel) and composites
  ΣwᵢLᵢ/Σwᵢ with layer 0 where the splat is `000000`. Roads now have soft edges.
  All 18 scene renders regenerated.
* **The 16,641-byte tail is NOT uninitialised memory** (previous entry said it
  was). As a 129×129 image it is coherent relief; `intro518495` has a plateau at
  value 201 covering 20% of the grid, which uniform noise cannot produce. It is
  exactly 129², spans 0..255 in all six maps, has **no seam continuity** with
  the height patch (mean |A[:,128]−B[:,0]| = 102..142), and correlates with
  nothing: −0.08..−0.38 vs height, 0.06..0.11 vs its gradients, ≈0 vs the layer
  channels. Likely a 2-channel normal map split across planes, but unidentified.
* **Header re-read:** `u32 0xFAB00002` + two floats **−100.0 / 900.0**, identical
  in all six maps. The old `u16 2 / u32 64176 / u32 49864` reading was the same
  bytes misaligned by two. Whether these bound z is now flagged in §4.7 as the
  one open question (rendering with them doubles relief and tilts the village);
  neither pair exists as a binary constant, so Ghidra cannot settle it quickly.

**Next Steps:** locate the terrain loader to settle the z range and the second
plane. Entry points already found: `0x539731` / `0x53997b` reference the zmap
magic `0x602376D7`; `FUN_004ca0d0` builds a packed 2-channel texel buffer.

**Blockers:** None.

---

## 2026-10-31 (5) — textured animation renders; three camera/index bugs fixed

**Status:** DONE

**What was done:** The one shipped actor now renders **textured**, and getting
there exposed three silent bugs rather than any unknown format.
* **Textures were never missing.** The `.cmz` has no material table, but the
  SysFileVer spec does: `tex=机甲摩托_轮子,tex/3F736FBC.jpg;…` x7. Skin parts
  are named after those materials; the four `轮子` entries are the shared
  material for 前轮/后轮. **5/5 parts bind, 2,932/2,932 faces textured.**
* **`render()` wants radians.** `render_anim.py` passed degrees, so `cos(90.0)`
  was `cos(90 rad)` and the bike sat rolled 27° with wheels off the ground.
  Diagnosed arithmetically: wheel centres at world z 0.56/0.74 projected to
  screen y +0.57/−0.63.
* **`zoom` is a half-extent factor** (`scale = size*0.5*zoom/ext`), so 1.0 fills
  half the canvas. Added opt-in `fit="screen"` (projected silhouette, not 3D
  bbox) and `frame=(ox,oy,ext)` to pin one camera across all frames.
* **Part base was a face count, not a vertex offset** — parts 1–4 pointed 74+
  vertices too far. `caf_frames.py` always did `base += nv`, which is why the
  web viewer was right and the rasteriser was not.
* Strips are now 460 px cells and there is a new 3/4 `anim/hero.png`
  contact sheet (yaw 132/pitch 14, one frame per clip) — the broadside strip
  hides the front three-quarter form.
* New `render_anim.py --check` (2 s) -> new `check_all` invariant
  `anim-render`; suite is **14/14**. Spec §4.9 documents all of the above.

**Next Steps:**
1. Untouched: ground-layer blending (dominant channel only, no real blend), the
   16,641-byte noise block after the height field, 1,777 absent `effect/` files.
2. Optional: the 3 static-prop `.cmz` with skeletons but no packs could be
   posed to their bind frame for a prop sheet — no animation, so low value.

**Blockers:** None.

---

## 2026-10-31 (4) — animation chain CLOSED; first animated renders

**Status:** DONE (stopped mid-polish on the render camera, see Next Steps)

**What was done:** Resolved the `系统名 → model` link that had been open since
2026-10-20, and it was never in `ziptabedit`.
* **`ziptabedit` is player-only** — 12 rows, six per gender, keyed
  `模板|人类男` / `模板|人类女`; a player has no single model, so it cannot name one.
  Its `原始动作包`/`附加动作包` are themselves SysFileVer keys: all 24 references
  (14 distinct names, incl. all 16 `标准动作`) resolve to `action/<guid>.zip`.
* **NPC/monster/mount path is inline in SysFileVer**: `npctabedit.模型` →
  `modle=,model/X.cmz;tex=n,tex/Y;…;action=,action/Z.zip`. One row carries
  skeleton *and* clip pack. `_D` names are aliases pointing at the zip alone.
* **Validation:** 250 `_D`/`_Z` alias pairs, **0 disagreements** — that is what
  proves the spec parser, since a wrong parse cannot agree 250 times. New
  `check_all` invariant `action-links`; suite is now **13/13**.
* **Coverage:** 481 distinct NPC models; 479 have a `.cmz`; 366 have a pack
  (115 are static props). **Exactly 1 skeleton and 1 pack are shipped**:
  `骑宠_机甲摩托1阶_Z` → `model/B056501A.cmz` + `action/E41C7990.zip`, whose five
  members are the five loose `.caf`. So the retail client contains **one**
  complete animated actor — the mech mount — and the rest is stripped.
* New `tools/action_link.py` → `extracted/tables/action_links.csv` (481 rows),
  `action_packs.csv` (24 rows); explorer picks both up as `t_action_links.js`.
* New `tools/render_anim.py`: poses that rig from a clip and rasterises it
  (rigid skin, bone-local mesh from `skin/_skin.json`, per-frame world
  transforms per §4.9) → `extracted/models/anim/<clip>_strip.png` (5 strips) +
  `<clip>_fNN.png` (5 stills). Motion verified numerically: max vertex travel
  vs frame 0 is **1.195 m** (run), 0.357 m (jumpdown), 0.054 m (stand01, an
  idle — correctly small). Rig is **Z-up**, 43 bones, 4191 verts, 2932 faces;
  5 parts (前轮/后轮/车身/车头/轮眉架子).

**Next Steps:**
1. `render_anim.py` camera is hand-tuned (yaw 68, pitch 10, zoom 1.22). The
   bike reads correctly but sits small in the cell — `render()` frames on the
   bbox diagonal, which over-pads a 4.05 x 1.28 x 2.35 m object. Fix by passing
   an explicit frame box instead of nudging zoom.
2. No textures on the animated render — the 5 parts carry UVs but no material
   names; check whether the mech's texture is reachable from SysFileVer
   (`骑宠_机甲摩托1阶_Z` spec had no `tex=` entries, so probably not shipped).
3. Untouched: ground-layer blending (pick dominant only), the 16,641-byte noise
   block after the height field.

**Blockers:** None.

---

## 2026-10-31 (3) — textured 3D scenes; terrain grid corrected

**Status:** DONE

**What was done:** Closed all three open follow-ups, and found that the
previous 3D pass had been rendering a **transposed height field**.
* `render_scene3d.py` rewritten around `fw3_mesh()`: MAT3 **and** MAT2 tables
  (MAT2 has no 8-byte tail pad — reading it with the MAT3 stride invents a
  phantom material), TEUV per-corner UVs, submesh *i* → material *i*. 17 of 19
  material names bind through `texmap.json` (exact, then stem: `石头01.jpg` →
  the hash-verified `石头01.png`).
* Ground is now really textured: MAP_DATAEX's six `地表_*` layers resolve
  through SysFileVer to `surface/<guid>.jpg` (all six present), and the
  256x512 MAP_LAYERDATA is an RGB **blend field** (22,370 distinct colours) —
  dominant channel per cell picks the layer, world-space UVs at 6 m tiling.
  The road network in the renders is that splat, not geometry.
* **Terrain fix:** the height blob is 129x258, but only the first 129x129 is
  authored (mean |dz| 0.14 byte/cell); the second block is uninitialised noise
  (all 256 values, mean |dz| 41). Reading it as 258 wide transposed the grid
  and produced the "curtain of spikes". World is 256 m square, z = -26.5..30.4.
  Same fix applied to `render_scenes.py` (its layout background was also drawn
  unscaled into a 760-px canvas, so markers past y=129 fell off the image).
* Cameras are now `oblique` (55), `plan` (12), `horizon` (72) with a sky
  gradient; 18 renders for the 3 populated scenes, full + close crop.
* `tools/effect_ini.py` parses the particle `.txt` format (Atom/Ribbon elements,
  Emitter/Roto/Gravity/GWell/ECircle/Vortex/Snd/R_Twist behaviours, curve
  values). Only 3 effects ship, 2 of them `m_sName='Test'` demos; SysFileVer
  registers **1,777** `effect/` paths that are not in the archive.
* `render_showcase.py` was only failing on missing Pillow — installed; labels
  now use a CJK font. `check_all.py` 12/12.

**Next Steps:**
1. `系统名 → model GUID` for animations (npctabedit / peteditlist), unchanged.
2. Blend two ground layers per cell instead of picking the dominant one.
3. The 16,641-byte noise block after the height field is still unexplained —
   if it is a per-vertex random seed, it would drive the deco scatter jitter.

**Blockers:** None.

---

## 2026-10-31 (2) — full 3D scene renders

**Status:** DONE

**What was done:** New `tools/render_scene3d.py` composites a whole intro
scene in world space: terrain mesh from the MAP_HEIGHTDATA grid (258x129,
2 m spacing, z = -50 + v*500/255; render mesh decimated 3x, z sampling full-res
bilinear), MAP_WIDGETDATA scenery instances at (x, y, terrain+dz) with yaw
from the direction pair and scale u8/16, and all MAP_MODELDATA placements
resolved through SysFileVer (all 22 resolve; spec parse must split on commas —
`modle=,model/…` does not start with `model/`). Geometry read straight from
the shipped .53wf props (minimal VERT/VERH/INDX reader, x0.01). Added
`tri_rgb` per-triangle colour to `obj_render.render()`; camera is a custom
view transform fed through render(yaw=0,pitch=0) — render's own yaw/pitch
framing is too loose for flat planes (fits bbox diagonal). Outputs:
`extracted/maps/intro{517494,518494,518495}_3d_{oblique,high}.png`
(6 renders; ~30% frame coverage, all objects placed).

**Next Steps:**
1. Textured version: bind each mesh's zmap-verified textures via the existing
   uv/texes path in obj_render.render (TEUV chunks carry per-corner UVs).
2. Read `netpak/effect/*.txt` particle INIs (3 plaintext files).
3. Minor: `render_showcase.py` stale path.

**Corrected in (3):** the terrain grid below is **129x129**, not 258x129 — the
258-wide reading transposed the field and the "coherent" claim was wrong.

**Blockers:** None.

---

## 2026-10-31 — zmap instance records fully decoded (Ghidra)

**Status:** DONE

**What was done:** Found the scenery-instance reader in the binary. The exe
embeds a dev-tool viewer script (references `ViewZmap`); the native parent
`GObj3dWidgetBsp` (source `E://GBox2//src//CLIENT//3d//wold//GObj3dWidgetBsp.cpp`)
holds the decode. Decompiled via the "set data err" string xref:
reader `FUN_004e1960`, writer `FUN_004e16a0`, terrain snap `FUN_004e1660`,
renderer `FUN_004e1ed0`. Instance record (9 B, after a 4xf32 header):
u16 x *0.01, u16 y *0.01, i16 dz *0.01 (z = terrain + dz), i8 dir_x /127,
i8 dir_y /127 (billboard direction pair), u8 scale *1/16. Corrects the old
range-guess (4*u16+u8). `zmap.py` now emits decoded instances
(126 across the two populated scenes); new `render_scenes.py` draws
`extracted/maps/intro*_layout.png` (heightmap bg + instance markers).
check_all 12/12.

**Next Steps:**
1. Full 3D scene render: terrain mesh from height grid (z = -50 + v*500/255,
   129x258 half-res grid) + placed objects (MAP_MODELDATA) + instanced
   decorations with yaw/scale.
2. Read `netpak/effect/*.txt` particle INIs (3 plaintext files).
3. Minor: `render_showcase.py` finds no textured meshes (stale path) — fix.

**Blockers:** None.

---

## 2026-10-30 — Ghidra pass; sbus confirmed; full pipeline closed

**Status:** DONE

**What was done:** Installed Ghidra 12.2 + JDK 25 (`D:/Softwares/…`, needs
JDK 25; headless project `D:/ghidra_proj/ChronoTales`; script
`tools/ghidra/decomp_window.java` — no PyGhidra in headless). Decompiled
`loadCoreSubmesh` (VA 0x59A640): **confirms §4.6 sbus layout exactly**
(interleaved base/step f32 ×0.01, normals u16/32767−1, weights u16/10000,
≤4 influences, 10-bit packed indices). New `tools/sbus.py` walks all 15 sbus
parts byte-exact (slack 0); `tools/skin_export.py` writes skin JSONs + per-part
OBJs, all render with real silhouettes. Closed the animation binding chain:
ziptabedit 系统名 → 动作包 → SysFileVer.var (asset registry, 9,975 rows, seed 0)
→ `action/<guid>.zip`; shipped `e41c7990.zip` = 骑宠_机甲摩托1阶_D (mech action
pack; `_M` model / `_D` 动作 convention); all 9 cmz got canonical names.
Mech rig declares 43 bones (u16 after `leks`; tags are stored reversed).

**Next Steps:**
1. Render an animated pose: `run`/`stand01` .caf (from `e41c7990.zip`) on rig
   `b056501a` — caf_decode.py + skin_pose.py exist, playback integration missing.
2. Read `netpak/effect/*.txt` particle INIs (plaintext, 3 files).
3. Optional: UTF-16 string decompile pass for ZLoadCoreModel chain (doc §4.4
   already records it from strings).

**Blockers:** None. Do not trust prose counts over `check_all.py`.

---

## 2026-10-30 (late) — folder audit: zmap scene maps + authoritative textures

**Status:** DONE

**What was done:** Audited the install tree for unexamined files.  Both gpaks
fully extracted (0 missing).  `unins000.dat` confirms the retail install is
exactly: 2 DLLs (ActiveX + NPAPI web-boot plugins, publisher leeuu.com) + exe
+ 2 gpaks — nothing else ever shipped.  Real finds:
* **6 `.zmap`** (`netpak/intro/intro5*.zmap`) = the intro scene maps of §4.7,
  now parsed by new `tools/zmap.py` → `extracted/maps/scenes/intro_scenes.json`.
  Objects resolve via SysFileVer to full material specs
  (`modle=,model/<GUID>.fw3;tex=<name>,tex/<GUID>.<ext>;...`).
* **Texture links now AUTHORITATIVE**: all 108 zmap texture references
  hash-verify against the archive.  `tex_link.py` rewritten: new ZMAP table
  (19/19 names hash-verified, was 4/15) — corrects 木01/木02 swap, 圣坛003
  (old tentative was actually 石头03), 顶02/墙01 look-alikes; adds 木箱,
  石头01/02/03.  Also fixed `load_archives()` to hash real on-disk paths
  (manifest `unknown/…` paths went stale after the rename).  Now **9,078 /
  11,761 mesh triangles (77%) bind verified textures**.  check_all 12/12.
* **7 `.bin`** with magic `0xBE119407` decode byte-exact as plain varbin
  (§4.2.1 confirmed live; loader = FUN_00538360, Ghidra: `cmp 0xbe119407` →
  skip 4 bytes → varbin reader).  Emote/UI-action/VIP/reward-push tables.
* Ghidra decompiles: `FUN_00539910` (0x602376D7 zip+inflate varbin path).

**Next Steps:**
1. Decode the zmap `world[1:]` scenery **instance blobs** (variable-length
   bit-packed transforms per placed object, e.g. 花007 count=30 blob=286 B;
   floats 0.2 scales + 0x7f-prefixed quants).  Reader is engine-side —
   find via `world.gbc::OnGetWidgetData` xref in Ghidra.
2. Render the intro scenes: terrain grid + placed objects (models ship!).

**Blockers:** None.


---

## 2026-10-20 (later) — two agents: asset manifest + `.caf`

**Status:** DONE

**What was done:** Split into two parallel tracks.
*Manifest track:* `unknown/3602e346.bin` in both archives is a `.var` with **seed
100** (`0xFFABC257+0x64`) holding the packer's file list. `tools/asset_manifest.py`
→ names **327→433/484**, 0 real conflicts. Shows netpak ships **97 of 8,672**
listed paths (1.1%) — a stripped retail cut, which is why no name→GUID table
exists. Fixed a silent bug: `payloads()` globbed `unknown/*.zip` and lost 6
payloads (48→42) once names were applied; now discovers by content.
*Animation track:* `tools/caf_decode.py` + `caf_validate.py`, spec §4.9. Five
`.caf` are stock Cal3D, byte-exact. All bind rig `2682c5d5`.
Added `tools/check_all.py`: **14 invariants**, verified to fail on injection.

**Next Steps:**
1. `ziptabedit` (`af3407ec`) IS the animation binding: 12 rows × 6 cols,
   `系统名 / 原始动作包 / 附加动作包 / 查找索引 / 附加动作名 / 替换动作`, 111 distinct
   clip names. Row 1 = `jumpdown, jumping, jumpstart, walk, death, fly, fly_stand`
   and `stand01` is its `替换动作` fallback idle. **The open link is 系统名 →
   model GUID**, and `查找索引` only holds `模板|人类男` — a wildcard. Look in
   `npctabedit` / `peteditlist`, not in animation data.
2. Render an animated pose from `.caf` + a bound `.cmz` (no renderer does this yet).
3. `netpak/effect/*.txt` particle format — plaintext INI, 3 files, unread.

**Blockers:** None. Do not trust prose counts over `check_all.py`; two errors
this session came from quoting remembered figures (43 vs 406 action packs).
