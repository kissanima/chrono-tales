#!/usr/bin/env python3
"""varbin_write.py - encode the tagged tree back to varbin bytes.

`varfile.load_varbin` is a *lossy* view of the encoding: several tags decode to
the same Python type (0x12 and 0x20 both give an int, 0x40 and 0x41 both give a
list, 0x30 and 0x31 both give a str).  So this is not a serialiser for the
decoded tree -- it implements the **client's own dispatch rules**, recovered by
correlating every (tag, value) pair across all 48 shipped payloads:

    value                tag    body
    ------------------   ----   ------------------------------------------
    None                 0xFF   --
    int  0..15           0x0n   --                (tag *is* the value)
    int  16..255         0x10   u8
    int  256..65535      0x11   u16
    int  else            0x12   u32
    float                0x13   u32 (IEEE-754 bits)
    3-tuple              0x16   3 x u32
    ""                   0x30   --                (no length field at all)
    str                  0x31   u32 chars + UTF-16LE
    bytes                0x32   u32 len + raw
    list len < 255       0x40   u8 count + items
    list len >= 255      0x41   u32 count + items

The boundaries are measured, not assumed: across 48 payloads the u8 array tag
tops out at len 66 and the u32 tag starts at exactly len 255, the u8 integer
tag never carries a value below 16, and no string uses 0x31 for a zero length.

Two things this cannot reproduce, both absent from every shipped payload:
- negative integers.  Nothing decodes below 0, so the client's sign convention
  is unobserved; they are written as 0x12 with two's complement, which is what
  the reader's `u32()` would undo.
- the 64-bit tags (0x14/0x15/0x21) and the alternate string/blob forms
  (0x50/0x51/0x33).  A value that needs them is rejected rather than guessed.

Round-trip is the test: `python tools/varbin_write.py check` re-encodes all 48
payloads and asserts byte equality with the archive.  `check_all.py` runs it as
the `varbin-writer` invariant.
"""
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import varfile  # noqa: E402

U32 = 0xFFFFFFFF


class WriteError(Exception):
    pass


def encode(value, out=None):
    """Encode one value.  `out` is a bytearray to append to (reused for speed)."""
    if out is None:
        out = bytearray()
    _value(value, out)
    return bytes(out)


def _value(v, out):
    if v is None:
        out.append(0xFF)
        return
    if isinstance(v, bool):
        # bools never occur in the shipped payloads; the client has no tag for
        # them, so they are integers as far as the format is concerned.
        _int(int(v), out)
        return
    if isinstance(v, int):
        _int(v, out)
        return
    if isinstance(v, float):
        out.append(0x13)
        out += struct.pack("<I", struct.unpack("<I", struct.pack("<f", v))[0])
        return
    if isinstance(v, str):
        _str(v, out)
        return
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        out.append(0x32)
        out += struct.pack("<I", len(b))
        out += b
        return
    if isinstance(v, tuple):
        if len(v) != 3 or not all(isinstance(x, int) for x in v):
            raise WriteError(f"only 3-int tuples are encodable, got {v!r}")
        out.append(0x16)
        for x in v:
            out += struct.pack("<I", x & U32)
        return
    if isinstance(v, list):
        n = len(v)
        if n < 255:
            out.append(0x40)
            out.append(n)
        else:
            out.append(0x41)
            out += struct.pack("<I", n)
        for x in v:
            _value(x, out)
        return
    raise WriteError(f"cannot encode {type(v).__name__}: {v!r}")


def _int(v, out):
    if 0 <= v <= 15:
        out.append(v)
    elif 0 <= v <= 0xFF:
        out.append(0x10)
        out.append(v)
    elif 0 <= v <= 0xFFFF:
        out.append(0x11)
        out += struct.pack("<H", v)
    elif -0x80000000 <= v <= U32:
        out.append(0x12)
        out += struct.pack("<I", v & U32)
    else:
        raise WriteError(
            f"integer {v} needs a 64-bit tag (0x14/0x15/0x21), which no shipped "
            f"varbin uses; refusing to guess")


def _str(s, out):
    if not s:
        out.append(0x30)
        return
    try:
        body = s.encode("utf-16-le")
    except UnicodeEncodeError as e:
        raise WriteError(f"string not encodable as UTF-16LE: {e}") from e
    out.append(0x31)
    out += struct.pack("<I", len(body) // 2)
    out += body


# --- self-check ------------------------------------------------------------

def _payload_files(root):
    from gpak import unwrap
    seen = set()
    for f in sorted((root / "extracted" / "netpak").rglob("*")):
        if not f.is_file() or f.suffix.lower() in (
                ".csv", ".json", ".png", ".jpg", ".jpeg", ".bmp", ".tga", ".dds"):
            continue
        try:
            head = f.read_bytes()[:4]
        except OSError:
            continue
        if head != b"\xd7\x76\x23\x60":
            continue
        key = str(f.resolve())
        if key in seen:
            continue
        seen.add(key)
        yield f, unwrap(f.read_bytes())


def cmd_check(root=None):
    """Re-encode every payload and compare with the bytes in the archive."""
    root = pathlib.Path(root or HERE.parent)
    ok = bad = 0
    for f, raw in _payload_files(root):
        v = varfile.load_varbin(raw)["value"]
        try:
            mine = encode(v)
        except WriteError as e:
            print(f"FAIL {f.name}: {e}")
            bad += 1
            continue
        if mine == raw:
            ok += 1
        else:
            bad += 1
            first = next((i for i in range(min(len(mine), len(raw)))
                          if mine[i] != raw[i]), min(len(mine), len(raw)))
            print(f"FAIL {f.name}: {len(mine)} vs {len(raw)} bytes, "
                  f"first diff at 0x{first:x} "
                  f"(mine {mine[first:first+8].hex()} "
                  f"archive {raw[first:first+8].hex()})")
    print(f"{ok}/{ok + bad} payloads re-encode byte-exactly")
    return 0 if bad == 0 else 1


def main(argv):
    if len(argv) > 1 and argv[1] == "check":
        return cmd_check(argv[2] if len(argv) > 2 else None)
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
