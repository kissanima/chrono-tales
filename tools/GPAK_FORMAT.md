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
| 0x10 | u32     | reserved, always `1000 + entryCount`                               |       1148 |       1336 |
| 0x14 | u32     | **entry count**                                                    |        148 |        336 |
| 0x18 | u32     | write time (unix)                                                  | 1380183591 | 1380180949 |
| 0x1C | u32     | **entry capacity** = `(dirPages*4096 − 84) / 536`                  |        152 |        336 |
| 0x20 | u32     | directory page count                                               |         20 |         44 |
| 0x24 | u32     | **used data pages** = `pageCount − dirPages − freePages`           |       1843 |       2704 |
| 0x28 | u32     | **total pages** = `fileSize >> 12`                                 |       1892 |       2781 |
| 0x2C | u32     | free page count                                                    |         29 |         33 |
| 0x30 | u32     | first free page                                                    |       1863 |       2748 |
| 0x34 | 32 B    | zero                                                               |            |            |

`[0x24]` is a **derived counter, not a source of truth**: it equals
`pageCount − dirPages − freePages`, and also `firstFreePage − dirPages` because
the packer allocates data pages contiguously upward from `dirPages` and the free
list is just the trailing run (`pageCount − firstFreePage == freePages` in both
archives).  The validator at `0x417210` never reads it, so a stale value would
go unnoticed — but both shipped archives are consistent.

`[0x10] == 1000 + entryCount` in both archives.  No reader has been found for
it either; it looks like a schema/version tag that happens to be derived from
the entry count.

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
| 0x004 | u32   | archive write time (unix)                               |
| 0x008 | u32   | **source file time** (unix) — see below                 |
| 0x00C | u32   | second timestamp                                        |
| 0x010 | u32   | **size** in bytes                                       |
| 0x014 | u32   | **start page**                                          |
| 0x018 | u16   | constant marker `0xFA21`                                |
| 0x01A | 510 B | **not initialised** — stale heap bytes written verbatim |

`+0x08` is **not a hash**. Earlier notes called it a "second hash" because it
does not vary with the name; it is a unix timestamp, and it is the only field in
the archive that preserves the *original* asset dates:

| archive | `+0x08` range | `+0x04` / `+0x0C` range |
| ------- | ------------- | ----------------------- |
| netpak  | 2011-03-30 … 2013-09-26 | 2013-09-26, 8 minutes wide |
| sysnet  | 2011-04-11 … 2013-09-26 | 2013-09-26, 50 minutes wide |

All 484 entries land inside 2005…2014, which a 32-bit hash could not do. The
hour histogram is a business-day curve once shifted to UTC+8 (sysnet peaks at
UTC 05:00 = 13:00 CST), and `+0x08 ≤ +0x0C` holds for **every** entry in both
archives — consistent with a file creation/modification time stored alongside
the pack time. `+0x04` and `+0x0C` are both pack-run timestamps.

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

### 2.7 Per-file compression wrapper — the `D7 76 23 60` tag

Some cached payloads are **not** raw files: they carry a 4-byte compression tag
written by the client itself. This is not a packer artefact — the writer is at
`0x53972F`:

```
call 0x427140                 ; get a destination buffer of size N
mov  dword ptr [eax], 0x602376d7
lea  edi, [eax+4]
rep  movsd / rep movsb        ; memcpy(buf+4, src, N)
```

and the matching reader dispatches on the first dword at `0x539979`:

| tag | meaning | loader behaviour |
| --- | ------- | ---------------- |
| `0x602376D7` (`D7 76 23 60`) | compressed | `0x56E1E0(buf+4, len−4)` → `0x56E080(…, 3, …)`, i.e. minizip/zlib inflate |
| `0x8A8E9468` | stored + additive check | allocates `len−4`, folds bytes into eight 8-bit accumulators (`acc[i % 8] += data[i]`) |
| `0xBE119407` | stored verbatim | `memcpy(dst, src+4, len−4)` at `0x5251A0` |

What the shipped data actually contains:

| tag | count | payload |
| --- | ----: | ------- |
| `0x602376D7` | 48 | every real `varbin` (a ZIP whose single entry is named `varbin`) |
| `0xBE119407` | 7 | small `*.bin` blobs (`[07 94 11 be]` + raw stream) |
| `0x8A8E9468` | 0 | recognised by the loader, never emitted by this build's packer |

