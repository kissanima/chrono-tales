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

| archive | `+0x08` range           | `+0x04` / `+0x0C` range     |
| ------- | ----------------------- | --------------------------- |
| netpak  | 2011-03-30 … 2013-09-26 | 2013-09-26, 8 minutes wide  |
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
| `0xBE119407` | 7 | small `*.bin` tables — see §4.2.1 |
| `0x8A8E9468` | 0 | recognised by the loader, never emitted by this build's packer |

`0xBE119407` was previously written off here as "recognised but never emitted
by this build". It is emitted seven times; the payloads are plain tagged var
trees and they decode exactly. See §4.2.1.

So the tag is not "unaccounted for" and it is not on *every* varbin either:
the 49th `*.zip` in the extraction has no tag because it is not a varbin at all.

**Correction — that blob is `e41c7990`, and it is not audio.** The entry
`c41c9276` recorded here previously was a conflation: `e41c7990` *is*
`C41C9276` (`gpak.hash_name("action/e41c7990.zip") == 0xC41C9276`), and the
asset manifest resolves it to `action/e41c7990.zip`. That ZIP holds five
**Cal3D skeletal animation clips** — `run.caf`, `stand01.caf`, `jumpdown.caf`,
`jumping.caf`, `jumpstart.caf` — each starting `CAF\0`. The earlier reading as
"`jumpdown.caf`, an audio asset" almost certainly came from the `.caf`
extension also being Apple **Core Audio Format**; here it is character
animation, and `netpak/action/` is the model action pack (43 `action/*.zip`
paths are defined in the table `ziptabedit`, of which only this one ships).

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

**Export invariant — prune, don't accumulate.** Because assignment is one-to-one,
correcting a tie-break *moves* a blob and the table that used to own it loses its
payload entirely. `varbin_tables.py` therefore deletes `*.csv` it did not write
this run, and `build_explorer.py` does the same for `data/t_*.js`. Without this,
an orphan from an earlier identification survives forever and silently inflates
every downstream count — which is exactly what happened when `actiontabedit.csv`
(a stale 10-row artefact of blob `8898f905`, since correctly assigned to
`diamondedit`) made the export read 43 tables instead of 42.

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

### 4.2.1 The `0xBE119407` wrapper — seven more tables outside the varbin DB

§2.7 lists three compression tags and notes that `0xBE119407` ("stored
verbatim") is *"recognised but never emitted by this build."* That is wrong:
seven `netpak` entries begin with it, for both the tag **and** the size/timestamp
pair, so this is a second `.var` container that happens to live outside a ZIP.

Each file is `u32 0xBE119407` followed by a **plain tagged var tree** — the
same grammar `varfile.Reader(plain=True)` already reads for a varbin payload,
just with the 4-byte tag stripped. All seven decode to their exact last byte:

| id | bytes | rows × cols | what the content is |
|---|---:|---|---|
| `1cce069d` | 324 | 7 × 6 | UI shortcut actions: `打开商城` / `打开背包`, `是否打开` |
| `ea6b3654` | 172 | 4 × 6 | level curve: `Level 1`, 50, 40000, 100 |
| `b241fb00` | 746 | 6 × 16 | social/chat macros: `xingli`, `Salute`, `[发起者] salutes.` |
| `d1fa89b8` | 979 | 11 × 15 | push-reward schedule: `推送奖励_20分钟`, `神秘礼物` |
| `7ff704dc` | 42 | 1 × 8 | single config row |
| `7278bb49` | 5 | 0 | empty table |
| `c20fea4b` | 5 | 0 | empty table |

They are **named tables**, not opaque blobs, so the count of genuinely
unidentified `netpak` content drops accordingly. They are deliberately *not*
folded into `varbin_tables.py`: that exporter's 42-table output is a load-bearing
invariant (one-to-one editor assignment, §4.2), and these have no `tabedit`
editor and no `varbin_identity.json` entry. Adding them there would also have
to solve their missing column names, which no shipped editor supplies.

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
versions; 13 files use `MAT3`, 9 use `MAT2`. The **only** difference is that
each `MAT3` record is followed by 8 zero bytes and `MAT2` records are not — read
`MAT2` with the `MAT3` stride and the parser walks past the end of the table
into the `MESH` header and invents a phantom material. Submesh *i* uses
material *i*. Texture names resolve to archive files through
`extracted/models/texmap.json` (see §4.5), so 17 of 19 material names now bind
|  |  |
| --- | --- |
to a real `tex/<guid>.jpg|png`.

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
consecutive strings, **and — decisively — in the six `.zmap` intro scene
maps (§4.7)**: every placed object carries its full
`modle=,model/<GUID>.fw3;tex=<name>,tex/<GUID>.<ext>;...` material spec,
and `world[1:]` scenery entries carry an explicit `model/…` + `tex/…` path
pair.  Hashing all 108 texture references from the scene maps reproduces
live archive entries — **108/108** — so the mapping is authoritative, not
inferred.

**Resolution status — all 19 distinct material names are hash-verified**
(full table in `extracted/models/textures.html`, generated by
`tex_link.py`):

| source | count | notes |
|---|---|---|
| varbin decoration table | 4 | 奇幻灌木, 平原草01, 灌木003, 蘑菇001 |
| `.zmap` scene specs | +15 | 16 names total, overlapping the above |

The zmap table **corrected three earlier guesses** that had been made
before the scene maps were decoded: `木01.jpg` / `木02.jpg` were swapped
(5A1005D7 / CF794CC6), the old "tentative" 圣坛003 file was actually
石头03 (CCAED5C9), and 顶02 / 墙01 pointed at look-alike files.  It also
added four names the guessing stage never had: 木箱 (DF514440), 石头01
(B35D1B68), 石头02 (99888485), 石头03 (CCAED5C9).

Mesh-level fallback handles two meshes that ship with no `MAT3` block at
all (the crate `879926bf` and the flower `4c85d7c7`): they bind to a
single texture via the varbin link or visual identification of the mesh.

Result: **9,078 of 11,761 mesh triangles (77%) bind a verified texture**;
the remainder belong to meshes whose materials name files that simply do
not ship in this 97-of-8,672-path install.

`tex_link.py` → `extracted/models/texmap.json` (the link table);
`obj_render.py --texmap extracted/models/texmap.json` → `png_tex/*.png` and
`textured_sheet.png`; `tex_gallery.py` → `textures.html`;
`zmap.py` → `extracted/maps/intro_scenes.json` (scene layouts).


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
|  |  |  |
| --- | --- | --- |
- every decoded normal is unit length (`|n| = 1.000`).
- every triangle index is in `[0, n_verts)`.
- the stream is **fully consumed** on all 15 parts. Where bytes remain
  between one `sbus` blob and the next marker they are not mesh data at all:
|  |  |  |
| --- | --- | --- |
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


### 4.7 The six map payloads — `.zmap` scene documents

Six of the 48 `varbin` payloads are not tables. They are **map files** — the
`<mapname>.zmap` documents the scene editor writes, caught by the "no table has
a blob column" rule in §4.2. The layout is not inferred from the data: the
editor builds it in `ui/video/world.gbc::SaveMapData()` and reads it back in
`OnEditData()`, and `def.gbc` names every index:

```
#enum MAP_VER,MAP_HEIGHTDATA,MAP_LAYERDATA,MAP_WIDGETDATA,MAP_DATAEX,MAP_MODELDATA,MAP_END
```

| field | enum | content |
|---|---|---|
| 0 | `MAP_VER` | `100` — `OnEditData` returns `-2` on any other value |
| 1 | `MAP_HEIGHTDATA` | 129×129 16-bit height field: 12-byte header + two 8-bit planes + u32 rolling-hash trailer |
| 2 | `MAP_LAYERDATA` | `[100, 256, 512, 393216 bytes]` — 24-bit terrain-type map |
| 3 | `MAP_WIDGETDATA` | `[map name, decorations…]` |
| 4 | `MAP_DATAEX` | 6 texture layers + light + path layer + fog |
| 5 | `MAP_MODELDATA` | fixed model placements, 18 fields each |

`MAP_DATAEX` is indexed by `MAPBASE_LAYER1…LAYER6, MAPBASE_LIGHT,
MAPBASE_PATHLAYERNO, MAPBASE_FOGCOR, MAPBASE_PATHLAYERNOEX`; each of the six
layers is `[tex_name, tr_type, leave]` (`MAPLAYER_*`). Every `MAP_MODELDATA` row
is the 18-name `FIXMODEL_*` enum, so the placement table exports with real
column names rather than positional ones.

**`FIXMODEL_*` — the 18 fields of a fixed placement** (`def.gbc:353`):

| # | name | type | meaning |
|---|---|---|---|
| 0 | `ID` | int | object id, unique within the tile |
| 1 | `RESNAME` | str | asset key, resolved through `SysFileVer.var` |
| 2 | `X` | float | metres |
| 3 | `Y` | float | metres |
| 4 | `Z` | float | **offset above the terrain**, not an absolute height |
| 5 | `SIZE` | float | scale multiplier (`SetScale`) |
| 6 | `ASPECT` | float | facing in **degrees** (`SetForward`) |
| 7 | `ANI` | str | animation name |
| 8 | `PLAYSPEED` | float | |
| 9 | `PARTICLE` | str | |
| 10 | `PARTSHOW` | int | 1 = shown |
| 11–15 | `TIPS`,`SOUND`,`SDELAY`,`VOL`,`STYLE` | | |
| 16 | `PARAM` | list | |
| 17 | `HALF` | int | inverted flag (`m_nEyeHalf = !v`) |

Two of these are settled by the shipped script rather than by inference:

* `Z` is relative to the ground — `world.gbc:905`:
  `hz = GetZHeight(v[FIXMODEL_X], v[FIXMODEL_Y]) + v[FIXMODEL_Z];` and
  `GFixModel::Build` calls `SetPos(x,y,0)` then `SetGroudHeight(dz)`.
  The data agrees: across the six tiles `Z` lies in −1.5…0.5 m, and a
  *terrain-relative* reading puts the props on the surface (median residual
  1.21 m) where an absolute reading would leave them up to 9 m out.
* `ASPECT` is degrees — `GFixModel::Build(…, float f)` is called with the
  default `180` and `SetForward(vData[FIXMODEL_ASPECT], 0)`; the shipped values
  span −172.6…247.5, which cannot be radians.

`SIZE` multiplies the model's natural size (the flower is 0.76 m tall, the
shrub 1.73 m, the mushroom 1.31 m), and the shipped values are 0.6, 0.8, 1.0,
1.48 and 1.8.

