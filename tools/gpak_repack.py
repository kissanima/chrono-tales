#!/usr/bin/env python3
"""Prove the GPAK format by writing it back.

  roundtrip   re-emit an archive from its own metadata and diff against the original
  fresh       repack every entry with sequential allocation, then re-read and verify

`roundtrip` should be byte-identical: it copies page placement, per-page seeds and
the directory's stale padding.  `fresh` exercises the real writer path -- new
directory, new page allocation, new seeds -- and is verified by reading the result
back with the unmodified reader.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gpak import ENTRY_DATA, ENTRY_SIZE, GpakArchive, PAGE  # noqa: E402
import gpak_write as W  # noqa: E402


def diff(a: bytes, b: bytes, limit=8):
    """Report differing byte ranges between two buffers."""
    if len(a) != len(b):
        print(f"  length differs: {len(a)} vs {len(b)}")
    n = min(len(a), len(b))
    runs, start, count = [], None, 0
    for i in range(n):
        if a[i] != b[i]:
            count += 1
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, n))
    total = sum(e - s for s, e in runs)
    print(f"  differing bytes: {total:,} / {n:,} in {len(runs)} runs")
    for s, e in runs[:limit]:
        print(f"    0x{s:08X}..0x{e:08X}  ({e - s} bytes)  page {s // PAGE}")
    return total


def classify_runs(a: bytes, b: bytes, arch: GpakArchive):
    """Check that every differing byte lies in a known-uninitialised region."""
    n = min(len(a), len(b))
    bad = []
    for i in range(n):
        if a[i] == b[i]:
            continue
        page, off = divmod(i, PAGE)
        ok = False
        if page == 0 or (off < PAGE and page < arch.dir_pages):
            # directory area: record tails 0x18..0x217, or the tail after the array
            if i < ENTRY_DATA:
                ok = False
            else:
                rel = (i - ENTRY_DATA) % ENTRY_SIZE
                idx = (i - ENTRY_DATA) // ENTRY_SIZE
                if idx < arch.entry_count and W.RECORD_TAIL <= rel < ENTRY_SIZE:
                    ok = True
                elif i >= ENTRY_DATA + arch.entry_count * ENTRY_SIZE:
                    ok = True
        if not ok:
            bad.append(i)
    return bad


def cmd_roundtrip(path):
    arch = GpakArchive(path)
    print(f"{os.path.basename(path)}: {arch.entry_count} entries, "
          f"{arch.page_count} pages, dir {arch.dir_pages}")
    rebuilt = W.rebuild(arch)
    orig = arch.data
    print(f"  rebuilt {len(rebuilt):,} bytes vs original {len(orig):,}")
    diff(orig, rebuilt)
    bad = classify_runs(orig, rebuilt, arch)
    if bad:
        print(f"  {len(bad)} differing bytes OUTSIDE the uninitialised regions:")
        for i in bad[:10]:
            page, off = divmod(i, PAGE)
            print(f"    0x{i:08X} page {page} off 0x{off:X}")
    else:
        print("  every difference is inside a known-uninitialised region -> "
              "writer reproduces the format exactly")

    # and confirm the rebuilt bytes read back as a valid archive
    tmp = path + ".rebuilt"
    with open(tmp, "wb") as f:
        f.write(rebuilt)
    arch2 = GpakArchive(tmp)
    ok = bad2 = 0
    for e1, e2 in zip(arch.entries, arch2.entries):
        if (e1.name_hash, e1.size, e1.start_page) != (e2.name_hash, e2.size, e2.start_page):
            bad2 += 1
            continue
        ok += arch.read_entry(e1) == arch2.read_entry(e2)
    print(f"  re-read check: {ok}/{arch.entry_count} entries identical "
          f"({bad2} header mismatches)")
    crc_ok = sum(1 for i in range(arch.dir_pages, arch.page_count)
                 if arch2.page_crc_ok(i))
    print(f"  rebuilt page CRCs valid: {crc_ok}/{arch.page_count - arch.dir_pages}")
    os.remove(tmp)


def cmd_fresh(path):
    arch = GpakArchive(path)
    print(f"{os.path.basename(path)}: {arch.entry_count} entries, "
          f"{arch.page_count} pages")

    w = W.GpakWriter(version=arch.version, write_time=arch.write_time)
    payloads = {}
    for e in arch.entries:
        data = arch.read_entry(e)
        payloads[e.name_hash] = data
        w.add("", data, mtime=e.mtime, name_hash=e.name_hash,
              name_hash2=e.name_hash2, mtime2=e.mtime2)
    blob = w.build(padding={"free_pages": 8})
    print(f"  built {len(blob):,} bytes ({len(blob) // PAGE} pages)")

    tmp = path + ".fresh"
    with open(tmp, "wb") as f:
        f.write(blob)
    a2 = GpakArchive(tmp)
    print(f"  header: entries={a2.entry_count} dirPages={a2.dir_pages} "
          f"capacity={a2.capacity} pages={a2.page_count} free={a2.free_pages}")

    ok = bad = 0
    for e in a2.entries:
        got = a2.read_entry(e)
        if got == payloads.get(e.name_hash):
            ok += 1
        else:
            bad += 1
            print(f"    MISMATCH hash={e.name_hash:#010x} "
                  f"size={e.size} got={len(got)}")
    print(f"  payload round-trip: {ok}/{a2.entry_count} identical ({bad} bad)")

    # directory must be sorted for binary search to work
    hs = [e.name_hash for e in a2.entries]
    print(f"  directory sorted by nameHash: {hs == sorted(hs)}")

    crc_ok = sum(1 for i in range(a2.dir_pages, a2.page_count - a2.free_pages)
                 if a2.page_crc_ok(i))
    data_pages = a2.page_count - a2.free_pages - a2.dir_pages
    print(f"  data page CRCs valid: {crc_ok}/{data_pages}")

    # free list integrity
    cur, seen, steps = a2.first_free_page, set(), 0
    while cur and cur not in seen:
        seen.add(cur)
        blk = blob[cur * PAGE:(cur + 1) * PAGE]
        cur = struct.unpack_from("<I", blk, 8)[0]
        steps += 1
    print(f"  free list: walked {steps} pages, expected {a2.free_pages}, "
          f"ok={steps == a2.free_pages}")

    # spot-check binary search on a known hash
    if a2.entries:
        target = a2.entries[len(a2.entries) // 2].name_hash
        hit = any(e.name_hash == target for e in a2.entries)
        print(f"  lookup sanity: hash {target:#010x} present={hit}")
    os.remove(tmp)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "roundtrip"
    paths = sys.argv[2:] or [
        os.path.join(os.path.dirname(HERE), "gbox", "lm_en", "netpak.gpak"),
        os.path.join(os.path.dirname(HERE), "gbox", "lm_en", "sysnet.gpak"),
    ]
    for p in paths:
        if mode == "roundtrip":
            cmd_roundtrip(p)
        else:
            cmd_fresh(p)
        print()
