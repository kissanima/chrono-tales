#!/usr/bin/env python3
"""zmap_write.py - write a .zmap back out, and edit one.

Container: 4-byte tag `0x602376D7` + a ZIP holding exactly one entry named
`varbin`, whose bytes are a plain varbin (see `varbin_write.py`).

What round-trips exactly, and what cannot
-----------------------------------------
The **payload** re-encodes byte-for-byte: `varbin_write.encode` reproduces all
48 archive payloads, and the six `.zmap` payloads are among them.  The height
record additionally satisfies every constraint the client's loader enforces,
because it is regenerated from the same rules the loader reads it with:

    record length exactly 0x8212 (12 + 2*16,641 + 4)   loader 0x4ee4f9
    u32 tag 0xFAB00002, f32 minz/maxz at +4/+8
    two 8-bit planes: high byte then low byte
    u32 trailer = rolling hash h = (h << 1) ^ h16[i]

The **compressed stream** does not.  The shipped files were deflated by a 2013
zlib; no level or strategy available in zlib 1.3.1 reproduces those bytes, and
the sizes differ in both directions (e.g. 31,124 vs 31,414 on intro517493), so
it is a different compressor build rather than a different setting.  So a
rewritten `.zmap` is not byte-identical to the original as a file, and this
tool does not pretend otherwise: it asserts payload identity, CRC identity and
decode identity instead.

Everything except the deflate stream is preserved from a template: entry name,
DOS timestamp, the `55 54 05 00 07` extra field, external attributes and the
version fields.  Those are packer artefacts, not format, so they are copied
rather than regenerated.

Usage
-----
    python tools/zmap_write.py check            # all six: payload + decode identity
    python tools/zmap_write.py demo             # edit a copy, prove the edit took
    python tools/zmap_write.py edit IN OUT --raise-cell X,Y,UNITS
"""
import argparse
import io
import math
import pathlib
import struct
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import varbin_write  # noqa: E402
import varfile  # noqa: E402
import zmap  # noqa: E402

TAG = 0x602376D7
HEIGHT_TAG = 0xFAB00002
HEIGHT_N = 129
HEIGHT_CELLS = HEIGHT_N * HEIGHT_N          # 16,641
HEIGHT_LEN = 12 + 2 * HEIGHT_CELLS + 4      # 0x8212 = 33,298

# index of the height record inside the top-level 6-field map document
IDX_HEIGHT = 1


# --- height record ---------------------------------------------------------

def rolling_hash(grid):
    h = 0
    for v in grid:
        h = ((h << 1) ^ v) & 0xFFFFFFFF
    return h


def encode_height(grid, minz=-100.0, maxz=900.0, tag=HEIGHT_TAG):
    """Rebuild the 33,298-byte MAP_HEIGHTDATA record from a u16 grid."""
    if len(grid) != HEIGHT_CELLS:
        raise ValueError(f"height grid must be {HEIGHT_CELLS} cells, "
                         f"got {len(grid)}")
    hi = bytearray(HEIGHT_CELLS)
    lo = bytearray(HEIGHT_CELLS)
    for i, v in enumerate(grid):
        if not 0 <= v <= 0xFFFF:
            raise ValueError(f"height {v} out of range at cell {i}")
        hi[i] = (v >> 8) & 0xFF
        lo[i] = v & 0xFF
    return (struct.pack("<Iff", tag, minz, maxz) + bytes(hi) + bytes(lo)
            + struct.pack("<I", rolling_hash(grid)))


def decode_height(blob):
    """Inverse of `encode_height`, for verification."""
    if len(blob) != HEIGHT_LEN:
        raise ValueError(f"height record is {len(blob)} bytes, "
                         f"expected {HEIGHT_LEN}")
    tag, minz, maxz = struct.unpack_from("<Iff", blob, 0)
    hi = blob[12:12 + HEIGHT_CELLS]
    lo = blob[12 + HEIGHT_CELLS:12 + 2 * HEIGHT_CELLS]
    grid = [(hi[i] << 8) | lo[i] for i in range(HEIGHT_CELLS)]
    trailer = struct.unpack_from("<I", blob, HEIGHT_LEN - 4)[0]
    return dict(tag=tag, minz=minz, maxz=maxz, grid=grid, trailer=trailer,
                trailer_ok=trailer == rolling_hash(grid))