**Multi-row props are placed as matched pairs, not duplicated.** The village's
"house under a tree" is two placements at *identical* x, y, z, `SIZE` and
`ASPECT`: `精灵小镇_建筑02_Z` → `E2297D8E` is the house (walls/roof/wood, z 0–15.7 m)
plus the 29 m trunk and vines, and `精灵小镇_建筑201_Z` → `E753DD7C` is the
53 m leaf canopy (z 10.1–30.6 m) that goes over it. `新手村树1` (`4C3D969F`) /
`新手村树101` (`99A1D725`) pair the same way at three shared positions. A
renderer that drops either half is not visibly wrong — the canopy simply
hides an 11 m house from any angle above the horizon, so both halves must be
drawn to see the building at all.

**They are not six copies — they are six tiles of one world.** They carry
`MAP_VER 100` and the same internal name `w100`, and their height headers are
byte-identical, which is why this read as "six copies of one scene" for a long
time. The file name is the grid cell, not a copy id:

```
def.gbc:105  #define CHIPMAP "chipmap"                  // 地图切割名称
def.gbc:107  #define TerrainLodSIZE 256
def.gbc:108  #define WPOS2BPOS(s) (((s)+128000)/TerrainLodSIZE)
def.gbc:109  #define BPOS2WPOS(s) ((s)*TerrainLodSIZE-128000)
def.gbc:110  #define MAKEMAPID(ix,iy) ((ix)+(iy)*1000)   // 每块场景产生一个ID
```

and `ui/video/world.gbc::CheckWorldCenterPos` streams a
`(2*maprs+1)^2` neighbourhood of tiles around the player, 600 m out:

```
id       = MAKEMAPID(ix+x, iy+y);
mapname  = OnGetCopyName()+id;
vRes     = m_i_FileVer.GetResByName(mapname);
plod     = new MVTwLod;  plod.PerCreate(addv[1], addv[2], addv[3], ...);
```

So `intro517493` is ix=493, iy=517 (`id % 1000`, `id / 1000`), and the six
shipped tiles are a **3 × 2 block**, ix 493..495 × iy 517..518 = **768 × 512 m**
of one continuous world, at world coordinates x −1792..−1024, y 4352..4864.

**The adjacency is measured, not assumed.** Mean |Δh16| across every shared edge
is **0.01–1.01** units of 65,530 (≤ 30 mm), while the *reversed* pairing of the
same two edges gives **1,209–4,770**. That is a 1,000× separation, so the grid
direction (ix → +x, iy → +y) is a fact about the data, not a guess:

| edge | forward | reversed |
|---|---|---|
| `intro517493` → `intro517494` (x) | 0.008 | 1,209 |
| `intro517493` → `intro518493` (y) | 1.008 | 4,770 |

`SaveMapData` writes to `RESPATH + OnGetCopyName() + "/" + mapname + ".zmap"`, so
the path's directory is the *copy* (`intro`) and the file stem is
`copy + MAKEMAPID` — which is exactly why every file is named `w100` inside and
still distinct on disk.

**They are also the six `intro/*.zmap` files, and that matters operationally.**
Hashing those six paths resolves exactly the six map hashes:

| hash | path |
|---|---|
| `cc5de97f` | `intro/intro517493.zmap` |
| `6010fb33` | `intro/intro517494.zmap` |
| `4514d98f` | `intro/intro517495.zmap` |
| `da80ff38` | `intro/intro518493.zmap` |
| `9feba4ac` | `intro/intro518494.zmap` |
| `6b45f638` | `intro/intro518495.zmap` |

So there is no such thing as "the six map payloads *and* six intro scenes" —
they are one asset set reached two ways. The earlier reading of these as six
unrelated copies plus a separate intro set was an artefact of looking at them
through two different tools.

This caused a real bug worth recording. `varbin_tree.payloads()` used to glob
`extracted/netpak/unknown/*.zip`; once `apply_names.py` renamed these entries to
their real paths they left `unknown/`, and the count silently fell **48 → 42**
with no error raised. Any tool keyed on a path that naming can change is
fragile this way. `payloads()` now discovers by **content** (the `0x602376D7`
wrapper tag) anywhere under `netpak/`, and resolves the hash back through
`names.json`, so renaming is a no-op for it. `map_decode.py` additionally
cross-checks the hash-keyed walk against a path-keyed walk and fails loudly if
they disagree.

**Height field — fully decoded.** 12-byte header, then **129 × 258 bytes**,
then a u32 trailer. The header is `u32 0xFAB00002`, `f32 minz = -100.0`,
`f32 maxz = 900.0`, identical in all six maps.

The 129 × 258 block is **one 16-bit grid, not two 8-bit fields**: the first
16,641 bytes hold the high byte of every vertex and the second 16,641 hold the
|  |  |
| --- | --- |
low byte. `h16 = (plane1[i] << 8) | plane2[i]`, and

```
z = minz + h16 * (maxz - minz) / 65530
```

The loader proves it. `chronotales.exe`'s `.zmap` record reader starts at
`0x4ee4f9` and demands a record length of exactly **0x8212 = 33,298** =
12 + 2·16,641 + 4, which is the length of every shipped record:

```
4ee4f9  cmp  dword [edi], 0xfab00002   ; tag -- compared, not merely written
4ee591  cmp  dword [esp+0x10], 0x8212  ; exact record length
4ee647  mov  dword [esi+0x134], 0x81   ; 129 columns
4ee64d  mov  dword [esi+0x138], 0x81   ; 129 rows
4ee662  mov  edx, [edi]                ; minz, read from header +4  -> [esp+0x30], [esp+0x38]
4ee66d  fld  dword [edi]               ; maxz, read from header +8  -> ST0
4ee66f  fcom dword [esp+0x30]          ; maxz < minz -> "SetHeightData minz>maxz err"
4ee70f  fsub dword [esp+0x30]          ; ST0 = maxz - minz
4ee728  fmul dword [0x6d17e8]          ; * 1.5260186e-05 = 1/65530
4ee72e  fstp dword [esp+0x20]          ; scale = (maxz - minz) / 65530
4ee725  imul ecx, ebp                  ; ecx = 129*129
4ee73e  add  ecx, edi                  ; second cursor = first + 16,641
4ee77b  mov  dh, byte [edi]            ; high byte
4ee77d  mov  dl, byte [ecx]            ; low byte
4ee77f  and  edx, 0xffff
4ee78b  fild dword [esp+0x14]          ; h16
4ee794  fmul dword [esp+0x20]          ; * scale
4ee798  fadd dword [esp+0x38]          ; + minz
4ee79c  fstp dword [eax-0x10]          ; vertex z
```

