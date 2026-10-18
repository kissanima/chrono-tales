#!/usr/bin/env python3
"""Run the REAL varbin_tables.value() parser over each blob, locate first Fail,
dump hex + nearest strings, and also parse the declared header.
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import varbin_tables as VT  # noqa: E402

VB = os.path.join(ROOT, "work", "varbin")


def strings_near(d, pos, n=6, forward_only=False):
    strs = []
    i = 0
    while i < len(d):
        if d[i] == 0x31 and i + 5 <= len(d):
            (c,) = struct.unpack_from("<I", d, i + 1)
            e = i + 5 + c * 2
            if 0 < c <= 0x2000 and e <= len(d):
                txt = d[i + 5:e].decode("utf-16-le", "replace")
                if all(ch.isprintable() for ch in txt) and txt:
                    strs.append((i, txt))
                    i = e
                    continue
        i += 1
    before = [(s, t) for s, t in strs if s < pos][-n:]
    after = [(s, t) for s, t in strs if s >= pos][:n]
    return before, after


def main():
    tags = VT.load_tags()
    names = sys.argv[1:] or ["c41c9276", "e15a7343", "433267c3", "0580853e",
                             "66d2c4f3", "d07f4acc"]
    for name in names:
        d = open(os.path.join(VB, name + ".bin"), "rb").read()
        print(f"\n=== {name}  {len(d):,}B ===")
        # header guess: first bytes
        print(f"  head: {' '.join(f'{b:02x}' for b in d[:16])}")
        try:
            top, end = VT.value(d, 0, tags)
            print(f"  parses to {end:,} / {len(d):,}"
                  f" ({'EOF-EXACT' if end == len(d) else 'DESYNC'})")
            if end != len(d):
                lo = max(0, end - 24)
                print(f"  tail ctx: {d[lo:end + 32].hex()}")
                b, a = strings_near(d, end)
                print(f"  strings before: {[t[1][:30] for t in b]}")
                print(f"  strings after : {[t[1][:30] for t in a]}")
        except VT.Fail as e:
            # get the offset from the message if present, else binary-search-ish
            msg = str(e)
            print(f"  Fail: {msg}")
            # find offset by re-parsing with a wrapper that records position
            pos = find_fail_pos(d, tags)
            print(f"  fail offset ~{pos:x} ({pos:,})")
            lo = max(0, pos - 32)
            print(f"  ctx[{lo:x}..{pos + 24:x}]: {d[lo:pos + 24].hex()}")
            b, a = strings_near(d, pos)
            print(f"  strings before: {[t[1][:36] for t in b]}")
            print(f"  strings after : {[t[1][:36] for t in a]}")


def find_fail_pos(d, tags):
    """Re-run value() but capture the byte offset where Fail happened."""
    pos_box = []

    class T(dict):
        pass

    # monkeypatch struct.unpack_from is messy; instead use a recursive parser
    # that mirrors VT.value and raises with offset.
    import struct as st

    def parse(i, depth=0):
        if i >= len(d):
            raise IndexError(i)
        t = d[i]
        k = tags.get(t)
        if k is None:
            raise IndexError(i)
        if k == "str":
            (c,) = st.unpack_from("<I", d, i + 1)
            e = i + 5 + c * 2
            if c > 0x2000 or e > len(d):
                raise IndexError(i)
            return parse(e, depth + 1)
        if k == "g8":
            n = d[i + 1]
            j = i + 2
            for _ in range(n):
                j = parse(j, depth + 1)
            return j
        if k == "g32":
            (n,) = st.unpack_from("<I", d, i + 1)
            if n > 0x100000:
                raise IndexError(i)
            j = i + 5
            for _ in range(n):
                j = parse(j, depth + 1)
            return j
        if t == 0x12:
            return parse(i + 5, depth + 1)
        if t == 0x13:
            return parse(i + 5, depth + 1)
        if k == 8:
            return parse(i + 9, depth + 1)
        e = i + 1 + k
        if e > len(d):
            raise IndexError(i)
        return parse(e, depth + 1)

    try:
        parse(0)
    except IndexError as exc:
        return int(str(exc))
    return -1


if __name__ == "__main__":
    main()
