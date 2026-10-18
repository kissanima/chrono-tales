"""GBox2 .gpak writer.

Companion to gpak.py.  Two modes:

  rebuild(arch)      reproduce an existing archive exactly, reusing its own page
                     placement, per-page seeds and directory padding.  Byte-identical
                     apart from regions the original never initialised (see below).
  build_fresh(...)   pack a set of (name, bytes) from scratch: directory pages first,
                     then data pages allocated sequentially, then a zeroed free list.

Known-uninitialised regions in shipped archives (cannot be reproduced, by design):
  * directory record bytes 0x18..0x217 -- stale heap, only a 0xFA21 marker is written
  * the gap in the last directory page after the entry array -- stale heap
"""

from __future__ import annotations

import os
import struct

from gpak import (CONTAINER_MAGIC, ENTRY_DATA, ENTRY_SIZE, HDR_SIZE, LFSR_MASK,
                  MAX_PAYLOAD, PAGE, PAGE_HDR, PAGE_MAGIC, GpakArchive, gbox_crc,
                  hash_name)

RECORD_TAIL = 0x18          # start of the never-initialised part of a directory record
MARKER = 0xFA21


def encode_payload(seed: int, src: bytes) -> bytes:
    """Apply the per-page LFSR-XOR stream (self-inverse, same as decode)."""
    out = bytearray(len(src))
    k = seed & 0xFFFFFFFF
    for i in range(len(src)):
        k = (((k << 1) & 0xFFFFFFFF) & LFSR_MASK) + 1
        out[i] = src[i] ^ (k & 0xFF)
    return bytes(out)