**The `fsub` is the step that is easy to miss.** `fmul [0x6d17e8]` multiplies by
`1/65530`, so reading it as `z = h16 * (1/65530) + minz` gives a **1 metre** total
range. The multiplier is a *precomputed* `(maxz − minz)/65530` in `[esp+0x20]`,
built three instructions earlier from the two header floats; the `fadd` base in
`[esp+0x38]` is `minz`. Both header floats are therefore live, exactly as the
saver implies. The same routine also scales x and y: `x = col * ([esi+0x130] *
1.00001)` and `y = row * (…)`, i.e. 2 m per cell (the `1.00001` at `0x6d17e4` is
an anti-seam fudge).

The reciprocal turns up in the saver at `0x4ee45a`: after clamping to
`-100.01 .. 900.0` it computes `h16 = (h - minz) * 65.53` (`0x6d17d8`), writes
the high byte to plane 1 and the low byte to plane 2, then the trailer --

```
4ee479  88 16   mov byte [esi], dl   ; dl = AH   -> plane 1
4ee47d  88 07   mov byte [edi], al   ; al = low  -> plane 2
4ee48f  89 2f   mov dword [edi], ebp ; trailer
```

65.53 × 1000 = 65,530, exactly the divisor the loader uses, so the header floats
are **live data, not a comment**: the span really is 1000 m and the step really
is **15.3 mm**.

The trailer is a rolling hash, not crc32/adler32/sum. Verified on all six maps:

```python
h = 0
for v in h16:
    h = ((h << 1) ^ v) & 0xFFFFFFFF
```

**This reverses three earlier readings of this section.** (a) The second plane
was called "unidentified coherent relief, probably a tangent-space normal map";
it is the low byte — which is exactly why it spans 0..255, why it correlates
with nothing (a low byte wraps every 256 units, i.e. every 3.9 m of height), and
why `intro518495` shows a plateau at 201 over 20% of the grid (a genuinely flat
area, where h16 is constant and so both bytes are constant). (b) The tag was
called "write-only — nothing ever compares it"; there is a `cmp` at `0x4ee4f9`.
(c) The grid was read as 8-bit over `-50..450`; that halves the relief and
stair-steps every 3.9 m.

**Independent confirmation from the scene data.** Under this mapping the terrain
at every village prop is ≈ 0.00 m, and the props are authored at z = 0.
`精灵小镇_木箱_Z` at (137.93, 211.19) has authored z = 0.000 and `height_at`
returns **+0.060 m**; the four crates at x 131..136, y 210..213 land within
±0.15 m. The 8-bit `-50..450` reading only *looked* right because the village
happens to sit where the two mappings cross zero — it gets the village level by
coincidence and the rest of the map wrong.

Relief is therefore 72..124 m per map (was 36..62 m). Because the step is 15 mm
there is no staircase left to hide, so both renderers decode 16-bit and the
former two-pass tent blur (`Scene._smooth`, `render_scenes.smooth`) is now a
no-op kept only as an escape hatch.

**Large parts of a tile are an unpainted fill.** `h16 = 3273` — one single value —
covers **39%** of `intro518493` and **19%** of `intro518495`, and no other value
comes close (the next most common in `intro518493` has 86 cells against 6,458).
It decodes to z = **−50.05 m**, i.e. 5 cm under `def.gbc:114
#define DEFWORLD_MINZ -50`, so it is the editor's floor fill rather than painted
terrain. This is what the "crater" in `intro518493`'s south-west actually is: a
wide flat floor with a painted mound standing on it, not a bowl. It is also why
those regions render as a dead-flat plateau — that is the data, not a decode
artefact.


Three independent facts pin **one tile** at 256 m square:

* every scenery instance lands in `x 65..241, y 0..208` and every
  `MAP_MODELDATA` placement in `x 91..181, y 30..64` — nothing reaches 256;
* the layer map is 256 wide at 1 px/m, so a tile is 256 m across;
* `TerrainLodSIZE 256` at half resolution gives 129 vertices per axis.

(The old second bullet here said the layer map's lower half was "35% painted,
i.e. padding" — wrong twice: it is 5.9% painted, not 35%, and it is layers 3–5,
not padding. See the layer-map note below.)

**World assembly — `World` in `tools/render_scene3d.py`.** Stitching is not a
concatenation of images; three things have to be handled:

1. **Heights** are trivial: tile *(ix,iy)* occupies grid `[x*128, x*128+128]` and
   the shared edge row/col is duplicated, so the overlap must agree rather than
   be appended. `World.seam_max` asserts it (2 units of 65,530).
2. **Ground cannot be blended globally.** The six tiles do **not** share one
   surface set: slot 1 is `沙地` in `intro517493` and `地表_路面007` in
   `intro517494`, and only slot 0 (`地表_草地006`) is common to all six. So
   `blend_ground(res)` is per tile and `World.bake_ground()` pastes the six
   results into one 3072×2048 atlas (4 px/m). This also means a **1-cell step
   in the ground is expected at every tile boundary** where the two tiles' paint
   differs. That is authored data, not a stitching artefact, and the raw 1 m
   weight map proves it: the boundary column differs from its neighbour by up to
   **13× the local mean** (18.6 vs 1.4 at ix=494's y seam; 4.9 vs 0.5 and 6.8 vs
   0.9 at the two x seams) while one of the six edges is *identical* (0.000).
   The engine has six separate `MVTwLod` objects with six surface sets, so it
   sees exactly the same edge. In the render it reads as a thin straight line
   (3.9 RGB units against a 1.8 local mean).
   `沙地` is the one layer with no shipped `surface/*.jpg`, so that tile's blend
   renormalises over the five it has.
3. **The v axis was inverted.** `obj_render` samples at `ty = (1-v)*h`, so
   passing `v = y/world` (as the single-tile renderer did) mirrors the whole
   ground field about its middle row. That was invisible while the ground was
   "plausible grass"; it becomes obvious the moment six tiles are stitched and
   the road network has to join up across them. Fixed to `1 - y/world_h`.

`python tools/render_scene3d.py --world` renders the 768 × 512 m block
(`world_intro_3d_tex_{oblique,plan,horizon}.png`); the rasteriser is square-only
so the frame is cropped to the drawn content afterwards. `--check` now runs a
`world-tiling` invariant as well: the names must decode to a hole-free 3 × 2
rectangle, every shared edge must agree to ≤ 4 units while the reversed pairing
must exceed 100, and props must land on the stitched surface as well as they do
on a single tile (median 1.21 m either way, 38 of 39 rows; the 39th is a
sentinel row at (0,0)).

An earlier reading of this section called the second block a "mountain patch"
and explained its entropy as quantisation dithering, then later called it
"uninitialised noise". Both were wrong, and the second produced a visible bug:
reading the blob as a **258-wide** grid transposes the field, so every terrain
quad connected cells one row apart and the whole map rendered as a curtain of
vertical spikes. The 129 × 129 two-plane reading is correct and both
`tools/render_scene3d.py` and `tools/render_scenes.py` use it.


**Scenery instances — solved from the client, not inferred.** `MAP_WIDGETDATA`
is `[world name, decoration, …]`, and each decoration is

```
[int type, str name, str model path, str texture path, int, int count, bytes blob, int]
```

The blob is a 16-byte header of four `float32` followed by `count` records of
**9 bytes**. The client's own reader, writer, snap and renderer agree on the
layout:

```
FUN_004e1960  reader   blob must be 16 + 9*count
FUN_004e16a0  writer   writes the same four floats and the same 9-byte records
FUN_004e1660  snap     z = terrain(x,y) + dz
FUN_004e1ed0  renderer size = scale byte / 16, facing from the direction pair
```

| bytes | field | conversion | constant |
|---|---|---|---|
| 0–1 | `x` | `× 0.01` → metres | `_DAT_006d0408` = 0.01 |
| 2–3 | `y` | `× 0.01` → metres | |
| 4–5 | `dz` | `× 0.01` → metres **above the terrain** | |
| 6 | `dir_x` | `÷ 127` (signed) | `_DAT_006d158c` = 1/127 |
| 7 | `dir_y` | `÷ 127` (signed) | |
| 8 | `scale` | `× 0.0625` | `_DAT_006d1588` = 1/16 |