So the tag is not "unaccounted for" and it is not on *every* varbin either: the
49th `*.zip` in the extraction (`c41c9276`) has no tag because it is not a
varbin at all — its ZIP entry is `jumpdown.caf`, an audio asset. That single
misfiled blob is also why it appeared in the list of `varbin` streams that
"desync mid-stream" (§6).

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

`varbin` is **the same tagged serialisation as `SysFileVer.var`, with the
encryption removed** (see [`SYSFILEVER.md`](SYSFILEVER.md)). The tag really is a
*type* tag, and each payload is a single fixed-schema table:

| tag | payload |
|---|---|
| `0x00`–`0x0F` | small int; the value *is* the tag |
| `0x10` | `u8` |
| `0x11` | `u16` |
| `0x12` | `i32` |
| `0x13` | `f32` |
| `0x14` / `0x15` / `0x21` | `i64` |
| `0x16` | vec3, three `u32` |
| `0x20` | `u32` |
| `0x30` | empty string, no payload at all |
| `0x31` | `u32 charCount` + `charCount*2` bytes UTF-16LE, **no checksum** |
| `0x32` | `u32 len` + `len` raw bytes |
| `0x40` | array, count as `u8` |
| `0x41` | array, count as `u32` |
| `0xFF` | null |

Compared with the `.var` form there is no seed, no rolling XOR key, no string
checksum, no array trailer, and `0x32` blobs are not dword-padded. `tools/varfile.py`
reads both shapes; `tools/varbin_tree.py` drives the varbin side.

**All 48 payloads parse to their exact last byte** — 10,104,187 bytes holding
**19,640 records**. Each payload is one table: every record in it has the identical
field count, from 4 up to 88.

> An earlier pass read this as a property bag and tried to solve a positional width
> table from the "gaps" between strings. That stalled at 48% because the tag was
> taken to be a *schema field id* rather than a type — record boundaries were never
> fixed, so the widths could not converge. The 48% figure is superseded; the
> structure is fully known.

**Records are fixed-schema rows.** Taking `0580853e` (1,833 records × 66 fields) as
the example:

| field | content |
|---|---|
| 0 | entity id (`u32`) |
| 1 | Chinese name, e.g. `希望草原_采集_幽泉水井` |
| 3 | English name, e.g. `Deepspring Well Breath` |
| 25 | **property sub-array**, alternating label/value: `['分类','希望草原NPC','添加人','sxn']` |
| 28 | description text |
| 32 | spawn transform `[kind, x, z, (quat...)]` |
| 42 | interaction tag, e.g. `采集效果` |

Field 25 is where the old property-bag statistics actually came from — it is a
*field*, not the record layout. The label census it produced is still valid:

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

Blob roles split cleanly by string density: **39 of 48** are string-heavy
(strings > 50% of tokens), 6 are mixed, and 3 (`cc5de97f`, `da80ff38`, `4514d98f`)
are dominated by `0x32` raw blobs — they are map/tile payloads, not tables.
`c41c9276` is not a varbin at all: it is `jumpdown.caf`, an audio asset filed
under the wrong extension.

#### File header — two integers, then the first field

Every payload opens with the outer array header, then the first record's own header:

```
0x41 <u32 recordCount>      outer array
0x40 <u8  columnCount>      first record, i.e. its field count
```

`record count` uses `0x40 <u8>` below 256 and `0x41 <u32>` above; `column count` is
the record length and is identical for every record in the payload.

What an earlier pass read as a third "flag word" is the **first field's tag plus
payload** — `12 32 ad 35 80` is `i32` with a 4-byte body. Its high bit looked
"always set" because the leading byte is the `0x12` type tag rather than a bitfield,
and it "repeated between unrelated tables" because the tag is doing its job.

**Identification is two signals plus one hard rule.** Column count is the first
signal: it must equal the editor's `m_szItemName` length. The second is the
**field-type signature** — each schema declares a widget code per column, and
those codes map onto the serialised kind (`1`/`2` → int, `256`/`257`/`258` → str,
`1024`/`2048+`/`>=3840` → list). A payload's decoded records must agree
column-by-column, and that agreement is *scored*, not assumed.

The hard rule sits between them: **no `tabedit` schema declares a blob column**,
so any payload whose records contain a `bytes` field is not a table at all. That
is what the six map payloads are — they had been filed under `forcetabedit`
purely because they happened to have six columns.

