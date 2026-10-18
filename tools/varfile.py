"""Read and export the engine's `.var` binary serialisation.

Recovered from `file::LoadVarAsFile` (VA 0x540060), its sibling
`file::SaveVarAsFile` (VA 0x53FF20) -- both registered in the file-format
table at VA 0x53EC6A -- and the value writer at VA 0x42C190, which was
read instruction by instruction to pin down every payload size.

File layout
-----------
    u32 magic, then exactly one tagged `var`, then EOF
    magic = flag + 0xFFABC257        (flag == 0 -> 0xFFABC257, as shipped)

`flag` is a *seed*: the payload is protected by a rolling XOR key that
starts at `flag` and advances as

    key = (key * 214017 + 0x269EC3) & 0xFFFFFFFF

once per string character (writer VA 0x42C6C5) and once per array element,
at the loop head (writer VA 0x42C991).  Scalar payloads never advance it.
With flag == 0 the first key is 0, so early scalars read as plaintext --
that is what makes the file look half-decoded.

Tag table (all eighteen tags the writer can emit)
-------------------------------------------------
    0x00..0x0F   small int, value is the tag itself, no payload
    0x10         u8   ^ key8
    0x11         u16  ^ key16
    0x12         i32  ^ key
    0x13         f32  ^ key
    0x14/0x15/0x21  64-bit, two dwords ^ key
    0x16         vec3, three u32 ^ key
    0x20         u32  ^ key
    0x30         empty string, no payload
    0x31         string: u32 char-count ^ key, then chars (u16 ^ key16,
                 key advancing per char), then a **plain** u32 checksum
                 = 2n + 9 bytes total
    0x32         binary blob: u32 len ^ key, then dwords chained-xored
    0x33         binary blob: u32 len ^ key, then len bytes stored verbatim
    0x40         array, count as u8 ^ key8    (only when count < 0xFF)
    0x41         array, count as u32 ^ key     (count >= 0xFF)
    0x50/0x51    narrow (UTF-8) string, u32 / u16 byte count, no checksum
    0xFF         null

Both array forms end with a **plain** u32 copy of the final key.  That
redundancy is a free 32-bit integrity check on the whole key schedule: if
it ever disagrees, the parser has lost sync.

String checksum
---------------
Seeded with the key at the string's first character, then folded per char

    acc = char ^ rol32(acc, 1)

-- the same fold as the archive page checksum (`sub_416D70`), but over
16-bit units.  It is stored unencrypted, so it is an exact oracle for the
string's true length even when the length field looks implausible.

The varbin variant
------------------
The 48 `varbin` payloads inside `netpak.gpak` use the *same tag grammar*
with the encryption removed: no seed, no key advance, no string checksum,
no array trailer, and `0x32` blobs stored as `u32 len` + `len` raw bytes
without dword padding.  `load_varbin()` reads that form.  All 48 payloads
parse to their exact last byte.
"""
import argparse
import collections
import csv
import json
import pathlib
import re
import struct
import sys

MAGIC_BASE = 0xFFABC257
MULT = 214017
ADD = 0x269EC3
MASK = 0xFFFFFFFF


def advance(k):
    """One step of the key schedule (writer VA 0x42C6C5 / 0x42C991)."""
    return (k * MULT + ADD) & MASK


def rol(v, n=1):
    return ((v << n) | (v >> (32 - n))) & MASK


class VarError(Exception):
    pass