# --- container -------------------------------------------------------------

def unwrap(data):
    """-> (payload bytes, ZipInfo of the original entry, raw extra bytes)."""
    if struct.unpack_from("<I", data, 0)[0] != TAG:
        raise ValueError("not a .zmap: bad tag")
    z = zipfile.ZipFile(io.BytesIO(data[4:]))
    names = z.namelist()
    if names != ["varbin"]:
        raise ValueError(f"unexpected zip contents {names}")
    return z.read("varbin"), z.infolist()[0], z


def wrap(payload, template=None):
    """Build tag + zip.  `template` is the original ZipInfo to inherit from."""
    zi = zipfile.ZipInfo("varbin",
                         date_time=template.date_time if template
                         else (2011, 12, 21, 14, 34, 28))
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = template.external_attr if template else 0x80000000
    zi.create_version = template.create_version if template else 23
    zi.extract_version = template.extract_version if template else 20
    zi.extra = template.extra if template else b""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zi, payload, compress_type=zipfile.ZIP_DEFLATED,
                   compresslevel=9)
    return struct.pack("<I", TAG) + buf.getvalue()


def write_map(path_in, path_out, doc, template=None, compress=True):
    """Encode a decoded document (the inner 6-field list) to a .zmap."""
    payload = varbin_write.encode([doc])
    data = wrap(payload, template)
    pathlib.Path(path_out).write_bytes(data)
    return payload, data


def load_doc(path):
    """-> (payload, decoded inner list, ZipInfo)."""
    data = pathlib.Path(path).read_bytes()
    payload, zi, _ = unwrap(data)
    v = varfile.load_varbin(payload)["value"]
    if not (isinstance(v, list) and len(v) == 1):
        raise ValueError("unexpected varbin shape")
    return payload, v[0], zi


# --- commands --------------------------------------------------------------

def cmd_check(root=None):
    root = pathlib.Path(root or HERE.parent)
    maps = sorted((root / "extracted" / "netpak" / "intro").glob("*.zmap"))
    if not maps:
        print("no .zmap files found")
        return 1
    bad = 0
    for p in maps:
        payload, doc, zi = load_doc(p)
        # 1. payload identity: re-encoding the decoded tree must reproduce it
        mine = varbin_write.encode([doc])
        if mine != payload:
            print(f"FAIL {p.name}: payload re-encode differs "
                  f"({len(mine)} vs {len(payload)})")
            bad += 1
            continue
        # 2. the height record still satisfies the loader's constraints
        hb = doc[IDX_HEIGHT]
        h = decode_height(hb)
        if not h["trailer_ok"]:
            print(f"FAIL {p.name}: height trailer mismatch")
            bad += 1
            continue
        # 3. container: rebuild it and check the decode survives the trip
        data = wrap(mine, zi)
        back_payload, back_zi, _ = unwrap(data)
        if back_payload != payload:
            print(f"FAIL {p.name}: payload changed through the container")
            bad += 1
            continue
        if back_zi.CRC != zi.CRC:
            print(f"FAIL {p.name}: CRC changed "
                  f"{back_zi.CRC:#010x} vs {zi.CRC:#010x}")
            bad += 1
            continue
        # 4. the map still parses through the same reader the viewer uses
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".zmap", delete=False) as tf:
            tf.write(data)
            tmp = tf.name
        try:
            a = zmap.parse(tmp)
            b = zmap.parse(p)
        finally:
            pathlib.Path(tmp).unlink(missing_ok=True)
        if (a["world_name"], len(a["objects"]), len(a["scenery"])) != (
                b["world_name"], len(b["objects"]), len(b["scenery"])):
            print(f"FAIL {p.name}: reparse differs")
            bad += 1
            continue
        print(f"  {p.name:22s} payload {len(payload):>7,} B exact, "
              f"crc {zi.CRC:#010x} same, height {len(hb):,} B "
              f"(z {h['minz']:g}..{h['maxz']:g}), "
              f"deflate {zi.compress_size:,} -> {back_zi.compress_size:,}, "
              f"{len(a['objects'])} objects / {len(a['scenery'])} scenery")
    print(f"\n{len(maps) - bad}/{len(maps)} maps round-trip (payload + decode)")
    return 0 if bad == 0 else 1