Assignment is also **one-to-one**: an editor owns at most one payload. Without
that rule the top-scoring candidate wins for *every* blob that ties on column
count, which silently files unrelated tables under one editor — `pathedit` was
claiming three 5-column payloads, `unionbuffedit` two 8-column ones. Scoring
every (blob, table) pair globally and handing each table to its best claimant
resolves both. A payload that loses every table it wanted is reported as
`unshipped` (if it is a known editor-less table) or `ambiguous` — never guessed.

| blob | records | cols | table | conf |
|---|---|---|---|---|
| `87d142c2` | 4,122 | 52 | **itemtabedit** 物品表编辑 | exact |
| `e15a7343` | 1,982 | 2 | *sceneobjtab* — 场景物件表: scene name -> 64 object ids, no shipped editor | unshipped |
| `0580853e` | 1,833 | 66 | **npctabedit** | exact |
| `433267c3` | 1,399 | 88 | *questtab* — 任务表 (天梯/主线/支线/日常), 88 columns, no shipped editor | unshipped |
| `47bf0e8a` | 1,298 | 7 | **droptabedit** 掉落表编辑 | signature |
| `fcf45131` | 1,193 | 57 | **skilltabedit** | exact |
| `fcef78c4` | 1,157 | 22 | **npcmaketabedit** | exact |
| `6b1f6475` | 1,020 | 37 | **bufftabedit** | exact |
| `8664461b` | 924 | 24 | **effectabedit** | exact |
| `66d2c4f3` | 735 | 3 | **paramtabedit** 参数表编辑 | exact |
| `15fe62ab` | 560 | 32 | **maketabedit** 合成表编辑 | exact |
| `60792c4a` | 331 | 8 | *starbufftab* — 星运表: buff effect definitions referenced by startabedit's per-sign lists, 8 columns, no shipped editor | unshipped |
| `33e159c6` | 329 | 6 | **triggertabedit** 触发器表编辑 | signature |
| `4ea8321c` | 309 | 11 | **gifttabedit** 礼包表编辑 | exact |
| `09f8db1d` | 262 | 20 | **daytaskedit** 每日成就表编辑 | exact |
| `6a64e3da` | 255 | 18 | **MapAreaDlg** | exact |
| `b0a40a84` | 252 | 5 | *nodegraphtab* — 寻路节点图: node id, name, x, z, neighbour ids, 5 columns, no shipped editor | unshipped |
| `57defdf0` | 244 | 4 | **exptabedit** 经验表编辑 | exact |
| `cbc16f9c` | 213 | 12 | **synmaterialtabedit** 物品合成表编辑 | signature |
| `d483aad3` | 177 | 12 | **EquipTableEdit** 装备表编辑 | signature |
| `8d861de9` | 159 | 15 | **gameshopedit** 游戏商城表编辑 | signature |
| `55b90d45` | 154 | 8 | **unionbuffedit** 公会BUFF表编辑 | signature |
| `0e747450` | 136 | 12 | **worldmapdatatabedit** 大地图信息显示表编辑 | signature |
| `486bf1bd` | 128 | 5 | **transtabedit** 传送表编辑 | signature |
| `5218cf47` | 80 | 5 | **pathedit** | signature |
| `37a1742e` | 65 | 15 | **titletabedit** 称号表编辑 | signature |
| `93c34746` | 49 | 25 | **shoptabedit** 掉落表编辑 | exact |
| `9f9048cf` | 46 | 29 | **worldtabedit** 场景表编辑 | exact |
| `204d427b` | 41 | 12 | **lifeskilltabedit** 生活技能表编辑 | signature |
| `d07f4acc` | 27 | 27 | **peteditlist** 宠物表编辑 | exact |
| `dd447626` | 26 | 12 | *starsoultab* — 星魂表 (star soul definitions), 12 columns, no shipped editor | unshipped |
| `615fbfd5` | 20 | 23 | **daliytabedit** 日常活动表编辑 | exact |
| `455cc3b0` | 18 | 17 | **daliyactivitytabedit** 每日活动表编辑 | exact |
| `fe4857b1` | 18 | 9 | **runetabedit** VIP表编辑 | signature |
| `4a5475b2` | 12 | 7 | **startabedit** 星座表编辑 | signature |
| `96c9244b` | 12 | 15 | **viptabedit** VIP表编辑 | signature |
| `af3407ec` | 12 | 6 | **ziptabedit** 动作包定义表编辑 | signature |
| `8898f905` | 10 | 16 | **diamondedit** 水晶割据表编辑 | signature |
| `1e806186` | 8 | 6 | **forcetabedit** 势力表编辑 | signature |
| `fcd47eb6` | 7 | 9 | **specialtabedit** 特殊活动表编辑 | signature |
| `c3eba311` | 6 | 39 | **passtabedit** | exact |
| `6fb967a3` | 5 | 14 | **tasktabedit** 任务表编辑 | exact |

