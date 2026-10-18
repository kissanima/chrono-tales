#!/usr/bin/env python3
"""Dump raw .cmz `sbus` skin blobs for manual inspection.

Segments the MESH chunk on the byte-reversed `sbus` tags (the file layout is
variable-length, so the 136-byte in-memory stride cannot be used to walk it),
prints the 32-byte header as u32s, and hexdumps the payload.
"""
import json
import pathlib
import struct
import sys

TAGS = {b"HS"[::-1]}  # placeholder, unused


def gbk(b):
    return b.split(b"\x00")[0].decode("gbk", "replace")


def chunk_bounds(d):
    """[(off, tag, end)] for the four real chunk tags, in file order."""
    hits = []
    for i in range(8, len(d) - 4):
        s = d[i:i + 4][::-1]
        if s in (b"skel", b"MESH", b"MAP1", b"END."):
            if not hits or i >= hits[-1][0] + 4:
                hits.append((i, s.decode("latin1")))
    return [(o, t, hits[k + 1][0] if k + 1 < len(hits) else len(d))
            for k, (o, t) in enumerate(hits)]


def blobs(d, start, end):
    pos, i = [], start
    while True:
        j = d.find(b"sbus", i, end)
        if j < 0:
            break
        pos.append(j)
        i = j + 1
    out = []
    for k, s in enumerate(pos):
        e = pos[k + 1] if k + 1 < len(pos) else end
        out.append((s, d[s:e]))
    return out


def hexdump(b, base=0, width=16):
    for i in range(0, len(b), width):
        seg = b[i:i + width]
        h = " ".join(f"{c:02x}" for c in seg)
        a = "".join(chr(c) if 32 <= c < 127 else "." for c in seg)
        yield f"{base+i:06x}  {h:<{width*3}}  {a}"


def main():
    files = sys.argv[1:] or sorted(
        str(p) for p in pathlib.Path("extracted/netpak").rglob("*.cmz"))
    summary = []
    for fn in files:
        p = pathlib.Path(fn)
        d = p.read_bytes()
        bounds = chunk_bounds(d)
        mesh = next((b for b in bounds if b[1] == "MESH"), None)
        print("=" * 78)
        print(f"{p.stem}  size={len(d)}")
        if not mesh:
            print("  no MESH")
            continue
        off, _, end = mesh
        n_parts = struct.unpack_from("<I", d, off + 4)[0]
        print(f"  MESH {off:x}..{end:x}  n_parts={n_parts}")
        for k, (s, blob) in enumerate(blobs(d, off + 8, end)):
            if len(blob) < 32:
                continue
            hdr = struct.unpack_from("<7I", blob, 4)
            pay = blob[32:]
            print(f"\n  -- sbus[{k}] @{s:#x} len={len(blob)} payload={len(pay)}")
            print(f"     hdr u32 = {list(hdr)}")
            print(f"     part_index={hdr[0]} A={hdr[2]} B={hdr[3]} bonecap={hdr[6]}")
            for line in list(hexdump(pay[:256])):
                print("     " + line)
            if len(pay) > 256:
                print(f"     ... ({len(pay) - 256} more bytes)")
            summary.append(dict(file=p.stem, k=k, total=len(blob), pay=len(pay),
                                hdr=list(hdr)))
    if summary:
        pathlib.Path("extracted/models/cmz/_sbus_summary.json").write_text(
            json.dumps(summary, indent=1), encoding="utf-8")
        print(f"\nwrote {len(summary)} blob summaries -> "
              "extracted/models/cmz/_sbus_summary.json")


if __name__ == "__main__":
    main()
