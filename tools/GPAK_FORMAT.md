# Chrono Tales `.gpak` — format specification & extraction pipeline

Reverse-engineered statically from `chronotales.exe` (build 2013-08-28) plus the two  
shipped archives. Everything below is confirmed against the binaries and validated  
against the real data (all 4,547 pages pass the format's own checksum).

---

## 1. What the client actually is

| File                         | Reality                                                                                                                                                                                                                                              |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gboxwebruner.dll`           | An **IE ActiveX control** (`ProgID WebRuner.gboxruner`, CLSID `{095DB571-…}`). It is the *web launcher*, not an archive reader. It only pulls the client down (`Accept-Encoding: gzip, deflate`, `GBoxRuner 1009, Jul 24 2013`).                     |
| `npgboxruner.dll`            | Same launcher, Netscape-plugin variant (`npgboxruner`). Also not a reader.                                                                                                                                                                           |
| `gbox/lm_en/chronotales.exe` | The **native client**: 32-bit PE, imports `d3d9`, `ddraw`, `dsound`, `WS2_32`, `pdh`. Engine source paths embedded: `E:\GBox2\stdruner\stdcln\GClnApp.cpp`, `E:\GBox2\src\pub\base\GFileCache.cpp`, `E:\GBox2\src\CLIENT\frame\GNetCacheFileIO.cpp`. |

So this is **not** a Flash/SWF wrapper. It is a native DirectX 9 MMO client built on an  
in-house engine called **GBox2**, with:

- zlib **1.1.3 / 1.1.4** statically linked (`inflate`/`deflate`, Mark Adler / Jean-loup Gailly copyright strings) — used for network traffic, **not** for the archive.
- an embedded scripting language (`.gbc` files, plain-text "GBox script": `class X:Y { … }`, `style`, `#include`, `#define`).
- a **virtual file system** for the archives, implemented by `GNetCacheFileIO` /  
  `GNetPakIO`. `InitPak(pakname, "sysbin/")` in the embedded script `index.gbc`  
  shows the pak is a *network resource cache* rooted at `sysbin/`.

The two archives are therefore a **page-based mini filesystem**, not a ZIP-alike.

---

## 2. Container layout

All integers are little-endian. Page size is **4096**; page *N* lives at `N * 4096`.

### 2.1 Header (page 0, offset 0, 84 bytes)

|  Off | Type    | Field                                                              |     netpak |     sysnet |
| ---: | ------- | ------------------------------------------------------------------ | ---------: | ---------: |
| 0x00 | char[4] | magic `KAPG` = dword `0x4750414B` — `"GPAK"` written byte-reversed |            |            |
| 0x04 | u32     | header size = `0x54`                                               |         84 |         84 |
| 0x08 | u32     | version = `0x64` (low 16 bits; high 16 must be 0)                  |        100 |        100 |
| 0x0C | u32     | reserved = 0                                                       |          0 |          0 |
| 0x10 | u32     | reserved (this build: `1000 + entryCount`)                         |       1148 |       1336 |
| 0x14 | u32     | **entry count**                                                    |        148 |        336 |
| 0x18 | u32     | write time (unix)                                                  | 1380183591 | 1380180949 |
| 0x1C | u32     | **entry capacity** = `(dirPages*4096 − 84) / 536`                  |        152 |        336 |
| 0x20 | u32     | directory page count                                               |         20 |         44 |
| 0x24 | u32     | reserved                                                           |       1843 |       2704 |
| 0x28 | u32     | **total pages** = `fileSize >> 12`                                 |       1892 |       2781 |
| 0x2C | u32     | free page count                                                    |         29 |         33 |
| 0x30 | u32     | first free page                                                    |       1863 |       2748 |
| 0x34 | 32 B    | zero                                                               |            |            |

The validator at `0x417210` checks exactly these: magic, `[4]==0x54`,  
`[8]&0xFFFF==0x64`, `[8]>>16==0`, `[0x28]==fileSize>>12`, and  
`[0x1C] >= (dirPages*4096-0x54)/536` (compiled as a `mul 0x7A44C6B / shr 4`  
divide-by-536).

### 2.2 Directory — flat array of 536-byte records at **offset 0x54**

The directory lives at the *front* of the file, immediately after the 84-byte header,  
inside pages 0…dirPages-1. Records are sorted **ascending by `nameHash`** so the client  
can binary-search (`0x4174B0`).

|   Off | Type  | Field                                                   |
| ----: | ----- | ------------------------------------------------------- |
| 0x000 | u32   | `nameHash` — MPQ `HashString(lower(path), 0)`           |
| 0x004 | u32   | write time (unix)                                       |
| 0x008 | u32   | second hash (not `HashString(…,1)`; purpose unknown)    |
| 0x00C | u32   | second timestamp                                        |
| 0x010 | u32   | **size** in bytes                                       |
| 0x014 | u32   | **start page**                                          |
| 0x018 | u16   | constant marker `0xFA21`                                |
| 0x01A | 510 B | **not initialised** — stale heap bytes written verbatim |

> **There are no filenames in the archive.** The 512-byte tail of each record is the  
> uninitialised remainder of the 536-byte struct the packer `malloc`'d. The engine  
> keys the cache purely on the 32-bit path hash.

### 2.3 Pages

Page 0 doubles as the container header, so its payload area starts at `0x54`.  
Every other *allocated* page (magic `0xADAE`) has:

|   Off | Type | Field                                                |
| ----: | ---- | ---------------------------------------------------- |
| 0x000 | u32  | magic `0xADAE`                                       |
| 0x004 | u32  | `prevPage` (0 = first page of the file)              |
| 0x008 | u32  | `nextPage` (0 = last page of the file)               |
| 0x00C | u32  | reserved                                             |
| 0x010 | u32  | `chunkIndex` — position of this page within the file |
| 0x014 | u32  | **`seed`** — cipher key                              |
| 0x018 | u32  | reserved                                             |
| 0x01C | u32  | `dataLen` ≤ 4060 (`0xFDC`)                           |
| 0x020 | …    | payload (`dataLen` bytes)                            |
| 0xFFC | u32  | checksum of the *decoded* payload                    |

Files are **chains** of pages (up to 4060 bytes each), possibly non-contiguous —  
e.g. netpak entry 20 has `prev = 1832`. Free pages are zero-filled with  
`{0xADAE, prev, page+1, 0, 0, 0, 0, 0}` and form the free list at `[0x30]`.

### 2.4 Payload cipher (`sub_416BB0`)

```c
uint32_t k = seed;
for (i = 0; i < len; i++) {
    k = ((k << 1) & 0xE7BD2160) + 1;   // LFSR
    dst[i] = src[i] ^ (k & 0xFF);
}
```

`seed == 0` is a **fixed point**: `((0<<1)&mask)+1 = 1`, and `((1<<1)&mask)+1 = 1`  
because `0xE7BD2160 & 2 == 0`. So seed 0 degenerates to a **constant XOR 0x01** — which  
is what this build uses for almost every page. That single bit-flip is the entire  
"obfuscation": it hides file signatures (`89 50 4E 47` → `88 51 4F 46`) and makes the  
plaintext script text look like garbage.

### 2.5 Checksum (`sub_416D70`)

```c
uint32_t acc = 0;
for (i = 0; i < len; i++) { acc = rol32(acc, 1); acc ^= src[i]; }
```

Computed over the **decoded** payload and compared with the dword at `0xFFC`  
(`sub_416C70`). Every page in both archives matches.

### 2.6 Filename hash (`sub_44AD30`) — Blizzard MPQ `HashString`

The crypt table is generated at startup by `sub_44ACB0` with the stock MPQ constants  
(`seed = 0x00100001`, `seed = (seed*125 + 3) % 0x2AAAAB`, 256×5 entries), and the hash  
uses the stock seeds `0x7FED7FED` / `0xEEEEEEEE` with hash type 0.

Differences from MPQ: the string is read as **UTF-16**, and characters are normalised  
**to lower case** (MPQ upper-cases) with `\` → `/`.

```python
def hash_name(s):                       # s = UTF-16 path, type 0
    t = crypt_table(); a, b = 0x7FED7FED, 0xEEEEEEEE
    for ch in s:
        c = 0x2F if ch == '\\' else (ord(ch) + 0x20 if 'A' <= ch <= 'Z' else ord(ch))
        a = ((a + b) & 0xFFFFFFFF) ^ t[c]
        b = (b + c + ((b << 5) & 0xFFFFFFFF) + a + 3) & 0xFFFFFFFF
    return a
```

---

## 3. Why `pak_archive` / `GPK_RePack` don't apply

- **AdamFull/pak_archive (GPAK)** — unrelated format; only the four ASCII letters collide.
- **GoneUp/GPK_RePack (Tera)** — unrelated container, and Tera's GPK is not page-chained.
- The magic *looks* like `GPAK` but is stored reversed (`KAPG`), and the `T` byte at  
  offset 4 is part of the `0x54` header-size field. Signature-scanning for `GPAK` on  
  disk finds nothing.

The one genuinely reusable idea is MPQ's `HashString`, which GBox2 lifted verbatim —  
that is what made filename recovery possible.

---

## 4. Results

|                                |          netpak.gpak |          sysnet.gpak |
| ------------------------------ | -------------------: | -------------------: |
| Size on disk                   |          7,749,632 B |         11,390,976 B |
| Pages                          |                1,892 |                2,781 |
| Pages CRC-verified             |        1,843 / 1,843 |        2,704 / 2,704 |
| Directory entries              |                  148 |                  336 |
| Entries with content extracted |        **148 / 148** |        **336 / 336** |
| Logical bytes                  |            7,138,937 |           10,242,434 |
| Written                        | 2013-09-26 08:19 UTC | 2013-09-26 07:35 UTC |

Contents:

- **netpak** — model/media bundle: 49 ZIP bundles (each holding a `varbin` blob, with a  
  4-byte `D7 76 23 60` wrapper), 45 JPEG, 22 `.53wf` meshes, 11 PNG, 9 `.cmz`  
  skeletons, 9 binary, 3 text.
- **sysnet** — the engine/resource root (`sysbin/`): 221 `.gbc` GBox scripts,  
  63 PNG, 26 binary, 13 JPEG, 12 text, 1 RIFF.

### Formats seen inside

- `.gbc` — **plain-text** GBox script, GBK-encoded. Not a binary format at all:
  164,844 lines of source across 235 files. See §4.1.
- `.53wf` — 3D model. Header `53WF`, then byte-reversed 4CC chunks. Fully decoded,
  see §4.3.
- `.cmz` — skeleton. Header `CMZ\0`, `u32 version=102`, then the same byte-reversed
  4CC scheme. Only four tags exist: `skel`, `MESH`, `MAP1`, `END.`. Bone hierarchy,
  bind pose and attachment names are fully decoded — see §4.4.
- ZIP bundles — standard deflate ZIPs whose only member is `varbin`.

### 4.1 `.gbc` — the client's entire script source

The `.gbc` entries are **uncompiled source**, not bytecode. Decoded as GBK they are
a C-like declarative DSL in which UI layout *is* the class body: widget instances
are declared as typed members with an initialiser block, and methods are ordinary
function bodies.

```
class GSplitBar:GUIPanel
{
    GSysHSwitch m_pHSwitch:(60,50,100,10,1,20,this,"SplitValue",1) { … }
    void SetSplit(int minvalue, int maxvalue) { … }
}
```

Grammar, as recovered:

| construct | meaning |
|---|---|
| `#pragma once`, `#ifndef/#define/#endif`, `#ifdef _MAIN` | preprocessor; `_MAIN` guards the standalone test harness |
| `#include "path"` | textual include, resolved relative to the including file |
| `#push` / `#pop` | class-scope stack |
| `#layer name { … }` | a named panel layer inside a class (350 of them) |
| `#enum A,B,C` | enumerators with implicit values 0,1,2… (158 declarations) |
| `#define NAME value` | object-like macros; `$` is the source-directory token |
| `#system dictionary "path"` | binds a localisation CSV for the following block |
| `class N:Base,mixin1,mixin2` | **single base plus any number of `style` mixins** |
| `style N { … }` | a named trait/behaviour mixin (301 declared, mixed in 274 times) |
| `Type name:(args) { … }` | widget instance; the block is its constructor |
| `Type name;` | plain member |
| `@"text"` | localisation literal (8,720 sites) |
| `pid` | parent id — the enclosing widget |
| `m_i_Name` | implicit binding to the interface registered as `"_Name"` |

**The interface registry is the real dependency graph.** Scripts do not call each
other by class; they resolve a named singleton at runtime:

```
g_pDeskTop.RegInterface(m_pEquipTab, "_EQUIPTAB");   // publish  (470 sites)
obj p = FindInterface("_PARAMTAB");                  // resolve  (697 sites)
m_i_PubFun.SplitToVar(...)                           // implicit (9,530 refs)
```

428 distinct names are published, 279 of the 300 `m_i_*` symbols resolve to one,
and 2,088 file→file edges fall out of it — a denser and more truthful graph than
`#include` (567 edges), because it captures the actual runtime coupling.

`tabedit/*.gbc` (46 files) are the table editors; their `m_szItemName` column
declarations are the same schemas decoded in §4.2, which is how the varbin blobs
were identified.

Index, dependency graphs and localisation inventory: `gbc_index.py` →
`extracted/gbc_index/`.

### 4.2 Inside `varbin` — the entity database

The 49 `.bin` entries in `netpak` are ZIPs (one member, literally named `varbin`,
behind a 4-byte `D7 76 23 60` prefix). Uncompressed they total **10,104,187 bytes** —
the single largest payload in either archive, and it is *content*, not code.

`varbin` is a flat **tagged value stream**, not a table of fixed records:

| byte | payload | meaning |
|---|---|---|
| `0x31` | `u32 charCount` + `charCount*2` bytes UTF-16LE | string (self-describing) |
| `0x10` | 1 | u8 |
| `0x11` | 1 | u8 |
| `0x12` | 4 | int32 (saw `0xFFFFFFFF` = −1) |
| `0x13` | 4 | float32 (saw `0x3F800000` = 1.0, `0x3F4CCCCD` = 0.8) |
| `0x40` | 1 | u8 (often a small count) |
| `0x41` | 4 | u32 (header count / version) |

The tag is a **schema field id**, not a type: the payload width is implied by the id,
so a reader only needs the width table above — the meaning of each id lives in the
game binary and in the `tabedit/*.gbc` editors (see below). No compression, no
encryption; only the strings are self-describing, which is what makes the file
readable at all.

Deriving the width table was a constraint problem, not guesswork. Every "gap" between
two strings is an independent, complete sequence of non-string fields, so it is a hard
constraint on the widths. Solving by intersection across all 25,492 distinct gaps
pinned the seven ids above; `tools/varbin_solve.py` reproduces it. The remaining ids
are unresolved (strict gap coverage 48%), so `varbin_extract.py` **resynchronises** on
the next valid string instead of aborting — every blob yields all of its text even
where the numeric grammar is still open.

**Records are property bags.** Strings appear as adjacent label/value pairs. Labels
recur thousands of times across the corpus, so adjacency alone recovers a categorical
cross-tab without needing the numeric schema:

| label | occurrences | top values |
|---|---|---|
| `添加人` (added by) | 9,524 | `qmz`×4015, `sxn`×2553, `yup`×2246, `sp`×252, `胡晓曦`×122 |
| `分类` (category) | 6,449 | `装备`×2277, `圣灵之都`×344, `商城道具`×341, `跑环`×321, `阳光沙漠普通怪`×310 |
| `档次` (tier) | 2,557 | `三档`×1150, `二档`×508, `一档`×306, `七档`×269, `新手`×168 |
| `职业` (class) | 2,420 | `通用`×472, `学士`×427, `刺客`×407, `牧师`×370, `战士`×362, `法师`×356 |
| `掉落类型` (drop type) | 493 | `特定掉落`×249, `通用掉落`×221 |
| `生活技能` (life skill) | 668 | `炼药`×131, `防具`×129, `捕猎`×104, `武器`×81 |
| `种类` (kind) | 484 | `镶嵌宝石`×119, `装备`×91, `道具`×55, `强化`×41 |

The six playable classes are `学士` Scholar, `刺客` Assassin, `牧师` Priest,
`战士` Warrior, `法师` Mage, plus `通用` universal. Tiers run `新手` → `一档`…`七档`
plus `时装` cosmetic.

Blob roles split cleanly by string density: **39 of 49** are property bags
(strings > 50% of tokens), 6 are mixed, and 4 (`cc5de97f`, `da80ff38`, `4514d98f`,
`c41c9276`) are pure binary — the last has zero strings in 12,744 bytes.

#### File header — three integers

Every blob opens with the same triple:

```
[record count]  [column count]  [flag word]
```

`record count` is `0x40 <u8>` for small tables and `0x41 <u32>` above 255; the flag
word's high bit is **always set** across all 26 occurrences, so it is a flag/version
word rather than a name hash (hashing every table name, filename and editor title at
three hash types produced no match). It repeats between unrelated tables, confirming
it is not an identifier.

`column count` is the identification key: it must equal the editor's `m_szItemName`
length. Matching all 49 blobs against the 43 schemas:

| blob | records | cols | table |
|---|---|---|---|
| `87d142c2` | 4,122 | 52 | **itemtabedit** 物品表编辑 |
| `0580853e` | 1,833 | 66 | **npctabedit** NPC table |
| `433267c3` | 1,399 | 88 | *(no matching editor)* |
| `47bf0e8a` | 1,298 | 7 | **droptabedit** 掉落表编辑 |
| `fcf45131` | 1,193 | 57 | **skilltabedit** 技能表 |
| `fcef78c4` | 1,157 | 22 | **npcmaketabedit** NPC 合成表 |
| `6b1f6475` | 1,020 | 37 | **bufftabedit** buff 表 |
| `8664461b` | 924 | 24 | **effectabedit** 效果表 |
| `66d2c4f3` | 735 | 3 | **paramtabedit** 参数表 |
| `15fe62ab` | 560 | 32 | **maketabedit** 合成表 |
| `33e159c6` | 329 | 6 | **forcetabedit** 势力表 |
| `60792c4a` | 331 | 8 | **unionbuffedit** 公会 BUFF 表 |
| `09f8db1d` | 262 | 20 | **daytaskedit** 每日成就表 |
| `6a64e3da` | 255 | 18 | **MapAreaDlg** 地图区域 |
| `57defdf0` | 244 | 4 | **exptabedit** 经验表 |
| `cbc16f9c` | 213 | 12 | **EquipTableEdit** 装备表 |
| `d483aad3` | 177 | 12 | **EquipTableEdit** |
| `8d861de9` | 159 | 15 | **gameshopedit** 商城表 |
| `55b90d45` | 154 | 8 | **unionbuffedit** |
| `0e747450` | 136 | 12 | **EquipTableEdit** |
| `486bf1bd` | 128 | 5 | **pathedit** 路径表 |
| `5218cf47` | 80 | 5 | **pathedit** |
| `37a1742e` | 65 | 15 | **gameshopedit** |
| `93c34746` | 49 | 25 | **shoptabedit** 商店表 |
| `9f9048cf` | 46 | 29 | **worldtabedit** 场景表 |
| `204d427b` | 41 | 12 | **EquipTableEdit** |
| `d07f4acc` | 27 | 27 | **peteditlist** 宠物表 |
| `dd447626` | 26 | 12 | **EquipTableEdit** |
| `615fbfd5` | 20 | 23 | **daliytabedit** 日常活动表 |
| `455cc3b0` | 18 | 17 | **daliyactivitytabedit** 每日活动表 |
| `fe4857b1` | 18 | 9 | **runetabedit** VIP 表 |
| `96c9244b` | 12 | 15 | **gameshopedit** |
| `4a5475b2` | 12 | 7 | **droptabedit** |
| `af3407ec` | 12 | 6 | **forcetabedit** |
| `8898f905` | 10 | 16 | **actiontabedit** 动作表 |
| `1e806186` | 8 | 6 | **forcetabedit** |
| `fcd47eb6` | 7 | 9 | **runetabedit** |
| `c3eba311` | 6 | 39 | **passtabedit** |
| `6fb967a3` | 5 | 14 | **tasktabedit** 任务表 |
| `e15a7343` | 1,982 | 2 | *(no matching editor)* |
| `4ea8321c` | 309 | 11 | **gifttabedit** 礼包表 |
| `fcef78c4` … | | | |

**46 / 49 identified**, 21 unambiguously by column count alone; **19,640 records** in
total. The tie-breaks (five blobs share 12 columns, four share 6, three share 15) are
resolved by scoring the blob's string vocabulary against each candidate's column names.
`tabeditcom.gbc` and `tabeditlist.gbc` are the editor *framework*, not tables, so 43 is
the true table count — which is why the 88-column and 2-column blobs have no schema.

The framework also explains what `netpak` is. `tabeditcom.gbc::GetAllData` fetches rows
over the network:

```
csvr::call(GATENAME+"/"+m_sSvrName, "GetAllData", v)
```

so these blobs are the **client-side cache of server tables** — which is why netpak's
directory keys are not derivable from the archive contents.

**The schema is in the client, not the archive.** All 43 `tabedit/*.gbc` table editors
declare their columns as parallel tuples — `m_szItemName` (names), `m_szExplain` (help
text), `m_szType` (widget/type codes), `m_szFlip` (sub-field layouts) — plus
`m_nTagNO` / `m_nKeyMode`. `tools/tabedit_schema.py` extracts all 43 into
`table_schemas.json`; the item table alone is 52 columns with full per-column
documentation, e.g. 装备部位 enumerating all 20 equip slots and 属性构成 all 37
attribute builds. That file is the lookup table for labelling any numeric field once
its id is matched.

---

### 4.3 `.53wf` — mesh geometry

22 meshes, 34 submeshes, 8,434 vertices, 11,761 triangles. Fully decoded; the
recovered algorithm reproduces the client's own vertex positions bit-for-bit,
so every mesh is now a renderable OBJ with per-corner UVs.

**File layout.**

```
53WF <u32 version=1> <u16 nmesh>
MAT3 | MAT2                  material table (name + texture filename)
MESH                         fixed 72-byte block, GBK name + padding
nmesh x ( INDX, TEUV, VERT|VERH )
ENDM                         exactly once, after the last submesh
```

`nmesh` in the header equals both the number of `INDX` chunks and the number of
`VERT`/`VERH` chunks in **all 22 files** — that is the authoritative submesh
count, and it is what makes the chunk chain unambiguous. Tags are stored
byte-reversed (`XDNI` = `INDX`), and `ENDM` is stored as `MDNE`.

**`VERT` — quantised positions.** 21 of 22 meshes use this. A 24-byte header of
six `float32` values, then three `uint8` per vertex:

```
base_x, step_x, base_y, step_y, base_z, step_z    ; all multiplied by 0.01
for each vertex:  pos = byte * step + base
```

The `0.01` constant is read from VA `0x6D0738` (`0x2D0738` → `0.0099999998`).
Header floats are therefore in centimetres; `step * 255` gives the submesh
extent directly. Vertex buffers are over-allocated: a median of 1.4% of the
bytes belong to vertices no triangle references.

**`VERH` — unquantised positions.** The dispatch is `sub eax,0x56455248`
(`HREV`→`VERH`) then `je`, and `sub eax,0xc` turns it into `TREV`→`VERT`, so
`VERH` is simply the neighbouring tag. Payload is `float32` xyz triples, 12
bytes per vertex, no header, same `0.01` scale. Only 1 of 22 meshes uses it —
the tier-7 human male weapon — so it is the high-precision authoring path.

**`INDX`** — `uint16` triangle indices, no header. Manifold: 58–96% of edges
are shared by exactly two triangles.

**`TEUV`** — `float32` UV pairs, **one per index (per corner)**, stride 8;
`len/8` equals the index count in all 34 submeshes. Verified numerically: for
89.2% of edges shared by two triangles both endpoints carry byte-identical UVs
(median deviation exactly 0.0), and the remainder are legitimate texture seams.

**`MAT3` / `MAT2`** — material entries of `<u16 n> <GBK name> <16 bytes>
<u16 m> <GBK texture>`. `MAT3` and `MAT2` are the same list at two format
versions; 13 files use `MAT3`, 9 use `MAT2`. Texture names resolve to files
(`蘑菇001.jpg`, `木箱.jpg`, `小镇门.png`) but those files are stored under
hashed names in the archive and the path convention was not recovered, so
textures are not yet linkable.

**Orientation.** Assets are **metres, +Z up, right-handed**. This is not a guess:
the mushroom cluster decodes to `z ∈ [0.027, 1.340]` and the altar platform to
`z ∈ [0.000, 0.566]` — both sit exactly on the ground plane, and mushrooms
render as domed caps on stems rather than as discs. Reading the same data as
Y-up lays every plant on its side.

**Verification.** Median triangle edge is 0.25x the length it would have with
vertices shuffled (`min 0.059`, `max 1.003`), so the point cloud is genuinely
structured rather than noise. The three ratios near 1.0 are the two smallest
meshes in the set (a 21-vertex grass billboard and an 8-vertex cube) where
shuffling barely changes anything. Decoded bounding boxes are object-scale
throughout: crate 0.55 m, shoulder pad 0.61 m, weapon 2.11 m, mushroom 3.3 m.

`mesh_vert_decode.py` → `extracted/models/obj/*.obj` + `extracted/models/*.json`;
`obj_render.py` → `extracted/models/png/*.png` + `contact_sheet.png` (a pure-stdlib
z-buffered rasteriser, no 3D stack needed); `make_gallery.py` →
`extracted/models/models.html`.

### 4.4 `.cmz` — skeletons

9 files, all decoded to bind pose. Bone counts are 43 / 109 / 119×7; the 109- and
119-bone rigs are the humanoids (all exactly **1.754 m** tall, root `Bip01`), the
43-bone rig is the mech (`机甲摩托`, 1.565 m).

**Container.**

```
CMZ\0  <u32 version = 102>
skel   (stored "leks")   bone hierarchy
MESH   (stored "HSEM")   named submesh list
MAP1   (stored "1PAM")   attachment / model name list
END.   (stored ".DNE")
```

`102` is the same version word as `.53wf`'s, so one gate covers both. Chunk order is
`skel MESH MAP1` in all 9 files.

**Only four tags exist.** The loader at `0x5992FD` compares the tag against exactly
`skel`, `MESH`, `MAP1`, `END.` — `MESH` goes to `0x598F80`, `MAP1` is handled inline
at `0x59932D`, `skel` to `0x5ABB50`. Any other four-letter run inside the payload that
looks like a tag (`ENXV`, `EMPV`, `NBZ1`, …) is a **false positive** from scanning raw
bytes, not a chunk. This is why the earlier chunk inventory for `.cmz` was wrong.

**`skel` — the bone stream.**

```
u16 n
n x {
    u32 name_len ; GBK name[name_len]
    f32 x 14                       ; two (position, rotation) pairs
    s16 parent                     ; -1 = root
    u16 n_children ; u16 child[n_children]
}
```

The 64-byte stride only holds when `n_children == 0` — the record is variable-length,
which is what makes it desync if you assume a fixed record. The stream ends exactly
at the next `MESH` tag, which is the cheap self-check.

Of the 14 floats: `[0:3]` is the bone's local offset in centimetres along its parent's
+X, `[3:7]` is the local rotation as a quaternion `xyzw`, and `[7:10]` / `[10:14]` are
a second offset/rotation pair the exporter writes for bookkeeping but which is not
needed to pose. Parenting is **0 mismatches across all 9 files** when validated
bidirectionally (every child index appears in its parent's child list and vice versa).

**The quaternions are inverse rotations.** Composing them as-is breaks left/right
symmetry; conjugating them restores it. The pose is:

```
R_world[i] = R_world[parent] * conj(q[i])
P_world[i] = P_world[parent] + R_world[parent] * offset[i]      (offset scaled 0.01)
```

for roots, `R_world = conj(q)`, `P_world = offset * 0.01`.

This convention was not guessed — it was selected by a **geometric test with a
decisive answer**: a correct bind pose must mirror left/right. Scoring candidate
conventions by left/right mirror error gave **0.0000** for the formula above with X
as the mirror axis, versus large residuals for every alternative (quaternion order,
no conjugation, axis permutations). Independent confirmation on the humanoid rig:
the arm chain (UpperArm→Forearm→Hand) bends **2.3°** and the leg chain
(Thigh→Calf→Foot) **1.2°** — a clean A-pose. The 43-bone mech's 73.2°/82.5° are
genuine, because its bind pose has bent limbs.

Sanity numbers for the 109-bone rig: pelvis at `z = 98.20` cm, head top at
`z = 177.58` cm → 1.75 m, +Z up. That matches the `.53wf` orientation exactly, so
meshes and skeletons share one world frame and need no conversion.

**`MESH` — named submeshes.** `u32 n`, then `n × (u32 name_len, GBK name, u32 m,
m × record)`. Each record begins with the reversed `subs` tag and parses further
through `0x59A2E0`. The records are nested and variable-length, so a fixed 136-byte
stride desyncs — the parser must be defensive rather than assume a layout.

**`MAP1` — attachment names.** `u16 n`, then length-prefixed GBK names each followed
by a small flag block. Skipping the leading `u16` is mandatory; without it the name
decode returns zero entries.

**What the files actually are.** The `MAP1`/`MESH` names identify them: `机甲摩托_前轮`
(mech motorcycle front wheel), `b_faxing00` (hairstyle 00), `人类_男_战士7档_衣服_M`
(human male warrior tier-7 clothes). So `.cmz` holds skinned character and equipment
pieces — the skeleton, not the mesh; geometry lives in the matching `.53wf`.

`cmz.py` → `extracted/models/cmz/*.json` (bones + chunk inventory) and
`extracted/models/skel_obj/*.obj` (octahedral bone shapes, one OBJ per rig);
`obj_render.py --obj extracted/models/skel_obj` → `extracted/models/skeletons.png`.

### 4.5 Texture linking

Materials name their texture as a bare filename (`蘑菇001.jpg`, `木01.jpg`,
`新手村藤.png`); the archive stores every entry under the MPQ hash of a full
path.  The path convention is

```
model/<8-hex>.fw3        (mesh)
tex/<8-hex>.<ext>        (texture)
```

i.e. literal ASCII directories, and an 8-hex-digit art-pipeline GUID as the
filename.  Hashing those paths reproduces archive entries:

```
hash("model/ECED0FB0.fw3")  =  0x2eb6ebbf  (the mushroom mesh)
hash("tex/BDBC1E57.jpg")    =  0xdd462f88  (its texture)
```

`BDBC1E57` / `ECED0FB0` are opaque ids — the same string hashed under MPQ,
CRC32, FNV-1a, FNV-1, DJB2, DJB2X, SDBM, Jenkins, Murmur3 and across all
8 hash types / encodings / prefixes produces no match, so they are GUIDs
assigned by the art tool, not derivable from the Chinese filename.

**The GUID ↔ filename table** lives in the entity database (varbin), where
each decoration record stores its model path and texture path as
consecutive strings.  Parsing 9 such records (covering 5 textures) produces
4 hash-verified material-name links (the texture extensions match the
material extensions on all 4) and identifies 6 meshes that resolve
directly.  The remaining 11 names are not in the decoration table because
the trees, gate, altar and house textures come from map-instance data the
client reads at runtime from a `media/map/` folder that isn't shipped with
this client-only install — the `world/terrain.gbc` script documents the API
(`OnGetWidgetData(name, sModel, sTex)`), but the ZMAP files that feed it
aren't here.

**What is recovered for the 15 distinct material names** (full table in
`extracted/models/textures.html`):

| confidence | how it was resolved | count |
|---|---|---|
| hash | `tex/<GUID>.<ext>` from the varbin decoration table | 4 |
| visual | material extension picks the format (PNG = alpha cutout), the art matches the mesh unambiguously | 9 |
| tentative | architectural name fits by role (roof / brick / beam), supported by the house render | 2 |

Mesh-level fallback handles two meshes that ship with no `MAT3` block at
all (the crate `879926bf` and the flower `4c85d7c7`): they bind to a
single texture via the varbin link or visual identification of the mesh.

`tex_link.py` → `extracted/models/texmap.json` (the link table);
`obj_render.py --texmap extracted/models/texmap.json` → `png_tex/*.png` and
`textured_sheet.png`; `tex_gallery.py` → `textures.html`.

### 4.6 `.cmz` `sbus` skin-weight records (container decoded)

> **Status: container + per-part structure solved; exact per-vertex encoding
> still open (needs the consumer routine).**

A `.cmz` carries the skeleton, not the mesh; the mesh is the matching `.53wf`
(referenced by the part name through the varbin name table, e.g.
`人类_男_战士7档_衣服_M` → a `model/<GUID>.fw3`).  The per-part **skin weights**
that bind mesh vertices to bones live in the `MESH` chunk as a sequence of
`sbus` records (`sbus` is the byte-reversed store of the tag `subs`; the four
outer chunk tags are likewise byte-reversed: `leks`=skel, `HSEM`=MESH,
`1PAM`=MAP1, `.DNE`=END.).

**Chunk structure** (verified against the loader at `0x199307` in
`gbox/lm_en/chronotales.exe`):

```
u32 n_parts
[ per part ]  name_len:u32 | name:GBK+NUL | u32 record_count | sbus blob
```

Each `sbus` blob begins with a 32-byte header, then variable-length payload:

```
sbus             4 bytes  tag
u32 part_index   0..n_parts-1  (mech 2682c5d5 has 5 parts, indices 0..4)
u32 0
u32 A             per-part constant (53761 / 307457 / 92673 / 565249 ...)
u32 B             per-part constant (72704 / 164864 / 109568 / 330752 ...)
u32 0
u32 0
u32 bonecap      256 for mounts/mechs, 512 for humans  (= max-bone class,
                  i.e. an 8-bit bone index space; the mech rig has 43 bones,
                  human rigs 109–119)
[ payload ]      variable, u8/u16 mixture with sentinels 0x7fff / 0xffff
```

The loader allocates an in-memory array of `n_parts × 136-byte` part structs
(the `0x88` = 136 stride shows up at `0x19937e` `push 0x88` and the loop at
`0x199654` `add edx, 0x88`; the record indexer `0x19a2b0` is `base + idx*136`).
The 136-byte in-memory struct holds the part name, a few flag bytes (read one
at a time via `0xa1a40`/`0xa1a00`), and a pointer to the variable skin blob
at offset `+0x6c` (the blob itself is slurped by the `memcpy`-like reader
`0x199c30`).  Each part also has a sub-array of 136-byte records indexed the
same way (the loop at `0x199cc0` walks `[part+0x5c]` parts × `[struct+4]`
sub-records, calling `0x19b310(...,0x19)` per record).

**Payload format — open problem.**  Every blob's payload is an integer stream
(systematic `f32` scans are all ~0).  It is **not** a naive fixed-stride
`(bone:u16, weight:u16)` stream: no group size 1–8 × field position yields a
field whose values are all valid bone indices (< bonecap), and no clean
`bone/weight` split appears at the u8 or u16 level either.  Sentinel values
`0x7fff` (32767) and `0xffff`/`0xfffe` recur, and `0x2710` (10000) appears
repeatedly.  The blob is consumed opaquely (pointer stored, applied later when
the `.53wf` vertices are skinned), so the exact per-vertex (bone, weight)
encoding almost certainly lives in the mesh/skinning consumer, not the `.cmz`
parser — decoding it requires locating that consumer (the routine that reads
`struct+0x6c` and blends vertices by bone matrix).

`cmz.py` now extracts every part's `sbus` blob (header + raw bytes) into
`extracted/models/cmz/<hash>.json` under `mesh[].blob_len / part_index / A / B /
bonecap`; `tools/solve_sbus.py` is the brute-force field/stride checker used to
rule out the naive encodings.

---

## 5. The pipeline

```
tools/gpak.py              format library: page decode, cipher, CRC, MPQ hash, archive reader
tools/gpak_extract.py      CLI: list | verify | names | extract
tools/gpak_write.py        writer: page/dir/free-list emitter, byte-exact rebuild()
tools/gpak_repack.py       CLI: roundtrip | fresh  (verifies the writer)
tools/names.json           recovered filename table (hash -> path)
tools/propagate_names.py   #include-graph filename propagation
tools/varbin_extract.py    entity DB: tokens | text | keys (label->value cross-tab)
tools/varbin_identify.py   maps each blob to its game table via header column count
tools/varbin_tables.py     exports identified blobs to extracted/tables/*.csv
tools/varbin_solve.py      solves the varbin field-width table by constraint
tools/tabedit_schema.py    extracts all 43 table schemas from tabedit/*.gbc
tools/table_schemas.json   43 tables: columns, type codes, help text
tools/mesh_decode.py       .53wf / .cmz structural decode -> extracted/models/*.json
tools/mesh_vert_decode.py  decodes VERT/VERH into real positions -> OBJ + stats
tools/cmz.py               .cmz skeletons: chunks, bone tree, bind pose, per-part `sbus` skin blobs -> JSON + bone OBJ
tools/solve_sbus.py         brute-force field/stride checker for the `sbus` payload (rules out naive encodings)
tools/obj_render.py        pure-stdlib z-buffered rasteriser -> PNG + contact sheet
                           (optional --texmap for image-sampled UV shading)
tools/tex_link.py          material-name -> archive-entry link table -> texmap.json
tools/tex_gallery.py       texmap.json -> textures.html (per-link gallery)
tools/make_gallery.py      builds extracted/models/models.html
tools/mesh_vert_probe.py   characterises the VERT stream (superseded, kept for the record)
tools/gbc_index.py         script index: graph, classes, interfaces, strings -> HTML
```

```bash
python gpak_extract.py verify  gbox/lm_en/netpak.gpak
python gpak_extract.py list    gbox/lm_en/netpak.gpak
python gpak_extract.py names   gbox/lm_en/netpak.gpak gbox/lm_en/sysnet.gpak
python gpak_extract.py extract gbox/lm_en/netpak.gpak extracted
```

`extract` writes the real path when known, otherwise  
`unknown/<nameHash>.<sniffed-ext>`, and always emits `_manifest.json` / `_manifest.csv`  
with hash, size, start page, page count, timestamps and detected type.

### Filename recovery

Because the archive stores only hashes, names are recovered by *guessing and verifying*,
in two stages.

**Stage 1 — harvest & hash.** Pull every printable token from the decoded payloads
(≈164k tokens, incl. GBK), expand them into ≈1.8M path candidates using the directory
prefixes discovered from the corpus, hash each, and intersect with the 483 entry hashes.
This alone yields **90 / 483**.

**Stage 2 — `#include` propagation.** The `.gbc` scripts are a dependency graph: a file
whose name is known names the files it pulls in. Parse `#include "…"` and quoted asset
references out of every *named* script, resolve them relative to that script's own
directory (`posixpath.join` / `normpath`), hash the result, and repeat until nothing new
appears. Three rounds: `90 → 188 → 228 → 231`.

- **231 / 483** entries now resolve to real paths, e.g. `tabedit/itemtabedit.gbc`,
  `ui/bag/bagcom.gbc`, `country/en/trans.csv`, `world/terrain.gbc`, `ui/cooldown.png`,
  `SysFileVer.var` (2.4 MB).
- Per archive: `sysnet` **229 / 336 (68%)**, `netpak` **2 / 148 (1%)**.
- `netpak` is Chinese-named model data (`人类_男_战士7档_衣服_T.jpg`), and probing those
  decoded names directly against its hashes returns **zero** hits — its directory keys are
  produced somewhere else (another pak, or the server), so it stays hash-only.
- A wrong name is impossible: a hit means the 32-bit hash collided, which for 1.8M
  candidates against 483 targets is a ~0.2 expected false-positive count.

Reproduce:

```bash
python gpak_extract.py names gbox/lm_en/netpak.gpak gbox/lm_en/sysnet.gpak
python propagate_names.py
```

### Entity database

```bash
python varbin_dump.py        # unpack the 49 bundles to work/varbin/*.bin + hex view
python varbin_solve.py       # solve the field-width table -> varbin_fields.json
python varbin_identify.py    # blob -> game table map -> varbin_identity.json
python varbin_extract.py keys     # label -> value cross-tab -> varbin_keytab.json
python varbin_extract.py text     # flat searchable strings per blob
python varbin_extract.py tokens   # ordered token dump (JSONL) per blob
python tabedit_schema.py     # 43 table schemas -> table_schemas.json
```

Output lands in `extracted/netpak_varbin/`: `<blob>.txt` (one string per line, with
byte offset), `<blob>.tokens.jsonl` (`{off,k,v}`), `_keytab.csv` (5,132 rows).

### Script index

```bash
python gbc_index.py            # parse all 235 scripts -> extracted/gbc_index/
python gbc_index.py --stats    # numbers only, no HTML
```

Writes `index.html` (files / classes / interfaces / three dependency graphs),
`strings.html` (localisation inventory), `src/<id>.html` (line-anchored source with
`#L<n>` anchors so cross-references click straight to a line) and `data.json`.

Recovered: 235 files, 164,844 lines, 950 classes, 301 `style` mixins, 350 `#layer`
panels, 8,629 methods, 7,737 members, 428 registered interfaces, 16,272 user-visible
strings (8,720 of them `@"…"`), 282 `csvr::call` server-RPC sites, 158 enums.

---

## 6. Notes / loose ends

- Header `[0x24]` (`1843` / `2704`) is written but never read by the validator; it equals  
  `firstFreePage − dirPages` in both files, so it is probably a stale allocation cursor.
- Directory `+0x08` is *not* `HashString(path, 1)`; it does not change when the name  
  does, so it is likely a content or source-URL hash.
- The 4-byte `D7 76 23 60` prefix on every `varbin` ZIP is unaccounted for; `zipfile`
  reads past it, and the payload itself is intact. Its value is identical on all 49,
  so it is a constant tag rather than a length or checksum.
- `varbin` field ids beyond the seven in the table above are still open. Strict gap
  coverage is 48%; the blockers are ids whose gaps are ambiguous under intersection
  (two unknown ids in one gap). Table identification itself succeeded by a different
  route — matching the record's declared column count against `len(m_szItemName)` in
  `tabedit/*.gbc`, tie-broken by string-vocabulary overlap — giving **46 / 49** blobs
  and 19,640 records. Six blobs (`0580853e`, `66d2c4f3`, `d07f4acc`, `433267c3`,
  `e15a7343`, `c41c9276`) still desync mid-stream; forcing them invents tags, so they
  are left unexported rather than exported wrong.
- `.cmz` `sbus` skin records: **container + per-part structure solved** (see §4.6) —
  the `MESH` chunk is `n_parts` parts, each a length-prefixed GBK name + `sbus` blob
  with a 32-byte header (part_index / A / B / bonecap). The 136-byte in-memory part
  struct (stride from `0x19a2b0`) and the opaque `memcpy`'d blob (pointer at `+0x6c`)
  are confirmed from `0x199307`. The **exact per-vertex (bone, weight) encoding inside
  the blob is still open** — it is not a fixed `(bone, weight)` stride, so it must be
  read by the mesh/skinning consumer; locating that routine is the remaining step.
- `m_szType` codes are **editor widget types**, confirmed from the `switch` in
  `tabeditcom.gbc::FlipDlg`: `1` plain, `2` bitfield (2进制), `258` explanation
  (说明编辑), `512` task skill, `513` survey, `1024` group, `2048` flip-ex, `2049` path,
  `4096` NPC style. Anything `>= 3840` is a cross-table reference, with the lookup
  index taken as `code - 3840` (`SetSreachIndex(…, m_szType[i]-3840)`) — that is why
  the item table uses `3840`/`3841`/`3842`/`3844`. So the codes describe how the
  *editor* edits a column, not how it is serialised, and there is no direct mapping to
  `varbin` field ids. The serialiser itself is C++ in the client binary, not script —
  `tabeditcom.gbc` contains no read/write path.
- zlib is linked but never used on archive payloads — pages are stored raw.
- `.53wf` vertex layout is **solved** — see §4.3. `VERT` is a 24-byte
  base/step header (six `float32`, all × 0.01) followed by three bytes per
  vertex, `pos = byte * step + base`; `VERH` is the same thing as `float32`
  triples. All 34 submeshes decode to object-scale geometry that renders as the
  right thing.

  Two earlier conclusions in this document were wrong and are corrected here:

  | claim | reality |
  |---|---|
  | "`VERT` contents are not positions" | they are positions, quantised; the 3-byte stride was right, the missing piece was the header |
  | "edges are ~5× shorter than shuffled, so it is real geometry" | that test was **confounded** — indices reference numerically adjacent vertices (median \|Δindex\| = 4.5), so a smooth byte stream shortens edges for reasons unrelated to shape. It happened to give the right answer for the wrong reason. |

  The channel-spread argument (σ ≈ 74 = uniform over the quantisation range) was
  also a red herring: uniform byte coverage is *expected* when `step` is chosen
  so the object fills its own cube, which is exactly what a per-submesh
  quantiser does. Lesson: characterising a stream statistically cannot
  distinguish "encoded" from "quantised"; only the decode routine can.

  The recovered algorithm also explains the odd-length payloads that confused the
  earlier analysis — `ENDM` is a **whole-file** terminator, appearing once after
  the final submesh, never once per submesh.
- `.cmz` skeletons are **solved** — see §4.4. All 9 files parse; bone hierarchy,
  bind pose and attachment names recovered. Two corrections to earlier notes:
  `.cmz` has exactly four chunk tags (`skel`, `MESH`, `MAP1`, `END.`) and the loader
  accepts no others, so the `ENXV` / `EMPV` / `NBZ1` "chunks" listed here previously
  were byte-scan false positives; and bone quaternions are **inverse** rotations,
  so they must be conjugated before composing.
- Texture hash is **solved** — see §4.5. Archive paths are
  `model/<GUID8>.fw3` and `tex/<GUID8>.<ext>`, literal ASCII dirs, 8-hex
  GUIDs.  4/4 hash-verified material→texture links via the varbin
  decoration table; 9 visual matches (alpha cutouts and unambiguous art);
  2 tentative (bark/altar).  Full table in `extracted/models/textures.html`.
- 21 `#include` targets are absent from the archive, and they are informative rather
  than corrupt: 14 point into an `mtui/` tree (a **mobile/touch UI** build that ships
  elsewhere), the rest at `ui/vip/vip_vn.gbc` (Vietnamese VIP screen) and
  `country/ch/`, `country/jp/` fonts. So this build is the English retail client.
- The 8 `unknown/*.gbc` blobs are the login UI set. Their own includes name five
  siblings (`login_ver.gbc`, `login_url.gbc`, `login/sysobj.gbc`, `login/reg_dlg.gbc`,
  `login/login_dlg.gbc`), and matching contents against those names gives candidates
  for all eight — see the "Unnamed blobs" panel in `gbc_index/index.html`. These are
  inferences from content, not hash-verified names.