**42 / 48 payloads are tables**, carrying **19,634 of the 19,640 records**; the
remaining 6 are the map payloads, one record each. Confidence splits
**19 `exact`** (unique column count *and* uncontested), **18 `signature`** (won
on field-type agreement over a rival), **5 `unshipped`**, **6 `map`**.

The five editor-less tables are kept with positional `c0..cN` headers rather than
discarded:

| blob | rows × cols | what it is |
|---|---|---|
| `433267c3` | 1,399 × 88 | 任务表 — 789 天梯 / 324 主线 / 190 支线 / 91 日常 quests |
| `e15a7343` | 1,982 × 2 | 场景物件表 — scene name → up to 64 object ids |
| `dd447626` | 26 × 12 | 星魂 definitions (icon, id list, value band) |
| `60792c4a` | 331 × 8 | 星运 buff effects — the leaves `startabedit` points at |
| `b0a40a84` | 252 × 5 | 寻路节点图 — node id, name, x, z, neighbour ids |

The last two are the reason one-to-one matters. `60792c4a` and `55b90d45` both
have 8 columns, and `unionbuffedit` is the only 8-column editor; but `55b90d45`'s
rows are `["A级1", …, 是否公会满员, 公会在线人数, 公会平均等级, …]` — literally the
guild-buff columns — while `60792c4a`'s are `[id, 星运_击中_中毒_蓝, "Forcible
Poison", …, description]`, i.e. named entity records with no guild field at all.
Same story for 5 columns: `5218cf47` holds real `路径_*` waypoint lists
(`pathedit`), `486bf1bd` holds `[地点名, 世界, 坐标, …]` teleports
(`transtabedit`), and `b0a40a84` holds neither.

Two shipped editors carry a **stale title string** — `shoptabedit.gbc` says
`掉落表编辑` (copied from `droptabedit.gbc`) and `runetabedit.gbc` says `VIP表编辑`
(copied from `viptabedit.gbc`). The *filename* is the reliable identity, not the
title; two more (`pathedit.gbc`, `npctabedit.gbc`) have no title at all.

There are **43 table editors** (`tabeditcom.gbc` and `tabeditlist.gbc` are the
editor *framework* and are excluded from that count). 37 of them are matched by a
payload; the other six have no netpak table, so they are either server-only or
were never populated. The 88- and 2-column payloads have no schema to match at
all, which is why they land in `unshipped`.

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

### 4.6 `.cmz` `sbus` skin records — **SOLVED**

> **Status: per-vertex mesh + rigid skin fully decoded.** The blob is the
> skinned geometry itself (positions, normals, UVs, bone weights); the matching
> `.fw3` (`.53wf`) is just a re-export without skinning.

The `sbus` blob is a packed, **variable-length** record: a small header
followed by `n_verts` vertex records and an index buffer. The packed
encoding is critical — a `u8` flag sits between two `u32` fields, so any
4-byte-aligned parse is misaligned by one.  Layout recovered by reading the
per-record parser at VA `0x59A640` in `gbox/lm_en/chronotales.exe`
(called from `0x59A2E0`, which loops over parts with `stride 0x88 = 136`).

**Header** (offset 0 is the `sbus` tag). The loader first reads a *marker*
word at offset 8 and branches on it, so two variants exist:

```
 0  u32  'sbus'
 4  u32  part_index
 8  u32  marker
```

`marker == 0` — **variant 2**, used by all 15 shipped parts:

```
12  u8   1                      <- flag; breaks 4-byte alignment
13  u32  n_verts
17  u32  n_faces                (= number of triangles in the index buffer)
21  u32  unknown                (stored to struct+0x10; 0 in all 15 parts)
25  u32  unknown                (element count of the 8-byte container at
                                 struct+0x24; 0 in all 15 parts)
29  u32  n_uv_sets              (1 or 2 in the shipped files)
33  u8   wide_influence_flag    0 -> n_influences is u8; != 0 -> u16
34  f32  base_x, step_x, base_y, step_y, base_z, step_z    (6 floats)
```

