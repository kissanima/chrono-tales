"""GBox2 .gpak reader.

Format reverse-engineered from chronotales.exe (GBoxII engine, 2013).

Container
---------
  0x00  char  magic[4]         "KAPG" == dword 0x4750414B  ("GPAK" byte-reversed)
  0x04  u32   headerSize       0x54
  0x08  u32   version          0x64 (low 16 bits); high 16 bits must be 0
  0x0C  u32   reserved         0
  0x10  u32   reserved         (1000 + entryCount on files produced by this build)
  0x14  u32   entryCount
  0x18  u32   writeTime        unix seconds
  0x1C  u32   entryCapacity    == (dirPages*4096 - 0x54) / 536
  0x20  u32   dirPageCount
  0x24  u32   reserved
  0x28  u32   totalPageCount   == fileSize >> 12
  0x2C  u32   freePageCount
  0x30  u32   firstFreePage
  0x34  ...   reserved (zero) to 0x54

Directory: a flat array of 536-byte records starting at 0x54 (inside page 0),
sorted ascending by nameHash.  Record:

  0x000 u32 nameHash     MPQ HashString(lower(path), type 0)
  0x004 u32 writeTime
  0x008 u32 nameHash2    MPQ HashString(path, type 1)
  0x00C u32 writeTime2
  0x010 u32 size         logical size in bytes
  0x014 u32 startPage    first page of the file's page chain
  0x018 ... 0x217        unused on disk (marker 0xFA21 then stale heap bytes)

Pages (4096 bytes each).  Page 0 doubles as the container header, so its
payload area begins at 0x54.  Every other allocated page:

  0x000 u32 magic      0xADAE
  0x004 u32 prevPage   (0 = first page of a file)
  0x008 u32 nextPage   (0 = last page of a file)
  0x00C u32 reserved
  0x010 u32 chunkIndex index of this page within the file
  0x014 u32 seed       key for the payload cipher
  0x018 u32 reserved
  0x01C u32 dataLen    <= 4060
  0x020 ...            payload, XOR'd with an LFSR keystream
  0xFFC u32 checksum   gbox_crc(payload)

Payload cipher (sub_416BB0):
    k = seed
    repeat: k = ((k << 1) & 0xE7BD2160) + 1; byte ^= k & 0xFF
  seed 0 degenerates to a constant 0x01 key, which is what this build writes
  for most pages.

Checksum (sub_416D70):
    acc = 0; for each byte: acc = rol32(acc, 1) ^ byte
"""

from __future__ import annotations

import io
import os
import struct
import sys
import zipfile

PAGE = 4096
HDR_SIZE = 0x54
PAGE_HDR = 0x20
MAX_PAYLOAD = 0xFDC
PAGE_MAGIC = 0xADAE
CONTAINER_MAGIC = 0x4750414B
ENTRY_SIZE = 536
ENTRY_DATA = 0x54
LFSR_MASK = 0xE7BD2160


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #

def decode_payload(seed: int, src: bytes) -> bytes:
    """Undo the per-page LFSR-XOR stream cipher."""
    out = bytearray(len(src))
    k = seed & 0xFFFFFFFF
    for i in range(len(src)):
        k = (((k << 1) & 0xFFFFFFFF) & LFSR_MASK) + 1
        out[i] = src[i] ^ (k & 0xFF)
    return bytes(out)


def gbox_crc(buf: bytes) -> int:
    """GBox2 page checksum: rol32(acc, 1) ^= byte."""
    acc = 0
    for b in buf:
        acc = ((acc << 1) | (acc >> 31)) & 0xFFFFFFFF
        acc ^= b
    return acc


_CRYPT_TABLE: list[int] | None = None