class Reader:
    """Cursor over a `.var` / `varbin` payload.

    Two modes share the tag grammar:

      `plain=False`  the `.var` file — rolling XOR key, string checksums,
                     array trailers
      `plain=True`   a varbin payload — everything in the clear: no key,
                     no checksums, no trailers
    """

    def __init__(self, buf, key=0, plain=False):
        self.b = buf
        self.p = 0
        self.key = 0 if plain else key
        self.plain = plain
        self.arrays = 0
        self.strings = 0
        self.blobs = 0
        self.bad_checksums = 0
        self.first_bad = None
        self.tags = collections.Counter()

    # -- bounds-checked primitives -------------------------------------
    def need(self, n, what):
        if self.p + n > len(self.b):
            raise VarError(f"eof reading {what} at 0x{self.p:x}")

    def u8(self):
        self.need(1, "u8")
        v = self.b[self.p] ^ (0 if self.plain else self.key & 0xFF)
        self.p += 1
        return v

    def u16(self):
        self.need(2, "u16")
        v = struct.unpack_from("<H", self.b, self.p)[0]
        if not self.plain:
            v ^= self.key & 0xFFFF
        self.p += 2
        return v

    def u32(self):
        self.need(4, "u32")
        v = struct.unpack_from("<I", self.b, self.p)[0]
        if not self.plain:
            v ^= self.key
        self.p += 4
        return v

    # -- strings -------------------------------------------------------
    def string(self):
        start = self.p
        n = self.u32()
        # No fixed cap: some 组合 records carry one path per material slot
        # and a few models were exported with the texture list duplicated
        # hundreds of times (largest seen: 264,437 chars = 528 KB).
        self.need(n * 2, f"string body (len {n}) at 0x{start:x}")
        if self.plain:
            raw = self.b[self.p:self.p + n * 2]
            self.p += n * 2
            self.strings += 1
            return raw.decode("utf-16-le", "replace")

        self.need(4, "string checksum")
        acc = self.key
        out = bytearray()
        for _ in range(n):
            c = struct.unpack_from("<H", self.b, self.p)[0] ^ (self.key & 0xFFFF)
            self.p += 2
            acc = c ^ rol(acc)
            out += struct.pack("<H", c)
            self.key = advance(self.key)
        stored = struct.unpack_from("<I", self.b, self.p)[0]
        self.p += 4
        self.strings += 1
        if stored != acc:
            self.bad_checksums += 1
            if self.first_bad is None:
                self.first_bad = (start, stored, acc)
        return out.decode("utf-16-le", "replace")

    def narrow(self, wide_len):
        """0x50 / 0x51: UTF-8 string with a u32 or u16 byte count."""
        n = self.u32() if wide_len else self.u16()
        self.need(n, f"narrow string (len {n}) at 0x{self.p:x}")
        if self.plain:
            raw = self.b[self.p:self.p + n]
        else:
            raw = bytes(c ^ (self.key & 0xFF)
                        for c in self.b[self.p:self.p + n])
        self.p += n
        return raw.decode("utf-8", "replace")

    def blob(self):
        """0x32: binary blob.

        Plain (varbin): `u32 len` then `len` raw bytes -- verified on the six
        map payloads.

        Encrypted (`.var`): the writer at VA 0x42C430 copies dword-at-a-time
        with a *chained* xor.  The chain runs over the **plaintext** dword --
        `mov eax, [edi-4]` re-reads the source buffer, not the output -- so

            plain_i  = raw_i ^ key_i
            key_i+1  = plain_i ^ rol32(key_i, 1)

        and every 256th dword is followed by a redundant copy of the new key
        (`cmp dl, 0xff` at 0x42C461).  A plain u32 copy of the *unchanged*
        shared key terminates the blob.  No `.var` in the install uses this
        tag, so this branch is read from the writer but never exercised; it
        is validated by encoding with the same rule and decoding back.
        """
        n = self.u32()
        if self.plain:
            self.need(n, f"blob ({n} bytes) at 0x{self.p:x}")
            raw = self.b[self.p:self.p + n]
            self.p += n
            self.blobs += 1
            return raw

        ndw = (n + 3) // 4
        key = self.key
        out = bytearray()
        for i in range(ndw):
            self.need(4, f"blob dword at 0x{self.p:x}")
            w = struct.unpack_from("<I", self.b, self.p)[0]
            self.p += 4
            plain = w ^ key
            out += struct.pack("<I", plain)
            key = plain ^ rol(key, 1)
            if (i & 0xFF) == 0xFF:
                self.need(4, "blob pad dword")
                self.p += 4
        self.need(4, "blob trailer")
        self.p += 4
        self.blobs += 1
        return bytes(out[:n])

    def blob_verbatim(self):
        """0x33: binary blob stored verbatim.

        Writer VA 0x42C4CB: `u32 len ^ key`, then a straight `rep movsd` /
        `rep movsb` copy -- **no** obfuscation of the bytes -- then a plain
        u32 copy of the unchanged key.  Total `9 + len`.

        Never occurs in any shipped `.var` or `varbin`; the plain form is
        assumed to mirror 0x32 and drop the trailer, which is unproven.
        """
        n = self.u32()
        self.need(n, f"verbatim blob ({n} bytes) at 0x{self.p:x}")
        raw = self.b[self.p:self.p + n]
        self.p += n
        if not self.plain:
            self.need(4, "verbatim blob trailer")
            self.p += 4
        self.blobs += 1
        return raw

    # -- values --------------------------------------------------------
    def value(self, depth=0):
        if depth > 64:
            raise VarError(f"nesting too deep at 0x{self.p:x}")
        self.need(1, "tag")
        t = self.b[self.p]
        self.p += 1
        self.tags[t] += 1
        if t <= 0x0F:
            return t
        if t == 0xFF:
            return None
        if t == 0x10:
            return self.u8()
        if t == 0x11:
            return self.u16()
        if t == 0x12:
            return self.u32()
        if t == 0x13:
            return struct.unpack("<f", struct.pack("<I", self.u32()))[0]
        if t == 0x20:
            return self.u32()
        if t in (0x14, 0x15, 0x21):
            lo = self.u32()
            hi = self.u32()
            return (hi << 32) | lo
        if t == 0x16:
            return tuple(self.u32() for _ in range(3))
        if t == 0x30:
            return ""
        if t == 0x31:
            return self.string()
        if t == 0x32:
            return self.blob()
        if t == 0x33:
            return self.blob_verbatim()
        if t == 0x51:
            return self.narrow(False)
        if t == 0x50:
            return self.narrow(True)
        if t == 0x40:
            return self.items(self.u8(), depth)
        if t == 0x41:
            return self.items(self.u32(), depth)
        raise VarError(f"unknown tag {t:#04x} at 0x{self.p - 1:x}")

    def items(self, n, depth):
        self.arrays += 1
        if n > len(self.b):
            raise VarError(f"array count {n} at 0x{self.p:x}")
        out = []
        for _ in range(n):
            if not self.plain:
                self.key = advance(self.key)  # writer VA 0x42C991
            out.append(self.value(depth + 1))
        if self.plain:
            return out
        self.need(4, "array trailer")
        stored = struct.unpack_from("<I", self.b, self.p)[0]
        if stored != self.key:
            raise VarError(
                f"array trailer mismatch at 0x{self.p:x}: "
                f"stored {stored:#010x} != key {self.key:#010x}")
        self.p += 4
        return out