`marker != 0` — **variant 1**, not present in this build. The marker word *is*
`n_verts`, there is no flag byte and no `wide` byte, and everything from
`n_faces` onward follows directly at offset 12. This is why the original
"seven u32 header" reading was off by one: the flag byte is conditional, and
it only exists in variant 2.

`step` and `base` are the **un-scaled** floats written to the file; the
engine multiplies both by `0.01` (the same constant at VA `0x6D0738` that
the `.53wf` vertex decoder uses). The six floats are stored in the order
`base_x, step_x, base_y, step_y, base_z, step_z` — confirmed by the six
sequential reads at `0x59A957`..`0x59A9BC` and the matching
`fild / fmul / fadd` triples at `0x59AA39`, `0x59AA5E`, `0x59AA81`.

**Per-vertex record** (exactly `12 + 4*n_uv_sets + (1 or 2) + 3*n_influences`
bytes; the shipped files are all rigid 1-bone so each record is **20 bytes**):

```
u16 px, py, pz     pos = u16 * step + base, with step,base * 0.01
u16 nx, ny, nz     nrm = u16 * 3.0518509447574615e-05 - 1.0   (VA 0x6D073C, 0x6CEC4C)
[n_uv_sets x (u16,u16)]  uv = u16 * 3.05185e-5   (the first set is kept;
                        the stored values never exceed 32767, so every
                        decoded UV lands in [0,1] - verified on all 9 files)
u8/u16 n            number of bone influences (1 in every shipped file)
n x (u8 bone, u16 weight)   weight = u16 * 1e-4   (VA 0x6D457C, 10000 = 1.0)
```

Only the **first** UV set is retained (`test edi,edi; jne` at `0x59ABDC`) but
every set is consumed. Two shipped parts (`482f1708`, `d1500c0c` part 1)
declare `n_uv_sets == 2`; in both, the second pair is **byte-identical** to
the first, so discarding it loses nothing.

Influence handling splits at four:

```
n <= 4  (0x59ADA4)  every pair stored; a raw weight of 0 trips a debug
                    assert but the value is still written
n >  4  (0x59AC4C)  every pair still consumed from the stream, but only the
                    first four with a raw weight > 1 (> 1e-4) are kept
```

`cmp ax,1; jbe` is an **unsigned** comparison against the raw u16, so it
drops raw 0 and raw 1 (0.0 and 0.0001) — it is near-zero culling, not a
"weight of 1 means no influence" marker. The `n > 4` path additionally
accumulates a running weight total at `[esp+0x64]` that nothing ever reads:
dead bookkeeping from a normalisation step that was removed.

**Index buffer** — `n_faces` triangles immediately after the vertex
records.  The engine checks `cmp n_verts, 0x400 (1024)` at `0x59AFB8` and
chooses:

```
n_verts <  1024  :  one u32 per triangle, indices packed as
                     (i2 << 20) | (i1 << 10) | i0
n_verts >= 1024  :  three u16 per triangle
```

**Validation** (`tools/sbus_validate.py --strict`, 9/9 files, 15/15 parts):

- every decoded bone index is in `[0, n_bones)`.
- weight sum is exactly 1.000 for every vertex.
- every decoded normal is unit length (`|n| = 1.000`).
- every triangle index is in `[0, n_verts)`.
- the stream is **fully consumed** on all 15 parts. Where bytes remain
  between one `sbus` blob and the next marker they are not mesh data at all:
  they are the *next* part's `u32 name_len | GBK name | u32 record_count`
  header (22–33 bytes), which is what pins the MESH layout down.

**Part names** are recovered deterministically rather than by scanning.
MESH stores each part as

```
u32 name_len | GBK name (NUL terminated) | u32 record_count | sbus blob
```

so the label sits immediately in front of its own blob. `record_count` is the
number of `sbus` records in the part and is `1` everywhere, which is the
sanity check that keeps the backward walk out of the previous part's vertex
stream. `tools/sbus_decode.py:part_name()` implements it; all 15 parts resolve
with no ambiguity, including `机甲摩托_前轮` (front wheel), which the earlier
forward scan could not reach:

| file      | part | name                        |
|-----------|-----:|-----------------------------|
| 2682c5d5  | 0–4  | 机甲摩托_前轮 / 后轮 / 车身 / 车头 / 轮眉架子 |
| 482f1708  | 0    | b_faxing00                  |
| 5995da06  | 0    | 人类_男_战士7档_衣服_M         |
| 5e493068  | 0    | 人类_男_默认_鞋子_M            |
| 72b5ee25  | 0    | 人类_男_战士7档_手套_M         |
| a7b5710e  | 0    | 人类_男_战士7档_护臂_M         |
| d1500c0c  | 0–1  | 人类_男_战士7档_护腿_M / 护腿01_M |
| f2d259a2  | 0–1  | 人类_男_脸型09_M / 眼珠03_M     |
| ff725ba4  | 0    | 人类_男_战士7档_鞋子_M         |

| file      | parts | verts | faces |
|-----------|------:|------:|------:|
| 2682c5d5  | 5     | 4191  | 2932  | mech body + 4 rigid parts
| 482f1708  | 1     | 137   | 172   | hairstyle
| 5995da06  | 1     | 326   | 446   | tier-7 clothes
| 5e493068  | 1     | 140   | 196   | default shoes
| 72b5ee25  | 1     | 590   | 592   | tier-7 gloves
| a7b5710e  | 1     | 100   | 146   | tier-7 arm guards
| d1500c0c  | 2     | 581   | 694   | tier-7 leg guards
| f2d259a2  | 2     | 376   | 582   | face type 09
| ff725ba4  | 1     | 320   | 386   | tier-7 shoes

`tools/sbus_obj.py` exports each file to a single multi-group OBJ
(Z up, metres) and `tools/obj_render.py --up z` produces a recognisable
contact sheet of all 9 files at `extracted/models/skin_sheet.png`.

**The 136-byte in-memory part struct** (stride confirmed at the indexer
`0x19A2B0 = base + idx*0x88`) is filled by the parser and then consumed
opaquely by the animation system.  Field meanings (offsets in the 136-byte
struct):

```
+0x00  ptr      per-vertex array, 60 bytes each (see below)
+0x04  u32      n_verts
+0x08  u32      n_faces
+0x0c  u32      part_index
+0x10  u32      unknown (= header field at +21)
+0x14  char[]   triangle indices, n_faces * 3 u16 (resized at 0x59AF7C)
+0x24  object   container with a vtable, constructed as (stride 8,
                count = header field at +25) at 0x59A93A; its destructor at
                0x58F300 unlinks it from a global list at 0x79BB58
+0x54..0x5F    AABB min (3 floats), seeded to +5e6 at 0x59A6C1
+0x60..0x6B    AABB max (3 floats), seeded to -5e6 at 0x59A6DA
+0x7c  ptr[]    array of child part objects, +0x84 = count
```

The per-vertex record is 60 bytes (`n_verts * 0x3C` allocated at `0x59A917`):

```
+0x00  f32 x, y, z
+0x0c  f32 nx, ny, nz
+0x18  u32 n_influences
+0x1c  u32 bone[4] / f32 weight[4] interleaved, 8 bytes per pair
       (copied in one `rep movsd` of 8 dwords at 0x59AF34)
```

The two residual unknowns (`+21` and `+25`) are **0 in all 15 shipped
parts**, so their semantics cannot be recovered from data. What is proven:
`+21` is copied verbatim to struct `+0x10`, and `+25` is the element count
of the 8-byte container at struct `+0x24`. There is also no writer — the
`sbus` magic `0x73756273` occurs exactly **once** in `chronotales.exe` (the
compare at `0x59A6FF`), so the `.cmz` files were produced by an offline tool
that is not shipped with the client.

---

## 5. The pipeline

```
tools/gpak.py              format library: page decode, cipher, CRC, MPQ hash, archive reader
tools/gpak_extract.py      CLI: list | verify | names | extract
tools/gpak_write.py        writer: page/dir/free-list emitter, byte-exact rebuild()
tools/gpak_repack.py       CLI: roundtrip | fresh  (verifies the writer)
tools/names.json           recovered filename table (hash -> path)
tools/propagate_names.py   #include-graph filename propagation
tools/varfile.py           `.var` + `varbin` tagged-tree decoder (both modes)
tools/varbin_tree.py       CLI: check | show | schema | csv  (tagged-tree view)
tools/varbin_extract.py    entity DB: tokens | text | keys (label->value cross-tab)
tools/varbin_identify.py   blob -> game table: column count + field-type signature,
                           one-to-one assignment (exact/signature/unshipped/map)
tools/varbin_tables.py     exports the 42 identified tables to extracted/tables/*.csv
tools/varbin_solve.py      superseded: the old positional width-table solver
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
python gpak_extract.py wrap    gbox/lm_en/netpak.gpak
python gpak_extract.py names   gbox/lm_en/netpak.gpak gbox/lm_en/sysnet.gpak
python gpak_extract.py extract gbox/lm_en/netpak.gpak extracted
```

