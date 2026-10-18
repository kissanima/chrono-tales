# `SysFileVer.var` — the asset registry

`netpak.gpak` ships one file that describes every art asset in the client:
`SysFileVer.var`, 2,405,900 bytes. It is not a varbin, not a table, and not
compressed — it is the engine's own `.var` serialisation, written by
`file::SaveVarAsFile` and read by `file::LoadVarAsFile`.

Cracking it pays off three times over:

1. it **names 96 archive entries** that the MPQ hash attack could not reach,
2. it supplies a **5,672-entry GUID → human-name table**, and
3. it spells out **which textures belong to which model**, which the `.cmz`
   files themselves never record.

Everything below was verified against the writer, not guessed from the data.
`tools/varfile.py` decodes the whole file byte-exactly with zero errors.

```
python tools/varfile.py stats extracted/netpak/SysFileVer.var
python tools/varfile.py csv   extracted/netpak/SysFileVer.var -o manifest.csv
python tools/varfile.py guids extracted/netpak/SysFileVer.var -o guids.csv
python tools/varfile.py xref  extracted/netpak/SysFileVer.var \
                              extracted/netpak/_manifest.csv --merge tools/names.json
```

---

## 1. Container

```
u32 magic, then exactly one tagged `var`, then EOF

magic = flag + 0xFFABC257        shipped value 0xFFABC257, so flag = 0
```

`flag` is a **seed**, not a version. The payload is protected by a rolling
XOR key initialised to `flag`:

```
key = (key * 214017 + 0x269EC3) & 0xFFFFFFFF
```

The writer's arithmetic at VA `0x42C6C5` is the giveaway:

```asm
0042c6c7  lea  ebp, [eax + eax*2]     ; eax*3
0042c6ca  lea  ebp, [eax + ebp*4]     ; eax*13
0042c6cd  shl  ebp, 4                 ; eax*208
0042c6d0  add  ebp, eax               ; eax*209
0042c6d2  shl  ebp, 0xa               ; eax*209*1024  = eax*214016
0042c6d5  lea  eax, [eax + ebp + 0x269ec3]   ; eax*214017 + 0x269EC3
```

The key advances:

| where | when | VA |
| --- | --- | --- |
| string body | once per UTF-16 character | `0x42C6C5` |
| array element | once at the loop head, *before* the element | `0x42C991` |

Scalars never advance it. Because `flag == 0`, the very first key is `0`, so
the opening scalars read as plaintext — which is exactly why the file looks
half-decoded if you only skim the first few hundred bytes.

### 1.1 Tag table

All eighteen tags the writer can emit (`mov byte ptr [...], 0x??` sites in
`0x42C190`). "Seen" is the count in `SysFileVer.var`.

| tag | payload | seen |
| --- | --- | --- |
| `0x00`–`0x0F` | small int; the value *is* the tag, no payload | 59,850 / 834 / 1 |
| `0x10` | `u8 ^ key8` | 5 |
| `0x11` | `u16 ^ key16` | 1,673 |
| `0x12` | `i32 ^ key` | 7,462 |
| `0x13` | `f32 ^ key` | 405 |
| `0x14` / `0x15` / `0x21` | 64-bit, two dwords ^ key | 0 |
| `0x16` | vec3, three dwords ^ key | 2,601 |
| `0x20` | `u32 ^ key` | 0 |
| `0x30` | empty string, **no payload at all** | 41,016 |
| `0x31` | wide string, see below — `2n + 9` bytes | 38,784 |
| `0x32` / `0x33` | binary blob, length-prefixed, rolling-rotate XOR | 0 |
| `0x40` | array, count as `u8 ^ key8` — **only when count < 0xFF** | 29,859 |
| `0x41` | array, count as `u32 ^ key` — count >= 0xFF | 1 |
| `0x50` / `0x51` | narrow (UTF-8) string, `u32` / `u16` byte count, no checksum | 0 |
| `0xFF` | null | 471 |

The `0x40` / `0x41` split is a size optimisation, not a semantic one
(`cmp ecx, 0xff; jae` at `0x42C8DD`). The `0x50` / `0x51` split is the same
idea at a threshold of `0x7530` (30,000) bytes.

### 1.2 String layout (`0x31`)

```
u32  char_count ^ key
u16  char[0] ^ key16          key advances
u16  char[1] ^ key16          key advances
...
u32  checksum                 PLAIN — not xored
```

Total `2n + 9`, returned by the writer as `lea eax, [edx + edx + 9]`.