def load(path):
    """Decode a whole `.var` file.  Raises `VarError` if it does not parse."""
    d = pathlib.Path(path).read_bytes()
    if len(d) < 8:
        raise VarError("file too short")
    magic = struct.unpack_from("<I", d, 0)[0]
    flag = (magic - MAGIC_BASE) & MASK
    rd = Reader(d[4:], flag)
    value = rd.value()
    return dict(path=str(path), magic=magic, flag=flag, value=value,
                consumed=rd.p, total=len(rd.b), arrays=rd.arrays,
                strings=rd.strings, bad_checksums=rd.bad_checksums,
                first_bad=rd.first_bad, tags=rd.tags)


def load_varbin(data):
    """Decode a varbin payload: the same tag grammar, entirely in the clear.

    `data` is the *unwrapped* payload — run it through `gpak.unwrap` first,
    because the archive entry is a `0x602376D7` minizip wrapper around it.
    Unlike a `.var` there is no seed, no rolling key, no string checksum and
    no array trailer; strings are bare UTF-16 and `0x32` blobs are
    `u32 len` + `len` raw bytes with no padding.
    """
    if isinstance(data, (str, pathlib.Path)):
        data = pathlib.Path(data).read_bytes()
    rd = Reader(data, plain=True)
    value = rd.value()
    return dict(value=value, consumed=rd.p, total=len(data),
                arrays=rd.arrays, strings=rd.strings, blobs=rd.blobs,
                tags=rd.tags)


# --- SysFileVer.var record schema -------------------------------------
#
# Every record is a 0x40 array of exactly 17 values:
#
#   0  name        logical asset name, e.g. 'BOSS_棕毛王_T01'
#   1  path        'tex/caa3efe4.jpg', or for 组合 a 'modle=..;tex=..' spec
#   2  kind        贴图 模型 动作 组合 特效 数据 声效 地表 表格 天空
#                  图标 场景平面 UI SenceJpg 版本 表格模板文件
#   3  group       '' 固定场景 PC 怪物 NPC
#   4  ''          always empty
#   5  ''          always empty
#   6  0           always 0
#   7  ''          always empty
#   8  0           always 0
#   9  0           always 0
#   10 0           always 0
#   11 flag        '0' / '1' / '' / '无' (stored as text)
#   12 transforms  [] or [[(u32, u32, u32), f32], ...]  or null
#   13 vec         [] or [(u32, u32, u32)]              or null
#   14 id          small positive integer, 1..~123k
#   15 0           always 0
#   16 0           always 0