def cmd_demo(out=None):
    """Edit a copy of one map and prove exactly the intended change took."""
    root = HERE.parent
    src = root / "extracted" / "netpak" / "intro" / "intro517493.zmap"
    payload, doc, zi = load_doc(src)
    before = decode_height(doc[IDX_HEIGHT])

    # pick a cell that is not on the border, raise it by 500 h16 units (~7.6 m)
    x, y = 64, 64
    i = y * HEIGHT_N + x
    grid = list(before["grid"])
    old = grid[i]
    grid[i] = min(0xFFFF, old + 500)
    doc[IDX_HEIGHT] = encode_height(grid, before["minz"], before["maxz"])

    out = pathlib.Path(out or (root / "work" / "edited.zmap"))
    out.parent.mkdir(parents=True, exist_ok=True)
    new_payload, _ = write_map(src, out, doc, template=zi)

    # read it back through the same path the viewer uses
    payload2, doc2, zi2 = load_doc(out)
    after = decode_height(doc2[IDX_HEIGHT])
    changed = [i2 for i2 in range(HEIGHT_CELLS)
               if before["grid"][i2] != after["grid"][i2]]
    print(f"wrote {out} ({len(payload2):,} B payload, {out.stat().st_size:,} B file)")
    print(f"  cell ({x},{y}) {old} -> {grid[i]} "
          f"(+{grid[i] - old} h16 = {(grid[i] - old) * (after['maxz'] - after['minz']) / 65530:.2f} m)")
    print(f"  cells changed on re-read: {len(changed)} "
          f"({'exactly the one intended' if changed == [i] else 'UNEXPECTED: ' + str(changed[:8])})")
    print(f"  trailer recomputed and valid: {after['trailer_ok']}")
    print(f"  scene still parses: world={doc2[3]}, objects={len(doc2[5])}, "
          f"ground layers={len(doc2[4])}")
    return 0 if (changed == [i] and after["trailer_ok"]) else 1


def cmd_edit(args):
    payload, doc, zi = load_doc(args.input)
    h = decode_height(doc[IDX_HEIGHT])
    x, y, units = (int(t) for t in args.raise_cell.split(","))
    grid = list(h["grid"])
    m_per_unit = (h["maxz"] - h["minz"]) / 65530.0
    r = max(0, args.radius)
    touched = 0
    peak = 0
    for cy in range(y - r, y + r + 1):
        for cx in range(x - r, x + r + 1):
            if not (0 <= cx < HEIGHT_N and 0 <= cy < HEIGHT_N):
                continue
            d = math.hypot(cx - x, cy - y)
            if d > r:
                continue
            # cosine falloff: full at the centre, zero at the rim, so the edit
            # is a smooth hill rather than a one-cell spike
            f = 1.0 if r == 0 else 0.5 * (1.0 + math.cos(math.pi * d / r))
            du = int(round(units * f))
            if du == 0 and d > 0:
                continue
            i = cy * HEIGHT_N + cx
            before = grid[i]
            grid[i] = max(0, min(0xFFFF, before + du))
            if grid[i] != before:
                touched += 1
                peak = max(peak, abs(grid[i] - before))
    doc[IDX_HEIGHT] = encode_height(grid, h["minz"], h["maxz"])
    payload2, _ = write_map(args.input, args.output, doc, template=zi)
    print(f"{args.input} -> {args.output}")
    print(f"  centre ({x},{y}) +{units} h16, radius {r} cells "
          f"({r * 2} m): {touched} cells changed, peak {peak} h16 "
          f"= {peak * m_per_unit:.2f} m")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="verify payload + decode round-trip")
    c.add_argument("root", nargs="?")
    sub.add_parser("demo", help="edit a copy and prove the edit took")
    e = sub.add_parser("edit", help="raise one terrain cell")
    e.add_argument("input")
    e.add_argument("output")
    e.add_argument("--raise-cell", required=True, metavar="X,Y,UNITS")
    e.add_argument("--radius", type=int, default=0,
                   help="cells of cosine falloff around the centre (default 0)")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        return cmd_check(a.root)
    if a.cmd == "demo":
        return cmd_demo()
    if a.cmd == "edit":
        return cmd_edit(a)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
