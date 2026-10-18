#!/usr/bin/env python3
"""Diagnose a varbin parse failure: show the tag, its position, the enclosing group
counts, and the surrounding bytes so the missing payload size can be inferred."""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")

ZERO = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
        0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x30, 0xFF}


def walk(d, i, stack, depth=0):
    if i >= len(d):
        raise RuntimeError(("eof", i, stack))
    t = d[i]
    if t == 0x31:
        (c,) = struct.unpack_from("<I", d, i + 1)
        return i + 5 + c * 2
    if t == 0x40:
        n = d[i + 1]
        j = i + 2
        for k in range(n):
            j = walk(d, j, stack + [(i, n, k)], depth + 1)
        return j
    if t == 0x41:
        (n,) = struct.unpack_from("<I", d, i + 1)
        j = i + 5
        for k in range(n):
            j = walk(d, j, stack + [(i, n, k)], depth + 1)
        return j
    if t in (0x12, 0x13):
        return i + 5
    if t in (0x10, 0x11):
        return i + 2
    if t in ZERO:
        return i + 1
    raise RuntimeError(("tag", i, t, stack))


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "87d142c2"
    d = open(os.path.join(VB, name + ".bin"), "rb").read()
    try:
        end = walk(d, 0, [])
        print(f"{name}: clean, {end}/{len(d)} bytes")
        return
    except RuntimeError as e:
        info = e.args[0]
    kind = info[0]
    if kind == "eof":
        print(f"{name}: hit EOF at {info[1]} of {len(d)}")
        return
    _, pos, tag, stack = info
    print(f"{name}: {len(d)} bytes")
    print(f"unknown tag 0x{tag:02X} at 0x{pos:X}")
    print("enclosing groups (offset, count, index):")
    for off, n, k in stack[-6:]:
        print(f"   0x{off:<8X} n={n:<8d} i={k}")
    lo = max(0, pos - 24)
    hi = min(len(d), pos + 48)
    print(f"\ncontext 0x{lo:X}..0x{hi:X}")
    print("  " + " ".join(f"{b:02x}" for b in d[lo:hi]))
    print("  " + " ".join(f"{b:02X}" if k == pos - lo else f"[{b:02X}]"
                          for k, b in enumerate(d[lo:hi])))
    # how far to the next string?  that bounds the payload
    for j in range(pos + 1, min(pos + 200, len(d))):
        if d[j] == 0x31:
            (c,) = struct.unpack_from("<I", d, j + 1)
            e = j + 5 + c * 2
            if 0 < c <= 0x2000 and e <= len(d):
                txt = d[j + 5:e].decode("utf-16-le", "ignore")
                if all(ch.isprintable() for ch in txt):
                    print(f"\nnext string @0x{j:X} (+{j - pos} bytes): {txt!r}")
                    print(f"  span: {d[pos:j].hex()}")
                    break


if __name__ == "__main__":
    main()