FIELDS = ["name", "path", "kind", "group", "f4", "f5", "f6", "f7", "f8",
          "f9", "f10", "flag", "transforms", "vec", "id", "f15", "f16"]

# `tex=DisplayName,tex/1a2b3c4d.jpg` inside a 组合 path
PAIR = re.compile(r"(?:^|;)(\w+)=([^,;]*),([^;]+)")
# a bare asset path such as `model/b056501a.cmz`
BARE = re.compile(r"^(?:\./)?([\w]+)/([0-9a-fA-F]{8})\.(\w+)$")


def guid_map(records):
    """Map 8-hex asset GUID -> human name, from the manifest itself.

    Two sources: the `tex=Name,tex/GUID.jpg` pairs inside 组合 specs (which
    carry the display name directly), and the record name for plain paths.
    """
    out = {}
    for rec in records:
        if not isinstance(rec, list) or len(rec) < 3:
            continue
        name, path = rec[0], rec[1]
        m = BARE.match(path)
        if m:
            out.setdefault(m.group(2).lower(), name)
        for kind, disp, sub in PAIR.findall(path):
            b = BARE.match(sub.strip())
            if b and disp:
                out.setdefault(b.group(2).lower(), disp)
    return out


# --- CLI --------------------------------------------------------------
def cmd_stats(a):
    r = load(a.var)
    print(f"{a.var}")
    print(f"  magic        {r['magic']:#010x}   flag {r['flag']:#010x}")
    print(f"  consumed     {r['consumed']} of {r['total']} bytes"
          f"{'  (exact)' if r['consumed'] == r['total'] else '  !! LEFTOVER'}")
    print(f"  arrays       {r['arrays']}")
    print(f"  strings      {r['strings']}   bad checksums {r['bad_checksums']}")
    if r["first_bad"]:
        p, s, c = r["first_bad"]
        print(f"  first bad    at 0x{p:x} stored {s:#010x} calc {c:#010x}")
    print("  tags         " + "  ".join(
        f"{t:#04x}:{c}" for t, c in sorted(r["tags"].items())))
    v = r["value"]
    if isinstance(v, list):
        print(f"  top level    array of {len(v)}")
        lens = collections.Counter(len(x) for x in v if isinstance(x, list))
        if len(lens) == 1:
            print(f"  records      all {next(iter(lens))} fields")
        for k, c in collections.Counter(
                x[2] for x in v if isinstance(x, list) and len(x) > 2
        ).most_common():
            print(f"    {c:6d}  {k}")


def cmd_dump(a):
    r = load(a.var)
    v = r["value"]
    rows = v if isinstance(v, list) else [v]
    for rec in rows[:a.limit]:
        if isinstance(rec, list):
            print(" | ".join("" if x in ("", [], None) else
                             (x if isinstance(x, (int, float)) else
                              (x if len(str(x)) < 40 else str(x)[:37] + "..."))
                             for x in rec))
        else:
            print(rec)


def cmd_csv(a):
    r = load(a.var)
    v = r["value"]
    rows = v if isinstance(v, list) else [v]
    out = open(a.out, "w", newline="", encoding="utf-8-sig") if a.out else sys.stdout
    w = csv.writer(out)
    w.writerow(FIELDS)
    n = 0
    for rec in rows:
        if not isinstance(rec, list):
            continue
        w.writerow([json.dumps(x, ensure_ascii=False) if isinstance(x, (list, tuple))
                    else ("" if x is None else x) for x in rec])
        n += 1
    if a.out:
        out.close()
        print(f"wrote {n} records to {a.out}")


def cmd_guids(a):
    r = load(a.var)
    v = r["value"]
    rows = v if isinstance(v, list) else [v]
    m = guid_map(rows)
    items = sorted(m.items())
    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["guid", "name"])
            w.writerows(items)
        print(f"wrote {len(items)} guid -> name pairs to {a.out}")
    else:
        for g, n in items[:a.limit]:
            print(f"{g}  {n}")