def crypt_table() -> list[int]:
    """Blizzard MPQ crypt table (sub_44ACB0)."""
    global _CRYPT_TABLE
    if _CRYPT_TABLE is None:
        tbl = [0] * 0x500
        seed = 0x00100001
        for i1 in range(0x100):
            i2 = i1
            for _ in range(5):
                seed = (seed * 125 + 3) % 0x2AAAAB
                t1 = (seed & 0xFFFF) << 16
                seed = (seed * 125 + 3) % 0x2AAAAB
                t2 = seed & 0xFFFF
                tbl[i2] = (t1 | t2) & 0xFFFFFFFF
                i2 += 0x100
        _CRYPT_TABLE = tbl
    return _CRYPT_TABLE


def hash_name(name: str, hash_type: int = 0) -> int:
    """MPQ HashString as used by GBox2 (sub_44AD30).

    Input is treated as UTF-16; ASCII letters are lower-cased and backslashes
    normalised to forward slashes before hashing.
    """
    tbl = crypt_table()
    base = (hash_type & 7) << 8
    s1 = 0x7FED7FED
    s2 = 0xEEEEEEEE
    for ch in name:
        c = ord(ch)
        if c == 0x5C:
            c = 0x2F
        elif 0x41 <= c <= 0x5A:
            c += 0x20
        if c > 0x100:
            s1 = (s1 + s2) & 0xFFFFFFFF
            s1 ^= tbl[base + (c & 0xFF)]
            s2 = (s2 + c + ((s2 << 5) & 0xFFFFFFFF) + s1 + 3) & 0xFFFFFFFF
            c >>= 8
        s1 = (s1 + s2) & 0xFFFFFFFF
        s1 ^= tbl[base + (c & 0xFF)]
        s2 = (s2 + c + ((s2 << 5) & 0xFFFFFFFF) + s1 + 3) & 0xFFFFFFFF
    return s1


# --------------------------------------------------------------------------- #
# archive objects
# --------------------------------------------------------------------------- #

class Entry:
    __slots__ = ("index", "name_hash", "mtime", "name_hash2", "mtime2",
                 "size", "start_page", "name")

    def __init__(self, index, raw: bytes):
        (self.name_hash, self.mtime, self.name_hash2, self.mtime2,
         self.size, self.start_page) = struct.unpack_from("<6I", raw, 0)
        self.index = index
        self.name = None

    def __repr__(self):
        return (f"<Entry {self.index} hash={self.name_hash:#010x} "
                f"size={self.size} page={self.start_page} name={self.name!r}>")


class GpakArchive:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            self.data = fh.read()
        self.pages_total = len(self.data) // PAGE
        d = self.data
        (self.magic, self.header_size, self.version, self.reserved0,
         self.reserved1, self.entry_count, self.write_time, self.capacity,
         self.dir_pages, self.reserved2, self.page_count, self.free_pages,
         self.first_free_page) = struct.unpack_from("<13I", d, 0)
        if self.magic != CONTAINER_MAGIC:
            raise ValueError(f"{path}: not a GBox2 pak (magic {self.magic:#x})")
        self.version = self.version & 0xFFFF
        self.entries: list[Entry] = []
        for i in range(self.entry_count):
            off = ENTRY_DATA + i * ENTRY_SIZE
            self.entries.append(Entry(i, d[off:off + ENTRY_SIZE]))

    # -- pages ------------------------------------------------------------- #

    def page_header(self, index: int) -> dict | None:
        off = index * PAGE
        if off + PAGE > len(self.data):
            return None
        (magic, prev, nxt, r0, chunk, seed, r1, length) = \
            struct.unpack_from("<8I", self.data, off)
        if magic != PAGE_MAGIC or length > MAX_PAYLOAD:
            return None
        return dict(index=index, prev=prev, next=nxt, chunk=chunk, seed=seed,
                    length=length, offset=off)

    def page_payload(self, index: int) -> bytes:
        h = self.page_header(index)
        if h is None:
            raise ValueError(f"page {index} has no valid header")
        raw = self.data[h["offset"] + PAGE_HDR:h["offset"] + PAGE_HDR + h["length"]]
        return decode_payload(h["seed"], raw)

    def page_crc_ok(self, index: int) -> bool:
        h = self.page_header(index)
        if h is None:
            return False
        stored = struct.unpack_from("<I", self.data, h["offset"] + 0xFFC)[0]
        return gbox_crc(self.page_payload(index)) == stored

    def read_chain(self, start: int) -> tuple[bytes, list[int]]:
        """Follow the prev/next chain from `start` and concatenate payloads."""
        out, order, cur, guard = [], [], start, set()
        while cur and cur not in guard:
            h = self.page_header(cur)
            if h is None:
                break
            guard.add(cur)
            order.append(cur)
            out.append(self.page_payload(cur))
            cur = h["next"]
        return b"".join(out), order

    # -- entries ----------------------------------------------------------- #

    def read_entry(self, entry: Entry) -> bytes:
        blob, _ = self.read_chain(entry.start_page)
        return blob[:entry.size]

    def find(self, name: str) -> Entry | None:
        want = hash_name(name, 0)
        lo, hi = 0, len(self.entries) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            h = self.entries[mid].name_hash
            if h == want:
                return self.entries[mid]
            if h < want:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def verify(self) -> tuple[int, int]:
        ok = bad = 0
        for e in self.entries:
            blob, _ = self.read_chain(e.start_page)
            if len(blob) >= e.size:
                ok += 1
            else:
                bad += 1
        return ok, bad