def page_bytes(prev: int, nxt: int, chunk: int, seed: int, payload: bytes,
               reserved: int = 0) -> bytes:
    """One allocated data page: header + ciphertext + zero pad + checksum."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload {len(payload)} > {MAX_PAYLOAD}")
    buf = bytearray(PAGE)
    struct.pack_into("<8I", buf, 0, PAGE_MAGIC, prev, nxt, 0, chunk, seed,
                     reserved, len(payload))
    buf[PAGE_HDR:PAGE_HDR + len(payload)] = encode_payload(seed, payload)
    struct.pack_into("<I", buf, 0xFFC, gbox_crc(payload))
    return bytes(buf)


def free_page_bytes(prev: int, nxt: int) -> bytes:
    """A free page: magic 0, prev/next carry the free list, everything else zero."""
    buf = bytearray(PAGE)
    struct.pack_into("<III", buf, 0, 0, prev, nxt)
    return bytes(buf)


def dir_pages_for(entry_count: int) -> int:
    need = HDR_SIZE + entry_count * ENTRY_SIZE
    return (need + PAGE - 1) // PAGE


def capacity_for(dir_pages: int) -> int:
    return (dir_pages * PAGE - HDR_SIZE) // ENTRY_SIZE


class GpakWriter:
    """Collects entries then emits the container."""

    def __init__(self, version: int = 0x64, write_time: int = 0,
                 reserved1: int | None = None):
        self.version = version
        self.write_time = write_time
        self.reserved1 = reserved1
        self.items = []          # (name_hash, mtime, name_hash2, mtime2, data, seeds)

    def add(self, name: str, data: bytes, mtime: int | None = None,
            seeds: list[int] | None = None, name_hash: int | None = None,
            name_hash2: int | None = None, mtime2: int | None = None):
        h = name_hash if name_hash is not None else hash_name(name, 0)
        h2 = name_hash2 if name_hash2 is not None else hash_name(name, 1)
        self.items.append((h, self.write_time if mtime is None else mtime,
                           h2, self.write_time if mtime2 is None else mtime2,
                           data, seeds))
        return self

    # ------------------------------------------------------------------ #

    def build(self, padding: dict | None = None) -> bytes:
        """Sequential allocation: directory pages, then data pages, then free list."""
        n = len(self.items)
        dir_pages = dir_pages_for(n)
        entries = sorted(self.items, key=lambda t: t[0])

        chunks = []                       # (page_index, bytes) in allocation order
        placed = []                       # (entry fields..., start_page)
        page = dir_pages
        for (h, mtime, h2, mtime2, data, seeds) in entries:
            count = max(1, (len(data) + MAX_PAYLOAD - 1) // MAX_PAYLOAD)
            if seeds is None:
                seeds = [(0x9E3779B1 * (page + i + 1)) & 0xFFFFFFFF
                         for i in range(count)]
            if len(seeds) < count:
                raise ValueError("not enough seeds for page chain")
            start = page
            for i in range(count):
                prev = page - 1 if i else 0
                nxt = page + 1 if i < count - 1 else 0
                payload = data[i * MAX_PAYLOAD:(i + 1) * MAX_PAYLOAD]
                chunks.append((page, page_bytes(prev, nxt, i, seeds[i], payload)))
                page += 1
            placed.append((h, mtime, h2, mtime2, len(data), start))

        page_count = page
        # free list occupies the tail, doubly linked, prev/next only
        free_pages = (padding or {}).get("free_pages", 0)
        first_free = page
        for i in range(free_pages):
            idx = first_free + i
            prev = 0 if i == 0 else idx - 1
            nxt = 0 if i == free_pages - 1 else idx + 1
            chunks.append((idx, free_page_bytes(prev, nxt)))
        page_count += free_pages

        header = self._header(len(placed), dir_pages, page_count, free_pages,
                              first_free, 0)
        return self._assemble(header, placed, chunks, dir_pages, page_count,
                              padding)

    def _header(self, entry_count, dir_pages, page_count, free_pages,
                first_free, reserved2):
        buf = bytearray(HDR_SIZE)
        struct.pack_into(
            "<13I", buf, 0,
            CONTAINER_MAGIC, HDR_SIZE, self.version & 0xFFFF, 0,
            1000 + entry_count if self.reserved1 is None else self.reserved1,
            entry_count, self.write_time,
            capacity_for(dir_pages), dir_pages, reserved2,
            page_count, free_pages, first_free)
        return bytes(buf)

    def _assemble(self, header, placed, chunks, dir_pages, page_count, padding):
        padding = padding or {}
        out = bytearray(page_count * PAGE)
        out[0:HDR_SIZE] = header

        tails = padding.get("record_tails", {})
        for i, (h, mtime, h2, mtime2, size, start) in enumerate(placed):
            off = ENTRY_DATA + i * ENTRY_SIZE
            struct.pack_into("<6I", out, off, h, mtime, h2, mtime2, size, start)
            tail = tails.get(i)
            if tail is None:
                # fresh: marker then zeros
                struct.pack_into("<H", out, off + RECORD_TAIL, MARKER)
            else:
                out[off + RECORD_TAIL:off + ENTRY_SIZE] = tail

        dir_tail = padding.get("dir_tail")
        if dir_tail is not None:
            end = ENTRY_DATA + len(placed) * ENTRY_SIZE
            out[end:dir_pages * PAGE] = dir_tail

        for idx, blob in chunks:
            out[idx * PAGE:(idx + 1) * PAGE] = blob
        return bytes(out)


# --------------------------------------------------------------------------- #
# rebuild an existing archive
# --------------------------------------------------------------------------- #

def rebuild(arch: GpakArchive, preserve_padding: bool = True) -> bytes:
    """Re-emit `arch` from its own metadata.

    Page placement, per-page seeds and the directory's stale padding are all copied
    from the source, so the result should match byte for byte.
    """
    w = GpakWriter(version=arch.version, write_time=arch.write_time,
                   reserved1=arch.reserved1)

    # recover allocation order from the page map
    order = []
    for e in arch.entries:
        blob, pages = arch.read_chain(e.start_page)
        seeds = [arch.page_header(p)["seed"] for p in pages]
        order.append((e, pages, seeds))

    # reconstruct the exact page layout: index -> bytes
    layout = {}
    for e, pages, seeds in order:
        data = arch.read_entry(e)
        count = max(1, (len(data) + MAX_PAYLOAD - 1) // MAX_PAYLOAD)
        for i, p in enumerate(pages):
            h = arch.page_header(p)
            payload = data[i * MAX_PAYLOAD:(i + 1) * MAX_PAYLOAD]
            (resv,) = struct.unpack_from("<I", arch.data, p * PAGE + 0x18)
            layout[p] = page_bytes(h["prev"], h["next"], h["chunk"], h["seed"],
                                   payload, resv)

    # free pages
    cur = arch.first_free_page
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        blk = arch.data[cur * PAGE:(cur + 1) * PAGE]
        layout[cur] = blk
        cur = struct.unpack_from("<I", blk, 8)[0]

    padding = {}
    if preserve_padding:
        tails = {}
        for i, e in enumerate(sorted(arch.entries, key=lambda x: x.name_hash)):
            off = ENTRY_DATA + i * ENTRY_SIZE
            tails[i] = arch.data[off + RECORD_TAIL:off + ENTRY_SIZE]
        padding["record_tails"] = tails
        end = ENTRY_DATA + arch.entry_count * ENTRY_SIZE
        padding["dir_tail"] = arch.data[end:arch.dir_pages * PAGE]

    # rebuild header + directory, then drop in the preserved pages
    entries = sorted(arch.entries, key=lambda e: e.name_hash)
    header = bytearray(HDR_SIZE)
    struct.pack_into(
        "<13I", header, 0,
        CONTAINER_MAGIC, HDR_SIZE, arch.version & 0xFFFF, arch.reserved0,
        arch.reserved1, arch.entry_count, arch.write_time, arch.capacity,
        arch.dir_pages, arch.reserved2, arch.page_count, arch.free_pages,
        arch.first_free_page)

    out = bytearray(arch.page_count * PAGE)
    out[0:HDR_SIZE] = header
    for i, e in enumerate(entries):
        off = ENTRY_DATA + i * ENTRY_SIZE
        struct.pack_into("<6I", out, off, e.name_hash, e.mtime, e.name_hash2,
                         e.mtime2, e.size, e.start_page)
        if preserve_padding:
            out[off + RECORD_TAIL:off + ENTRY_SIZE] = padding["record_tails"][i]
        else:
            struct.pack_into("<H", out, off + RECORD_TAIL, MARKER)
    if preserve_padding:
        end = ENTRY_DATA + arch.entry_count * ENTRY_SIZE
        out[end:arch.dir_pages * PAGE] = padding["dir_tail"]
    for idx, blob in layout.items():
        out[idx * PAGE:(idx + 1) * PAGE] = blob
    return bytes(out)