`wrap` tallies the compression tags of §2.7; `gpak.unwrap(blob)` strips them
(and inflates the ZIP ones) and is a no-op on untagged payloads, so it is safe
to call on every entry.

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
python varbin_tree.py check  # decode all 48 payloads, assert byte-exact
python varbin_tree.py show 0580853e   # pretty-print one payload as JSON
python varbin_identify.py    # blob -> game table map -> varbin_identity.json
python varbin_tables.py      # 42 identified tables -> extracted/tables/*.csv
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

- ~~Header `[0x24]` is a stale allocation cursor.~~ It is the **used data page
  count**, `pageCount − dirPages − freePages` — see §2.1. Both archives are
  self-consistent; the validator simply never reads it.
- ~~Directory `+0x08` is a content or source-URL hash.~~ It is a **unix
  timestamp** holding the original source file time, 2011-03 … 2013-09, and
  `+0x08 ≤ +0x0C` on every entry — see §2.2. It was mistaken for a hash because
  it does not change with the name, which is exactly what a file time does.
- ~~The 4-byte `D7 76 23 60` prefix on every `varbin` ZIP is unaccounted for.~~
  It is the client's own compression tag `0x602376D7`, written at `0x53972F` and
  dispatched at `0x539979` alongside two siblings — see §2.7. It is also not on
  every ZIP: `c41c9276` has none because it holds `jumpdown.caf`, not a varbin.
- ~~`varbin` field ids beyond the seven in the table above are still open; strict gap
  coverage is 48%.~~ **Resolved.** The tag is a *type* tag, not a schema field id, so
  there was never a width table to solve — see §4.2. All 48 payloads now decode to
  their exact last byte via `tools/varfile.py`, yielding **19,640 records**. The
  earlier desyncs were an artifact of the misreading, not of the data: `433267c3`,
  `e15a7343`, `0580853e`, `66d2c4f3` and `d07f4acc` are ordinary tables (they are
  `unshipped`, `unshipped`, npctabedit, paramtabedit and peteditlist respectively),
  the six map payloads `4514d98f` / `6010fb33` / `6b45f638` / `9feba4ac` / `cc5de97f`
  / `da80ff38` carry `0x32` raw tiles by design, and `c41c9276` is not a varbin at
  all — see §2.7.
- `.cmz` `sbus` skin records are **solved** — see §4.6. The earlier note claiming a
  32-byte / 7×u32 header was **wrong**: a `u8` flag sits between two `u32` fields, so
  every aligned read of that header is off by one. Once unpacked, the blob is the
  skinned geometry itself (positions, normals, UVs, weights, index buffer), which is
  why it looked like an opaque integer blob. Two header fields remain unexplained:
  `+21` (stored to struct `+0x10`) and `+25` (element count of an 8-byte container at
  struct `+0x24`). Both are **0 in all 15 shipped parts**, so they are not recoverable
  from data, and there is no writer to read them off: the `sbus` magic occurs exactly
  once in `chronotales.exe` (the compare at `0x59A6FF`), so `.cmz` is produced by an
  offline tool that is not shipped. The `wide_influence` (u16 count) and `n > 4`
  influence paths exist in the parser but are never exercised — every shipped part is
  rigid, one bone per vertex — so the u16-bone variant implied by the 512/1024
  bonecaps is untested here. Two structural facts came out of the disassembly and are
  implemented in `sbus_decode.py`: a **variant-1 header** exists for `marker != 0`
  (no flag byte, no `wide` byte, marker *is* `n_verts`), and the `n > 4` path consumes
  every pair but stores only the first four above a raw weight of 1.
  `sbus_validate.py --strict` is the regression test: bone range, weight sum == 1,
  unit normals and index range across all 9 files (6761 verts, 6146 tris).
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