The checksum is seeded with the key at the string's **first character** and
folded per character:

```
acc = char ^ rol32(acc, 1)
```

— the same fold as the archive page checksum `sub_416D70`, but over 16-bit
units. It is stored unencrypted, which makes it an exact 32-bit oracle for a
string's true length.

### 1.3 Array trailer

Both array forms end with a **plain** `u32` holding a copy of the final key.
That is free redundancy and a 32-bit integrity check on the entire key
schedule: if it ever disagrees, the parser has lost sync. `varfile.py`
enforces it as an invariant rather than merely measuring it.

All **29,860** arrays in `SysFileVer.var` pass.

---

## 2. Record schema

The top level is one `0x41` array of **9,975** records. Every record is a
`0x40` array of **exactly 17** values — no exceptions, and **no duplicate
names**.

| # | field | content |
| --- | --- | --- |
| 0 | `name` | logical asset name, e.g. `BOSS_棕毛王_T01` |
| 1 | `path` | `tex/caa3efe4.jpg`, or a composite spec (see 2.1) |
| 2 | `kind` | `贴图` `组合` `模型` `特效` `数据` `动作` `声效` `地表` `表格` `天空` `图标` `场景平面` `UI` `SenceJpg` `版本` `表格模板文件` |
| 3 | `group` | `''` `固定场景` `PC` `怪物` `NPC` |
| 4 | | always `''` |
| 5 | | always `''` |
| 6 | | always `0` |
| 7 | | always `''` |
| 8 | | always `0` |
| 9 | | always `0` |
| 10 | | always `0` |
| 11 | `flag` | `'0'` `'1'` `''` `'无'` — stored as **text**, not a number |
| 12 | `transforms` | `[]`, `null`, or `[[(u32, u32, u32), f32], ...]` |
| 13 | `vec` | `[]`, `null`, or `[(u32, u32, u32)]` |
| 14 | `id` | integer `1`..`123024`; `1` is a "no id" sentinel |
| 15 | | always `0` |
| 16 | | always `0` |

Fields 4–10 and 15–16 are dead: they are always empty. 402 records carry
`transforms`, 2,196 carry `vec`. `id` has 9,129 distinct values across 9,975
records; 834 records use the sentinel `1`.

### 2.1 Composite specs (`组合`, 2,195 records)

A `组合` record's `path` is not a filename but a small binding script:

```
modle=,model/7C23A145.cmz
  ;tex=NPC_风语者克劳德_01_M,tex/C13BE0A4.jpg
  ;tex=NPC_风语者克劳德_02_M,tex/8D1A2861.jpg
  ;action=,action/286988CF.zip
```

Grammar: `;`-separated `key=display_name,asset_path` items.

| key | count | meaning |
| --- | --- | --- |
| `modle` | 2,195 | base model (`.cmz` or `.fw3`). **The typo is the game's**, not a transcription slip. |
| `tex` | 16,179 | one texture slot; the display name is the material name |
| `action` | 375 | animation archive (`.zip`) |
| `add` | 3 | an *additional* model plus its own textures — an attachment or alternate part |

The `tex` display names are the **material slot names**, which is what makes
the GUID → name table possible: the same texture also appears as a plain
`贴图` record, but only the composite carries the artist-facing slot name.

Repeated `tex` entries are meaningful, not a bug — `骑宠_机甲摩托1阶_Z` lists
`tex/3F736FBC.jpg` four times because the bike has four wheels sharing one
texture.

### 2.2 Asset GUIDs are printed with `%X`, not `%08X`

Asset filenames are 32-bit hex GUIDs, and the exporter dropped leading zeros:

```
tex/E6ACC95.jpg        should be 0E6ACC95
model/7F1F144.cmz      should be 07F1F144
tex/4168830.jpg        should be 04168830
```

Proof, from the 23,212 eight-character GUIDs in the file: the first-nibble
histogram is

```
0: 0      1: 718    2: 783    3: 706    4: 834    5: 895    6: 762
7: 778    8: 9240   9: 4049   A: 763    B: 778    C: 767    D: 772
E: 770    F: 597
```

**Zero** start with `0`, where a uniform 32-bit space would give about 1,450.
The probability of that happening by chance is `(15/16)^23212 ≈ e^-1497`.

1,436 references are shorter than 8 characters. Of those, 1,426 are 6- or
7-character GUIDs that lost one or two leading zeros. The remaining handful
(`tex/cd.jpg`, `tex/ac1.jpg`, `tex/222224234512351.jpg`) are **not GUIDs at
all** — particle systems reference hand-named textures, so anything matching
`effect/` must not be zero-padded blindly.