`dz` is **signed** — the reader does `(int)(short)puVar10[2]` — and `FUN_004e1660`
then makes the final z absolute by adding the terrain height. The direction
pair is a unit vector; the renderer stores it with a negated x component to
build the card's basis.

**The four header floats are an animation parameter, not a size.** This is the
one field that was wrong before. `FUN_004e1e80` transforms them like this:

```
+0x74 = frame_delta * header[0] * 0.001
+0x78 = frame_delta * header[1] * 0.001
+0x7c = header[2]
+0x80 = header[3]
```

and `FUN_0044c560` returns elapsed **milliseconds** (`GetTickCount` minus the
previous frame), so the first two become `dt_seconds × amplitude`. The result
is handed to the renderer as a vec4 uniform (`FUN_004bdf40`) — it is the **wind
sway** of grass and foliage. The data agrees: all nine shipped decorations use
either all-zero or all-0.2, and the same asset (`精灵小镇_奇幻灌木001_Z`) carries
0.2 in one tile and 0.0 in the next, so it is authored per placement group.

Reading it as a scale shrank every grass, flower and shrub decoration to 20%.
The models are authored at their natural size — the flower is 0.76 m tall, the
shrub 1.73 m, the mushroom 1.31 m — so the instance's own scale byte is the
whole story.

A **version-1** record layout also exists in the client (14-byte stride, no
`dz`, which defaults to −0.01 m); only version 2 ships.

All nine decorations across the six tiles decode cleanly: 126 instances, the
count field always matching the blob length, every instance inside its own
256 m tile, and `dz` in −0.07…0.68 m. `tools/render_scene3d.py --check`
asserts all of that.


**Layer map.** `[100, 256, 512, pixels]` with 3 bytes per cell, **interleaved**
(a planar reading is pure noise: adjacent-byte delta 255 vs 0 interleaved).

Both 256×256 halves cover the same 256 m world at 1 px/m, and they are **not**
"one map plus padding" — an earlier reading said the second half was only 35%
painted and therefore padding, which was wrong. The runtime normalises a cell in
the top half against the cell 256 rows below it: `FUN_004edb40` reads both, sums
all six bytes (`*pB + pB[1] + pB[2] + *pA + pA[1] + pA[2]`) and rescales them to
`0xff`. So **top R/G/B weight layers 0/1/2 and bottom R/G/B weight layers 3/4/5**
— six weights for the six `MAP_DATAEX` layers, three channels each.

That matters in practice: ignoring the bottom half throws away the road and
stone layers. On `intro518494` the bottom half is only 34.8% painted, but it
holds 450 cells of `ff0000` (layer 3 = `地表_路面002`) and 594 of `0000ff`
(layer 5) — and layer 3 *is* the road network, invisible without it.