def cmd_xref(a):
    """Recover archive entry names by hashing the manifest's paths."""
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from gpak import hash_name

    r = load(a.var)
    v = r["value"]
    rows = v if isinstance(v, list) else [v]
    by_hash = {}
    for rec in rows:
        if isinstance(rec, list) and len(rec) > 2:
            p = str(rec[1]).replace("\\", "/")
            by_hash.setdefault(hash_name(p), (rec[0], p, rec[2]))

    text = pathlib.Path(a.manifest).read_text(encoding="utf-8")
    unnamed = [x for x in csv.DictReader(text.splitlines()) if not x["name"]]
    hits = []
    for row in unnamed:
        key = int(row["hash"], 16)
        if key in by_hash:
            hits.append((row["path"],) + by_hash[key])
    print(f"unnamed entries {len(unnamed)}   resolved {len(hits)}   "
          f"still unknown {len(unnamed) - len(hits)}")

    if a.merge:
        cache = pathlib.Path(a.merge)
        table = (json.loads(cache.read_text(encoding="utf-8"))
                 if cache.exists() else {})
        added = 0
        for ap, name, vp, kind in hits:
            h = hash_name(vp)
            if str(h) not in table:
                table[str(h)] = vp
                added += 1
        cache.write_text(json.dumps(table, ensure_ascii=False, indent=0,
                                    sort_keys=True), encoding="utf-8")
        print(f"merged {added} new names into {cache} "
              f"({len(table)} total)")

    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["archive_path", "name", "var_path", "kind"])
            w.writerows(hits)
        print(f"wrote {a.out}")
    elif not a.merge:
        for ap, name, vp, kind in hits[:40]:
            print(f"  {ap:34s} -> {vp:44s} {name!r} [{kind}]")


def cmd_selftest(a):
    """Round-trip the two blob framings, which no shipped file exercises."""
    def enc32(data, key):
        n = len(data)
        out = bytearray(b"\x32") + struct.pack("<I", n ^ key)
        k = key
        for i in range((n + 3) // 4):
            raw = int.from_bytes(data[i * 4:i * 4 + 4].ljust(4, b"\0"), "little")
            out += struct.pack("<I", raw ^ k)
            k = raw ^ rol(k, 1)
            if (i & 0xFF) == 0xFF:
                out += struct.pack("<I", k)
        return bytes(out + struct.pack("<I", key))

    def enc33(data, key):
        return (b"\x33" + struct.pack("<I", len(data) ^ key) + data
                + struct.pack("<I", key))

    bad = 0
    sizes = (0, 1, 3, 4, 5, 7, 8, 255, 256, 257, 1000, 1023, 1024, 5000)
    for n in sizes:
        data = bytes((i * 37 + 11) & 0xFF for i in range(n))
        for key in (0, 0xDEADBEEF, 0x269EC3):
            for enc, tag in ((enc32, "0x32"), (enc33, "0x33")):
                blob = enc(data, key)
                rd = Reader(blob, key)
                got = rd.value()
                if got != data or rd.p != len(blob):
                    bad += 1
                    print(f"  FAIL {tag} n={n} key={key:#010x} "
                          f"consumed {rd.p}/{len(blob)}")
    print(f"{len(sizes) * 3 * 2} round-trips, {bad} failures")
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("selftest", help="round-trip the blob framings")
    p.set_defaults(fn=cmd_selftest)

    p = sub.add_parser("stats", help="decode and summarise")
    p.add_argument("var")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("dump", help="print records")
    p.add_argument("var")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.set_defaults(fn=cmd_dump)

    p = sub.add_parser("csv", help="export records as CSV")
    p.add_argument("var")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_csv)

    p = sub.add_parser("guids", help="export GUID -> name pairs")
    p.add_argument("var")
    p.add_argument("-o", "--out")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.set_defaults(fn=cmd_guids)

    p = sub.add_parser("xref", help="name archive entries from the manifest")
    p.add_argument("var")
    p.add_argument("manifest")
    p.add_argument("-o", "--out")
    p.add_argument("--merge", metavar="NAMES_JSON",
                   help="fold the hits into a names.json cache")
    p.set_defaults(fn=cmd_xref)

    a = ap.parse_args()
    try:
        a.fn(a)
    except VarError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