The `8`/`9` spike (13,289 of 23,212, 57%) is unexplained. It is consistent
with a 32-bit hash whose top bit is usually set, but nothing in the writer
confirms that.

---

## 3. What the file buys you

### 3.1 Naming archive entries

The archive stores each entry under `hash_name(path)`. Hashing the 9,975
manifest paths and intersecting with the unnamed netpak entries resolves
**96 of 146**:

```
unknown/0338bb99.53wf  -> model/e753dd7c.fw3   '精灵小镇_建筑201_M'  [模型]
unknown/05466923.jpg   -> tex/6b3e5253.jpg     '人类_男_战士7档_衣服_T'  [贴图]
unknown/2682c5d5.cmz   -> model/b056501a.cmz   '骑宠_机甲摩托1阶_M'  [模型]
```

Overall name recovery goes from **231/483 (48%) to 327/483 (68%)** via
`varfile.py xref --merge tools/names.json`.

The 50 netpak entries it cannot resolve are exactly the **42 varbin `.zip`
bundles plus 8 `.bin`** blobs — the packed asset containers, which are not
themselves assets and so do not appear in a registry of assets. That is a
coherent boundary, not a gap in the decoder.

`sysnet` gains nothing: its 107 unnamed entries are `.gbc` scripts, and
`SysFileVer.var` covers art only.

### 3.2 GUID → name

`varfile.py guids` emits 5,672 pairs, sourced from two places: the `tex=`
slot names inside composites, and the record name for plain paths. This is
the table `.cmz` files do not carry — a `.cmz` references materials by index,
never by name.

### 3.3 Texture binding

For every character, `组合` records give the exact model → texture mapping:

```
骑宠_机甲摩托1阶_Z
  modle                     model/B056501A.cmz
  tex   机甲摩托_轮子        tex/3F736FBC.jpg   x4
  tex   机甲摩托_车身        tex/4168830.jpg
  tex   机甲摩托_车头        tex/895B79FF.jpg
  tex   机甲摩托_轮眉架子     tex/2FC21F0A.jpg
  action                    action/E41C7990.zip
```

---

## 4. Data-quality quirks

These are defects in the game's own data. They are worth knowing because each
one will break a naive parser or a naive GUID lookup.

| quirk | extent | consequence |
| --- | --- | --- |
| `%X` GUID formatting | 1,426 refs | pad to 8 hex digits, but **only** for `tex/` `model/` `action/` — never `effect/` |
| `modle` typo | all 2,195 composites | match `modle`, not `model` |
| runaway texture list | 1 record | `NPC_餐饮店老板_Z` carries a **264,437-character** path string (528 KB) — one `tex=` entry repeated hundreds of times. Any `n > 65536` length guard rejects the file. |
| duplicate `tex` slots | common | intentional — one texture shared by several sub-meshes |
| dead fields | 9 of 17 | fields 4–10, 15, 16 are always empty |

The 264,437-character string is what stalled the first decoder. It is not
corruption: its checksum validates, and it is the only length in the file
that exceeds 65,536.

---

## 5. Verification

The decode is self-checking three ways, and all three pass:

| check | result |
| --- | --- |
| bytes consumed | 2,405,896 of 2,405,896 — exact, no leftover |
| string checksums | 38,784 strings, **0** mismatches |
| array trailers | 29,860 arrays, **0** mismatches |

The three together are strong: the trailer key copy pins the key schedule at
every array boundary, and the string checksum pins it at every string. A
single mis-sized payload anywhere upstream would break both immediately.

Independently, `hash_name()` reproduces the sysnet manifest exactly
(`ui/skill/skillcom.gbc` → `0061aa34`), which validates the cross-reference
that recovers the 96 names.

---

## 6. Loose ends

- The `0x32` / `0x33` blob tags never occur in this file, so their exact
  layout (length-prefix width, the `dl == 0xFF` extra-dword quirk at
  `0x42C461`) is read from the writer but **not** exercised by real data.
- The `8`/`9` first-nibble skew in asset GUIDs is unexplained (see 2.2).
- Field 14 (`id`) is an integer with no confirmed external referent. A byte
  size is the natural guess — related records do carry near-identical values
  (`65086`, `65087`, `65088` for three sibling textures) — but it cannot be
  tested yet, because the assets it describes live inside the varbin bundles
  rather than in the archive directory. The 834 records holding the sentinel
  `1` are presumably generated or empty slots.