The field is a **continuous RGB blend field** (22,370 distinct colours), not a
palette index: the top half alone is 98% painted and dominated by `ff0000`
(31,156 px), with gradients between. They are **weights, not an index**, which
matters: picking the dominant channel per cell (the first implementation)
collapses the anti-aliased edges into 6 m stair-steps, because the rasteriser
binds one texture per triangle. The fix is to bake the blend once per scene:
`Scene.bake_ground()` resamples each half to 2048² over the 256 m world, tiles
each of the six layers at `GROUND_TILE` metres (48 px per 6 m tile — still finer
than the render's own 0.18 m/pixel), and composites `Σ wᵢ·Lᵢ / Σ wᵢ` over all
six, falling back to layer 0 where every weight is zero. Roads now have soft
organic edges instead of blocks, and the road surface is actually the road
texture.

**Height quantisation — resolved, and it is not a problem.** The old note here
said a byte is ~2 m over a 500 m range, so a "gentle" plain arrives as a
staircase and both renderers tent-blurred the field twice to hide it. The step
is actually 15.3 mm (16-bit over the header's 1000 m span), so the blur was
compensating for a decoding error rather than for the data. It now defaults to
zero passes. `terrain_color()`'s 5-band palette is still only used for the
untextured fallback — as a multiplier on a ground texture it produced hard
colour steps — so textured terrain keeps `height_tint()`.

`MAP_DATAEX`'s six layers are `地表_草地006, 地表_草地008, 地表_草地007,
地表_路面002, 地表_石03, 地表_草地100`, which `SysFileVer.var` resolves to
`surface/dd72a041.jpg, 1e05658b.jpg, 8fc0afb5.jpg, 19fc32a5.jpg, 459577eb.jpg,
de152742.jpg` — all present in the archive. That is why the rendered ground
shows a road network crossing the grass without any road geometry existing.


**Decorations.** Each is `[kind, res_name, model, texture, 0, n, blob, 0]` where
the blob is a 16-byte header + `n × 9 bytes`. **Fully decoded from code**
(Ghidra: reader `FUN_004e1960`, writer `FUN_004e16a0`, terrain snap
`FUN_004e1660`, renderer `FUN_004e1ed0`, all in
`E:\GBox2\src\CLIENT\3d\wold\GObj3dWidgetBsp.cpp`):

```
header: 4 x float32   per-scatter base scale (0.2 for flowers/grass, 0 = default)
record (9 bytes):
  u16  x        * 0.01  -> metres        (constant _DAT_006d0408 = 0.01f)
  u16  y        * 0.01  -> metres
  i16  dz       * 0.01  -> z offset; runtime z = terrainHeight(x,y) + dz
  i8   dir_x    / 127   \ billboard direction pair — the renderer builds the
  i8   dir_y    / 127   / ground quad from (dir_x/127, dir_y/127, -dir_x/127)
  u8   scale    * 0.0625  (_DAT_006d1588 = 1/16; direction divisor 0x6d158c = 1/127)
```

This corrects the earlier range-based guess ("four u16 + one u8, half-degrees"):
the third field is a *signed* z offset (mostly 0, so it looked like a zero u16
pair), the fourth u16 is really two signed direction bytes, and the trailing
byte is a scale in 1/16 units (values 0x0a..0x62 = 0.6..6.1). x/y decode to
world coordinates 65..237 × 7..224 m, consistent with the MAP_MODELDATA
placements on the same maps. The map name is the `"w100"` first element.

`tools/zmap.py` parses the container end-to-end (tag + ZIP + varbin + instances)
into `extracted/maps/scenes/intro_scenes.json`; `tools/render_scenes.py` draws
the top-down layouts (`extracted/maps/intro*_layout.png`). The older
`tools/map_decode.py` exports all six to `extracted/maps/`: JSON metadata, a
greyscale height map, a hillshaded relief, the layer map as PNG, and the
decoration and placement tables as CSV.

### 4.8 `extracted/explorer/` — one page over everything

`tools/build_explorer.py` reads the outputs of every other stage and emits a
single static page. Nothing is decoded here; it is purely a viewer, and it is
the fastest way to sanity-check a re-export.

| file | contents |
|---|---|
| `index.html` | the whole UI — no external assets, no build step |
| `data/index.js` | manifest: stats, 42 table entries, 6 map entries |
| `data/t_<id>.js` | one file per table, loaded on demand |
| `data/assets.js` | the 9,975 `SysFileVer.var` records |

Two constraints shaped the design:

- **`file://` has no `fetch`.** CORS blocks it, so data ships as
  `window.__X = …` assignments loaded through dynamically injected `<script>`
  tags. The page opens by double-clicking with no server of any kind.
- **5.7 MB of CSV cannot be inlined.** Hence one file per table; the manifest
  carries only counts, and a table's data is fetched the first time it is opened.

Views are hash-routed (`#t/itemtabedit`, `#maps`, `#assets`, …) so a table is
linkable. Every figure displayed is computed from the generated data — the two
`_manifest.json` files, `models/_index.json`, `gbc_index/data.json` — never
hardcoded, so a count cannot drift from the data it describes.


### 4.9 `.caf` — character animation (Cal3D `CalCoreAnimation`) — **SOLVED**

> **Status: byte-exact on all 5 shipped clips.** One clip per file, a flat
> track table, 32-byte keyframes. This is **stock libcal3d**, not a GBox
> container: there are no reversed tags, no packed headers and no chunk
> directory. Everything below is read off the loader, not inferred.

Five `.caf` files ship, all inside the ZIP `netpak` entry `e41c7990` =
`action/e41c7990.zip` (a plain archive, no `0x602376D7` wrapper). The archive's
own name hash is `hash_name("action/e41c7990.zip") = 0xC41C9276`, which is why
older notes in this repo call the same entry `c41c9276`; that is the *hash*, the
entry id is `e41c7990`.

| clip | bytes | duration | tracks | keys | key0 vs bind pose |
|---|---:|---:|---:|---:|---|
| `jumpdown.caf` | 12,744 | 0.266667 s | 43 | 387 (9/track) | 35/43 |
| `jumping.caf` | 12,744 | 0.266667 s | 43 | 387 (9/track) | 41/43 |
| `jumpstart.caf` | 12,744 | 0.266667 s | 43 | 387 (9/track) | **43/43** |
| `run.caf` | 22,376 | 0.500000 s | 43 | 688 (16/track) | 28/43 |
| `stand01.caf` | 22,376 | 0.500000 s | 43 | 688 (16/track) | 29/43 |

**Where the loader is.** `CalCoreAnimation::load` at VA **`0x5ABF70`**, its
per-track parser `CalCoreTrack::load` at VA **`0x5ABD90`**. Both name
`E:\GBox2\src\client\cal3d\src\coreanimation.cpp` (file offset `0x36a4C4`,
vector at `0x76A4C4`), and the track-delay assert string
`"loadCoreAnimation track delay err"` is at `0x76A4C4`. The magic dword
`'CAF\0'` = `0x00464143` is compared through a pointer at VA `0x6D4620`
(`mov ecx, 0x6d4620 / cmp ebx, [ecx]` at `0x5ABFBB`), and occurs exactly once
in the binary — so, like `sbus`, there is **no writer** shipped with the client.

**File layout.**

```
0x00  char[4]  "CAF\0"
0x04  u32      version        699..1100 accepted (0x5ABFDA / 0x5ABFE5); all 5 are 1000
0x08  f32      duration       seconds, must be > 0 (0x5AC010 tests against 0.0)
0x0C  u32      n_tracks       = 43 in every clip
      then n_tracks x {
          u32  bone_id      must be >= 0 (0x5ABDC4)
          u32  n_keys       must be > 0  (0x5ABDEE)
          n_keys x 32-byte keyframe {
              f32  time
              f32  tx, ty, tz           centimetres  (* 0.01)
              f32  qx, qy, qz, qw       inverse local rotation
          }
      }
```

**How each field's role was established.** The parser reads `bone_id` then
`n_keys`, allocates `n_keys << 5` bytes at `0x5ABE00`, and `rep movs` the keys
in verbatim — so a key is exactly 32 bytes and nothing is decoded per key.
It then multiplies exactly three floats by `0.01` (`fmul [0x6D0738]` at
`0x5ABE94`, `0x5ABE9D`, `0x5ABEB3`) and copies four at `key+0x10`.
`key+0` is never touched, which makes it the time; the three scaled floats are
the translation; the four copied ones are the quaternion. Finally it sets the
track's playback step to `keys[1].time - keys[0].time` when `n_keys > 2`
(`0x5ABE58`-`0x5ABE5D`), else to the constant `0.03333` (`0x3D08850A`, written
at `0x5ABE4C`).

The animation loader then walks the tracks and **enforces that every track
agrees with track 0 on that step to within `0.001`** — `fsub`, `fabs`,
`fcomp qword [0x6D4628]` (= `1e-3`), `test ah,0x41` at `0x5AC0E4`, else the
`"track delay err"` assert. This is a structural condition the format itself
guarantees, and the validator reuses it rather than inventing one.

**Corrections to the earlier reading.** Three claims in the briefing were
wrong, and they were wrong for the same reason — treating a float as an
integer:

- **`+0x04` is not `ticksPerSecond`.** It is the **format version**, and the
  loader rejects anything outside `699..1100`. `1000` is `CAF_VERSION`, whose
  value in stock libcal3d is also 1000 — a coincidence that made the mistake
  plausible.
- **`+0x08` is not `n_bones`.** It is a **float duration**. `0x3E888889` is
  `0.266666681` = **8/30 s**, and `0x3F000000` is `0.5` = **15/30 s**. Read as
  an int these are "1049135241 bones" and "1056964608 bones", which is what
  produced the "43 bones vs a float, check this" note in the brief. There is no
  ambiguity: the field is a duration, and it equals the final keyframe time of
  every track to f32 precision.
- **`+0x0C` is not `n_anims = 5`.** It is **`n_tracks = 43`**. A `.caf` holds
  exactly one clip; the "5" came from the number of files.

The float interpretation is not a guess. It is forced three ways at once: the
loader `fld`s the word and compares it against `0.0` rather than range-checking
an integer; the value tracks `8/30` and `15/30` exactly; and it equals the last
key time in all five files. An integer bone count has no reason to satisfy any
of those.

**Keyframe grid.** Times are `i/30` to within `1.4e-8` (they are f32), the step
is `1/30 s`, and all 43 tracks in a clip share one grid. `jumpstart`/`jumping`/
`jumpdown` hold 9 keys each (0 .. 0.2666 s), `run`/`stand01` hold 16
(0 .. 0.5000 s). The final key time equals the header duration exactly.

**Pose convention — same as `.cmz` §4.4.** Rotations are the *inverse* of the
bone→parent rotation, so `R_world[i] = R_world[parent] * conj(q[i])`; the
translation is in centimetres (`* 0.01`); frame is metres, **+Z up**. The
convention was not assumed: replaying `jumpstart.caf` key 0 through the §4.4
recurrence reproduces the `.cmz` bind pose to **8.3e-07 m** across all 43
bones, which is f32 round-off. That is a decisive, zero-parameter match — a
wrong quaternion order or a missing conjugation cannot land on an independently
derived pose.

**Which skeleton these drive.** All five clips bind to the 43-bone Biped rig
(now filed as **`b056501a.cmz`**; see the filename trap below), whose bind
height is **1.565 m** (world Z spans 0.207 .. 1.772).
Scoring every decoded `.cmz` by "how many bones' key 0 equals the bind pose"
gives **35–43 / 43** for that rig and **0 / 43** for all eight 109- and
119-bone rigs — no partial credit anywhere. `jumpstart.caf` is exact on all 43
bones, which is what pins the mapping.

**What that rig is — solved, and it settles a stale-name confusion.** The
skeleton is a standard 3ds Max **Biped** humanoid: `Bip01`, `Bip01 Pelvis`,
`Spine`/`Spine1`, `Neck`, `Head`, `HeadNub`, `L/R Clavicle` → `UpperArm` →
`Forearm` → `Hand` → `Finger0`, `L/R Thigh` → `Calf` → `Foot` → `Toe0`, and
`Footsteps`. So the skeleton is a humanoid character, not a vehicle.

The *same* file also carries five `sbus` parts named `机甲摩托_前轮 / 后轮 /
车身 / 车头 / 轮眉架子` (mech motorcycle front wheel / rear wheel / body /
front / mudguard), and 13 of the 43 bones are not Biped bones: `t`, `tf`,
`Bip01 F Lun`, `Bip01 B Lun`, `tb`, `Bip01 An`, `q`, `Bip01 penqi01`–`04`
(`penqi` = 喷气, "jet"), `s`, `w`. So one `.cmz` holds a **Biped skeleton plus
mech wheel/jet attachment bones and mech wheel geometry** — a rider-on-mech
rig, exported as a single skeleton.

**A filename trap worth recording, because it cost time here.** `cmz.py`'s
output JSONs were produced *before* `apply_names.py` renamed the extracted
files, so `extracted/models/cmz/2682c5d5.json` still says
`"path": "extracted\\netpak\\unknown\\2682c5d5.cmz"` — but that file no longer
exists. The rig now on disk is `extracted/netpak/model/b056501a.cmz`
(103,511 bytes, byte-identical skeleton to that JSON). So:

- the rig the five clips bind to is identified by **content**, and its
  current on-disk name is **`b056501a`**;
- `2682c5d5` is the *pre-rename* hash, and is what the JSON stem still says;
- §4.6's part names are correct — re-deriving them with
  `sbus_decode.part_name()` on `b056501a.cmz` returns exactly the five
  `机甲摩托_*` names. The `s` names in `cmz.py`'s JSON `mesh` array are an
  artefact of its own (weaker) name heuristic, not a contradiction.

This is the §4.7 lesson recurring: a tool keyed on a path that renaming can
change resolves to the wrong thing or to nothing.

The clips are deliberately leaner than the humanoid rigs: 31 of the 43 bone
names also exist in the 119-bone humanoid, but only 8 of those share an index,
and 12 bones (`Bip01 F Lun`, `tf`, `Bip01 B Lun`, `tb`, `Bip01 An`, `q`,
`Bip01 penqi01`–`04`, `s`, `w`) exist only on the 43-bone rig. So a 43-track
clip is **not** index-compatible with a 119-bone character and must be remapped
by name, not by track position. That is a real constraint for anyone animating
the humanoid meshes, and the decoder does not paper over it.

**Tracks that do not animate are not held at the bind pose.** Nearly every
track still varies in the last few decimal places (amplitudes of `4e-6` ..
`2e-5`), so a "constant track" test with a tight threshold finds **0/43**. The
variation is f32 noise from the exporter's resampling, not motion: it is two to
four orders of magnitude below the animated bones (`1e-2` .. `4e+1`). This is
the §6 trap in a new place — a threshold tuned to "constant" would report the
format as broken, and a threshold tuned to "moving" would report 43 animated
bones in every clip.

**Validation** (`tools/caf_validate.py --strict`, 5/5 clips, exit 0):

- the stream is **fully consumed** on all 5 files — the last key ends on the
  final byte, with no slack (this is what `caf_decode.py check` asserts, and it
  is the only real proof of the *layout*: a wrong stride or a wrong field role
  cannot survive it);
- `version` in `699..1100`, `duration > 0`, and `duration ==` final key time;
- every track in a clip has the same key count and byte-identical time column,
  and the step is `1/30` — the loader's own assert, re-implemented;
- key times strictly increase and lie in `[0, duration]`;
- every keyframe quaternion is unit length (worst error `6.7e-08`);
- every bone id is in `[0, 43)`, no id repeats, and `n_tracks ==` the rig's
  bone count;
- **a track that does not animate must equal the `.cmz` bind pose** (motion
  under `1e-3`; 25–41 tracks per clip qualify, worst deviation `1.6e-05`).
  This is the one assertion with an **external** ground truth — the rig comes
  from a different file — and it is what catches semantic corruption.

**What the validator can and cannot catch.** This is stated plainly because a
green check that cannot fail is worse than none. Checks 1–3 above are hard and
independent: a wrong stride, a wrong header interpretation or a wrong field
role breaks them immediately. The bind-pose check catches semantic damage to
any bone the clip leaves alone. **But there is no ground truth for a bone a
clip actually animates**, so a wrong quaternion convention or a swapped
translation axis applied to a *moving* track would pass — layout correctness
for those rests on byte-exactness, not on this tool.

That limit is not hypothetical: an earlier version of this validator asserted
"rigid bone segment length" and "root sits at its own translation" by
recomparing the replay against numbers derived from the *same* corrupt input.
Those assertions are tautological and `TOL_SEG` could never fire — a doubled
bone translation or a swapped `qy`/`qz` passed silently. They were **removed**
and replaced by the bind-pose comparison, which fails on both
(`6.31e+01` and `2.58e-01` respectively). A third bound, on the figure's
*height*, was also withdrawn: `jumping.caf` rotates the root **21.23° about
+X**, swinging the rig's 2.05 m of +Y bone length into +Z, so the bounding box
legitimately grows from 1.565 m to **2.548 m**. A height bound measures the
pose, not the decode.

Deliberately **not** a failure: scaling the root's translation. A root's
translation is the character's world motion and has no bind-pose constraint,
so the validator must leave it free.

Each check was confirmed to fail on a deliberately corrupted file. Mutants that
produce exit 1: version `5000`, retargeted `n_tracks`, a retimed duration, a
scaled quaternion, a duplicated bone id, an out-of-range bone id, a negative
key time, a byte inserted into the key stream, a doubled translation on a
static bone, and a swapped `qy`/`qz` on a static bone. Fixtures live in
`work/caf_neg/` (gitignored).

**What is not claimed.** The five clips are 1 of the **406** `action/*.zip`
paths the manifest lists (the 407th path is `action/火炎弹.txt`), so this is a
sample, not the animation set. The clip names are not animation *kind* names:
the exe embeds a `#enum` and a table at file offset `0x3F0CC6` / `0x3F0D76`
(VA `0x7F0CC6` / `0x7F0D76`)

```
#enum MAN_ANI_STAND,MAN_ANI_JUMPING,MAN_ANI_RUN,MAN_ANI_JUMPSTART,MAN_ANI_JUMPDOWN,MOVE_ANI_SLIDEEND,MOVE_ANI_SWIM
var m_szAniDef=(("01stand",2),("jumping",12),("03run",2.5),("jumpstart",31),("jumpdown",31),("jumping",8));
```

which names `01stand` and `03run` — **not** `stand01`/`run` — and maps each kind
to a *playback speed*, not a file. The four filenames that do appear
(`jumping`, `jumpdown`, `jumpstart`; `stand01` does not) are in a quoted block
(`/* … */`) of a superseded style, so this table does **not** establish the
model→clip binding.

**But `ziptabedit` does, and it is more specific than the name match suggests.**

`ziptabedit.gbc` ("动作包定义表编辑", action-pack definition table; netpak payload
`af3407ec`, 12 rows × 6 cols) is the missing model→clip map. Columns are
`系统名 / 原始动作包 / 附加动作包 / 查找索引 / 附加动作名 / 替换动作`, and the rows
|  |  |  |
| --- | --- | --- |
partition by `模板|人类男` (template: human male) and `模板|人类女` (human female):

| 系统名 (system) | 附加动作名 (base clips) | 替换动作 (replacement) |
|---|---|---|
| `人类_男_动作` | `jumpdown, jumping, jumpstart, walk, death, fly, fly_stand` | `stand01` |
| `人类_男_动作01` | `ready_quanji, attack_box02, ready_sword, attack_cross02, …` (16) | `quanji` |
| `人类_男_动作02` | attack/spell effects (`fsxiwang`, `zsxuanfenga`, …) (37) | `quanji` |
| `人类_男_动作03` | `swimidle, caiji, heyao, hit, knockdown, shiqu, shiyong, stun, swim, wakuang` | `stand01` |
| `人类_男_动作04` | social emotes (`qingzhu, kuqi, jingya, xingli, feiwen, …`) (26) | `stand01` |
| `人类_男_动作05` | mounted combat (`zuoqi_attack_…`, `zuoqi_ready_…`) (15) | `zuoqi` |

Three things follow, and they resolve most of the open question:

1. **The four shipped clips are a coherent "base action pack"**, not a random
   sample. `jumpdown, jumping, jumpstart` appear verbatim in row 1 alongside
   `walk, death, fly, fly_stand`. `run` is the one exception — it is not in any
   `附加动作名` list, though `walk` is, and `run.caf` and `stand01.caf` are the
   two 22,376-byte clips.
2. **`stand01` is the `替换动作` (replacement / fallback) clip**, the idle every
   system falls back to. That is exactly why it is the clip shipped and why
   `SetAniKind("stand01")` is the overwhelmingly common call site.
3. So "405 other clips absent" understates it: **one action pack out of six
   per gender, and only one of the two genders, is present at all.** The 12
   rows reference **111 distinct** `附加动作名` clip names between them, so the
   manifest's 406 zips are roughly 3–4 files per name (LOD or variant sets).

**The `系统名 → model` link is closed, and `ziptabedit` was the wrong place to
look for it.** `ziptabedit` is **player-only** — 12 rows, six per gender, keyed
|  |  |  |
| --- | --- | --- |
`模板|人类男` / `模板|人类女`. It never names a model because a player character
has no single model; it is assembled from equipment parts. NPCs, monsters and
mounts take a completely different path, and it is inline in `SysFileVer.var`:

```
npctabedit.模型 ──► SysFileVer[name] ──► "modle=,model/4428E5A6.cmz;
                                          tex=怪物_守墓人_1_m,tex/36DF1AFD.jpg;
                                          tex=怪物_守墓人_2_m,tex/F0CA0796.png;
                                          action=,action/1062CC2C.zip"
```

One row carries the skeleton **and** the clip pack. `_D` ("dynamic") names are
aliases that point straight at `action/<guid>.zip`; `_Z` ("static") names carry
the full spec. Both name the same GUID, which is the built-in cross-check:
**250 `_D`/`_Z` pairs, 0 disagreements** — that is what proves the spec parser is
right rather than merely plausible.

The player path resolves too, one hop further: `原始动作包` / `附加动作包` are
themselves `SysFileVer` keys, and all 16 `标准动作` names
(`人类_男_标准动作`, `…01`..`…06`, same for 女) map to `action/<guid>.zip`.
All 24 pack references in `ziptabedit` resolve.

Coverage over the shipped data:

| | count |
|---|---|
| distinct `npctabedit.模型` | 481 |
| with a `model/*.cmz` skeleton | 479 (2 have no spec at all) |
| with an `action/*.zip` pack | 366 (115 are static props — wells, crates, signs) |
| **skeleton actually shipped** | **1** |
| **pack actually shipped** | **1** |
| `ziptabedit` pack references | 24 (14 distinct names), all resolve, **0 shipped** |

That single shipped actor is `骑宠_机甲摩托1阶_Z` → `model/B056501A.cmz` +
`action/E41C7990.zip`, and the pack contains exactly `jumpdown, jumping,
jumpstart, run, stand01` — the five `.caf` files that ship loose in
`model/caf/`. Every `.caf` binds rig `b056501a`. So the retail client contains
**one** complete animated character — the mech mount — and the rest of the
animation system is intact but stripped. That is the whole story, and it
explains why `stand01` (row 1's `替换动作` fallback idle) is the clip every
`SetAniKind` call site reaches for.

`tools/action_link.py` resolves both paths and fails if any `_D`/`_Z` alias
disagrees → `extracted/tables/action_links.csv` (481 rows) and
`extracted/tables/action_packs.csv` (24 rows).

`tools/caf_decode.py` → `extracted/models/caf/*.json` (one per clip: header,
`rig`, `rig_file`, and per-track `bone` / `n_keys` / `keys`, each key being
`[time, tx, ty, tz, qx, qy, qz, qw]` with translations still in centimetres as
stored). `rig` is the `.cmz` JSON stem and `rig_file` is the name that skeleton
currently has on disk (`b056501a` here, not `2682c5d5` — the stem is stale, see
above). `tools/caf_decode.py check` is the byte-exact assertion;
`tools/caf_validate.py --strict` is the regression test.

**Rendering that actor.** `tools/render_anim.py` poses the rig per frame and
rasterises it to `extracted/models/anim/` (five 8-frame filmstrips plus one
large still per clip). Three things about it are worth recording, because each
one was a silent bug rather than a format unknown:

* **`render()` takes yaw/pitch in radians, not degrees.** `obj_render`'s own
  entry point converts with `math.radians`; the animation renderer passed
  degrees, so `cos(90.0)` was `cos(90 rad)` = −0.448 and the bike came out
  rolled ~27° with the wheels off the ground. The tell was arithmetic, not
  pixels: the two wheel centres are at world z ≈ 0.56 / 0.74 but landed at
  screen y +0.57 / −0.63.
* **`zoom` is a half-extent factor.** `scale = size*0.5*zoom/ext`, so `zoom=1.0`
  fits `ext` into *half* the canvas. `fit="screen"` (new, opt-in) measures the
  projected silhouette instead of the 3D bbox — the bbox diagonal over-pads a
  4.05 × 1.28 × 2.35 m object — and `frame=(ox, oy, ext)` pins one camera across
  every frame so an animation strip does not breathe.
* **The part base is a vertex offset, not a face count.** `load()` was adding
  `len(part_of)` (a per-face list) to each part's face indices, so parts 1–4
  pointed 74+ vertices too far. `caf_frames.py` always did `base += nv`, which
  is why the web viewer was correct while the rasteriser was not.

**The scene camera looks down, and once looked up.** `render_scene3d.py`'s
`view_transform()` packs the world into the frame `obj_render.render()` expects
— which, for `yaw=0, pitch=0, up="z"`, is `(right, away, up)`, *not*
`(right, up, away)`: `obj_render.rot()` maps its input `(p_x, p_y, p_z)` to
view space as `(p_x, p_z, p_y)`. An earlier version packed
`depth = z·cos(tilt) + y·sin(tilt)` and `up = z·sin(tilt) − y·cos(tilt)`, which
put **+z away from the camera and −y up the screen** — a camera below the
ground looking up. The terrain was then the nearest surface in every
direction, so it won the z-buffer everywhere: the village buildings were
invisible, every tree showed only the underside of its canopy, and a
"terrain vs terrain+building" pixel diff produced a branch-only silhouette
that read as a missing-model bug. The correct packing is
`depth = y·sin(tilt) − z·cos(tilt)`, `up = y·cos(tilt) + z·sin(tilt)`, with
`tilt` measured from the zenith (12 → plan, 55 → bird's-eye, 72 → horizon).
`camera_check` in `render_scene3d.py --check` asserts that a point 5 m above
the ground is both nearer to the camera and higher on screen than the ground
beneath it — the old transform fails the first of those.

**Textures come from the spec, not from the mesh.** The `.cmz` has no material
table, but the SysFileVer row carries the binding:
`modle=,model/B056501A.cmz;tex=机甲摩托_轮子,tex/3F736FBC.jpg;…`. Skin parts are
named after those materials (`机甲摩托_车身`), and the four `轮子` entries are
the shared material for `前轮`/`后轮`. All five parts bind, 2,932/2,932 faces
textured, UVs are per-vertex and already in `0..1`. `render_anim.py --check` is
the fast invariant (5/5 textures, UV range, no out-of-range face index).

### 4.10 Particle effects — `netpak/effect/*.txt`

The engine's effect format is **plain GBK text**, not a binary container, and it
is self-describing: the first line is `//Particle 1.00 for GBoxII`. Only three
files ship (`42da2fff`, `df3269c5`, `f30b8336`, 2.5–4.4 KB each) and two of them
are literally `m_sName='Test'` — engine demo scenes. `SysFileVer.var` registers
**1,777** `effect/...` paths (skill hits, mount trails, `quest_complete`,
`levelup`, `resurrect`, per-locale variants `_CN/_EN/_JP/_VN`), and hashing a
sample of them against the archive finds nothing: the real effect library is
referenced but not shipped. What the client does ship is the *format*, which is
enough to read any effect the server ever names.

Grammar: `key=value` lines, `//` comments, and `:Block` headers. Nesting is by
tab depth — a block at depth *i* owns properties at depth ≥ *i* and children at
depth > *i* (the elements' own properties sit at the same depth as the header,
which is the one trap in the parser).

```
//Particle 1.00 for GBoxII
m_sName='Test'                 m_nTotalTime=-1      m_sSndFileName='0'
:Atom                                             element: billboard particle
    m_sTextureName='smoke123.jpg'   m_nShowMode=2   m_nWorldSpace=0
    m_nBillboardKind=1  m_nAlginOrg=4  m_fMass=1  m_fSpeedStretch=0
    m_vAtomScale=(3,(0.01,0.64),(0.56,0.73),(1,0.9))     keyframed
    m_vAtomRed=1  m_vAtomGreen=1  m_vAtomBlue=1
    m_vAtomAlpha=(3,(0.02,0.03),(0.42,0.21),(0.81,0.48),(0.99,0.02))
    m_fFrameBegin=0  m_fFrameEnd=1000
    :Emitter                                          behaviour blocks
        m_nEmitRate / m_nChanceToEmit / m_nEmitDuration / m_nDelay / m_nNumLoops
        m_vPosition=v3(0,0,1)   m_vDirection=v3(0,0,-1)   m_nDirKind=0
        m_fVelocity  m_fAngle  m_nRotoKind  m_fRoll=(2,-90,90)
        m_fParticleLife  m_fStartSize  m_fStartHeight
    :Gravity   m_vGravity=v3(0,1,1.5)
:Ribbon                                            element: trail
    m_sTextureName='ray31.jpg'  m_nOutTimer=1000  m_fSpeedScale=1800
    :R_Twist   m_sBTag='w'  m_vSOff=v3(0,-0.5,0)  m_fRWidth=2  m_fRad=0.05
```

Value forms: `'text'`, bare int/float, `v3(x,y,z)`, `(n, v1…vn)` flat tuple, and
`(n,(t,v)×n)` — a piecewise-linear curve over normalised time. Curves are how
scale, colour and alpha are animated; `m_fFrameEnd=1000` says the atom lives on
a 1000-frame timeline.

Two element types (`Atom`, `Ribbon`) and eight behaviour blocks
(`Emitter`, `Roto`, `Gravity`, `GWell`, `ECircle`, `Vortex`, `Snd`, `R_Twist`)
cover all three files. `m_sBTagName` / `m_sBTag` bind a block to a skeleton
attachment point — the same `Bip01`-space sockets the `.cmz` `MAP1` chunk
lists, which is how a trail follows a weapon.

The three shipped effects reference `SoftFlare.jpg`, `GlowBlue.jpg` (present
next to them in `effect/`), and `HEIWU.png`, `HEIWU2.png`, `ray31.jpg`,
`smoke123.jpg`, `mysterySmoke2.jpg`, `specks_additive.jpg` (absent).

`tools/effect_ini.py` → `extracted/netpak/effect/_parsed.json`, with a summary
of elements, behaviours and textures per file.

---

## 5. The pipeline

```
tools/gpak.py              format library: page decode, cipher, CRC, MPQ hash, archive reader
tools/gpak_extract.py      CLI: list | verify | names | extract
tools/gpak_write.py        writer: page/dir/free-list emitter, byte-exact rebuild()
tools/gpak_repack.py       CLI: roundtrip | fresh  (verifies the writer)
tools/names.json           recovered filename table (hash -> path)
tools/propagate_names.py   #include-graph filename propagation
tools/apply_names.py       rename already-extracted unknown/* files once their
                           hash has a path (avoids re-extracting 88 MB)
tools/varfile.py           `.var` + `varbin` tagged-tree decoder (both modes)
tools/varbin_tree.py       CLI: check | show | schema | csv  (tagged-tree view)
tools/varbin_extract.py    entity DB: tokens | text | keys (label->value cross-tab)
tools/varbin_identify.py   blob -> game table: column count + field-type signature,
                           one-to-one assignment (exact/signature/unshipped/map)
tools/varbin_tables.py     exports the 42 identified tables to extracted/tables/*.csv
tools/map_decode.py        the 6 .zmap map payloads -> extracted/maps/ (JSON, PNG, CSV)
tools/render_scenes.py     top-down scene layouts (heightmap + deco markers) -> PNG
tools/render_scene3d.py    textured 3D scene: terrain + ground splat + instanced
                           props + placements, three cameras -> extracted/maps/*_3d*.png
tools/effect_ini.py        particle effect .txt -> JSON (elements, behaviours, textures)
tools/action_link.py       model -> skeleton + action pack, both selection paths
                           (NPC spec / player template) -> extracted/tables/action_*.csv
tools/build_explorer.py    one static page over every dataset -> extracted/explorer/
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
prefixes discovered from the corpus, hash each, and intersect with the 484 entry hashes.
This alone yields **90 / 484**.

**Stage 2 — `#include` propagation.** The `.gbc` scripts are a dependency graph: a file
whose name is known names the files it pulls in. Parse `#include "…"` and quoted asset
references out of every *named* script, resolve them relative to that script's own
directory (`posixpath.join` / `normpath`), hash the result, and repeat until nothing new
appears. Three rounds: `90 → 188 → 228 → 231`.

- **231 / 484** entries now resolve to real paths, e.g. `tabedit/itemtabedit.gbc`,
  `ui/bag/bagcom.gbc`, `country/en/trans.csv`, `world/terrain.gbc`, `ui/cooldown.png`,
  `SysFileVer.var` (2.4 MB).
- Per archive: `sysnet` **229 / 336 (68%)**, `netpak` **2 / 148 (1%)**.
- `netpak` is Chinese-named model data (`人类_男_战士7档_衣服_T.jpg`), and probing those
  decoded names directly against its hashes returns **zero** hits — its directory keys are
  produced somewhere else (another pak, or the server), so it stays hash-only.
- A wrong name is impossible: a hit means the 32-bit hash collided, which for 1.8M
  candidates against 484 targets is a ~0.2 expected false-positive count.

**Stage 3 — the `.var` asset registry.** `SysFileVer.var` stores real paths for
9,975 assets, and hashing them resolves 96 more entries that no amount of token
harvesting reached: `231 → 327`. See [`SYSFILEVER.md`](SYSFILEVER.md) §5.

**Stage 4 — the packer's own manifest.** `unknown/3602e346.bin` in *both*
archives is not an unknown blob: it is a `.var` file with **seed 100**
(`magic = 0xFFABC257 + 0x64 = 0xFFABC2BB`) holding the file list the packer
intended to ship. It decodes with the existing reader, byte-exactly, with zero
checksum failures — the first time the *encrypted* `.var` branch has been
exercised by real data, since `SysFileVer.var` uses seed 0.

    [ "FW405IGdT5", [ ["action/1062cc2c.zip", 79172, 1335165054], ... ] ]

`[build tag, [[path, size, mtime], ...]]`. Hashing those paths resolves every
entry the manifest lists that actually shipped: **327 → 433 / 484 (89.5%)**,
of which 106 are newly named and 32 were already known under different casing.
There were **zero** real conflicts — `hash_name` lower-cases internally, so the
manifest's casing is cosmetic — which independently re-validates every name
recovered in stages 1–3.

```bash
python asset_manifest.py check        # both decode byte-exactly
python asset_manifest.py stats        # per-directory listed/missing
python asset_manifest.py names        # dry run; --apply to merge
python asset_manifest.py csv -o m.csv # all 9,007 manifest rows
```

What the manifest reveals is more useful than the names. The packer's list is
**9,007 paths**, but only 432 of them are in the archives:

| archive | listed | shipped | note |
|---|---:|---:|---|
| netpak | 8,672 | 97 | **8,575 listed paths are absent** |
| sysnet | 335 | 335 | ships in full |

`netpak` is a **1.1% sample of a much larger asset tree**: `tex` alone lists
2,156 files of which 2,120 are missing, `model` 2,107 (2,076 missing), `effect`
1,795 (1,790 missing), and there are 405 map paths, 407 `action/*.zip` clips,
751 `chipmap` entries. So the install is a stripped retail cut, not a complete
client. This also explains why §4.5 could not find a name→GUID table in the
shipped client — the manifest *does* name the models that do ship, but the bulk
of the art was never delivered.

Consequence for the residual unknown set: `sysnet` is now **335 / 336** named
(the last one is this manifest itself). `netpak` stays at **51 / 148**, and the
manifest confirms that set is abiological containers rather than a lookup
failure: 42 `varbin` bundles, the 8 `BE119407`-wrapped tables, `SysFileVer.var`
itself (`D7E9272B`, 2,405,900 B — the only netpak entry whose size is not a round
number), and this manifest. None of them has a path.

Because extraction names entries when it runs, names recovered afterwards leave
the tree stale — files keep sitting in `unknown/<hash>.<ext>` although the hash
now has a path. `tools/apply_names.py` renames them in place instead of
re-extracting 88 MB:

```bash
python gpak_extract.py names gbox/lm_en/netpak.gpak gbox/lm_en/sysnet.gpak
python propagate_names.py
python varfile.py xref extracted/netpak/SysFileVer.var \
                        extracted/netpak/_manifest.csv --merge tools/names.json
python apply_names.py --apply
```

Note the two hash encodings: `unknown/` filenames are **hex**, `names.json` keys
are **decimal**. Comparing them directly reports zero overlap and makes it look
as though nothing is recoverable.

**What is left: 51 of 484** (was 157 before stage 4). The split is informative,
not just a remainder:

| archive | n | what |
|---|---|---|
| netpak | 51 | 42 `varbin` bundles + 8 `BE119407` tables + `SysFileVer.var` + the manifest — **containers**, with no path reference anywhere |
| sysnet | 1 | the manifest itself (`3602e346.bin`) |

The netpak half is not merely unsolved, it is unnameable: those are the entity-DB
bundles, addressed by content, and §4.2's `GetAllData` finding explains why their
keys are produced outside these archives. Every *asset* in both archives now has
a path.

Script literals are exhausted as a source for the rest: harvesting every
path-like literal from all 235 `.gbc` files yields 454 distinct strings, 65 of
which are already-known names — so the method works — and **zero** of which
resolve any of the names that were still missing before stage 4. The scripts
build image paths by runtime concatenation
(`"ui/" + panel + "/bg.png"`), so only combinatorial assembly (dir fragment ×
stem × ext) could close the remainder.

### Entity database

```bash
python varbin_dump.py        # unpack the 49 bundles to work/varbin/*.bin + hex view
python varbin_tree.py check  # decode all 48 payloads, assert byte-exact
python varbin_tree.py show 0580853e   # pretty-print one payload as JSON
python varbin_identify.py    # blob -> game table map -> varbin_identity.json
python varbin_tables.py      # 42 identified tables -> extracted/tables/*.csv
python map_decode.py         # 6 .zmap maps -> extracted/maps/
python build_explorer.py     # everything above -> extracted/explorer/index.html
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