# --------------------------------------------------------------------------- #
# per-file compression wrapper
# --------------------------------------------------------------------------- #
#
# Some cached payloads are prefixed with a 4-byte compression tag that the
# client writes itself (`mov dword ptr [eax], 0x602376d7` at 0x53972F) and
# dispatches on at 0x539979.  See GPAK_FORMAT.md §2.7.

WRAP_ZLIB = 0x602376D7     # minizip/zlib stream follows
WRAP_CHECKED = 0x8A8E9468  # stored, with an additive integrity fold
WRAP_STORED = 0xBE119407   # stored verbatim


def wrap_tag(blob: bytes) -> int | None:
    """The compression tag at the head of `blob`, or None if it is a raw file."""
    if len(blob) < 5:
        return None
    v = struct.unpack_from("<I", blob, 0)[0]
    return v if v in (WRAP_ZLIB, WRAP_CHECKED, WRAP_STORED) else None


def unwrap(blob: bytes) -> bytes:
    """Strip the compression wrapper, inflating it if the tag says so.

    Untagged payloads are returned unchanged, so this is safe to call on every
    extracted entry.  `WRAP_ZLIB` bodies are *complete single-entry ZIP
    archives* (entry name `varbin`), not bare deflate streams - the client
    opens them with minizip from memory.  `WRAP_CHECKED` is treated as stored:
    its integrity fold is a debug aid, not compression.
    """
    tag = wrap_tag(blob)
    if tag is None:
        return blob
    body = blob[4:]
    if tag == WRAP_ZLIB:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            names = zf.namelist()
            if len(names) != 1:
                raise ValueError(f"expected 1 ZIP entry, found {len(names)}")
            return zf.read(names[0])
    return body


# --------------------------------------------------------------------------- #
# type sniffing
# --------------------------------------------------------------------------- #

MAGICS = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"PK\x03\x04", "zip"),
    (b"RIFF", "riff"),
    (b"OggS", "ogg"),
    (b"BM", "bmp"),
    (b"DDS ", "dds"),
    (b"ID3", "mp3"),
    (b"53WF", "53wf"),
    (b"CMZ\x00", "cmz"),
]


def sniff(blob: bytes) -> str:
    for magic, ext in MAGICS:
        if blob.startswith(magic):
            return ext
    head = blob[:4096]
    text = sum(1 for b in head if 9 <= b <= 13 or 32 <= b < 127)
    if head and text / len(head) > 0.85:
        return "txt"
    return "bin"
