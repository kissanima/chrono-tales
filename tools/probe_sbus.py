#!/usr/bin/env python3
"""Probe the .cmz MESH chunk to locate and decode `sbus` skin records."""
import struct, binascii, sys, pathlib

ROOT = pathlib.Path("extracted/netpak/unknown")

def gbk(b):
    return b.split(b"\x00")[0].decode("gbk", "replace")

def find_chunks(d):
    out = []
    for i in range(8, len(d) - 4):
        s = d[i:i + 4][::-1]
        if s in (b"skel", b"MESH", b"MAP1", b"END."):
            out.append((i, s.decode("latin1")))
    return out

def dump_hex(b, off, n=160, width=32):
    seg = b[off:off + n]
    lines = []
    for i in range(0, len(seg), width):
        chunk = seg[i:i + width]
        hexs = " ".join(f"{c:02x}" for c in chunk)
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        lines.append(f"{off + i:06x}  {hexs:<{width*3}}  {asc}")
    return "\n".join(lines)

def probe(path):
    d = path.read_bytes()
    print("=" * 70)
    print(f"FILE {path.name}  size={len(d)} ver={struct.unpack_from('<I',d,4)[0]}")
    chunks = find_chunks(d)
    print("chunks:", " ".join(f"{t}@{o}" for o, t in chunks))
    # locate MESH
    mi = next((o for o, t in chunks if t == "MESH"), None)
    if mi is None:
        print("no MESH"); return
    p = mi + 4
    n = struct.unpack_from("<I", d, p)[0]; p += 4
    print(f"n_parts = {n}")
    parts = []
    for pi in range(n):
        ln = struct.unpack_from("<I", d, p)[0]; p += 4
        name = gbk(d[p:p + ln]); p += ln
        m = struct.unpack_from("<I", d, p)[0]; p += 4
        parts.append((name, m, p, p + m * 136))
        p += m * 136
    for pi, (name, m, s, e) in enumerate(parts):
        print(f"\n--- part[{pi}] name={name!r} records={m} region={s:x}..{e:x} (len {e-s})")
        # search for sbus tag within the region
        tag = b"sbus"
        idx = s
        positions = []
        while True:
            j = d.find(tag, idx, e)
            if j < 0:
                break
            positions.append(j)
            idx = j + 1
        print(f"  sbus tag occurrences in region: {len(positions)} -> {[hex(x) for x in positions[:10]]}")
        # byte length per record guess
        if m:
            print(f"  bytes/record if fixed = {(e-s)/m:.2f}")
        print(dump_hex(d, s, min(e - s, 192)))

    # also search whole file for sbus
    print("\nglobal sbus positions:", [hex(x) for x in
          [j for j in range(len(d)) if d[j:j+4]==b'sbus']])

if __name__ == "__main__":
    cand = sys.argv[1:] or [str(p) for p in ROOT.glob("*.cmz")]
    for c in cand:
        probe(pathlib.Path(c))
