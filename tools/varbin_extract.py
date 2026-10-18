#!/usr/bin/env python3
"""Extract netpak's `varbin` entity database.

Layout (recovered):
    * flat stream of `[u8 fieldId][payload]`
    * `0x31`  self-describing string: u32 charCount + charCount*2 bytes UTF-16LE
    * `0x10`  u8      `0x11` u8      `0x12` int32      `0x13` float32
    * `0x40`  u8      `0x41` u32
    Unknown field ids are resynchronised on the next string, so a parse never dies.

Records are property bags written as adjacent strings: a label that recurs across
records (分类 / 档次 / 职业 / 添加人 ...) is immediately followed by its value.  That
adjacency is enough to build a categorical cross-tab without knowing the numeric
schema, which lives in `tabedit/*.gbc`.

Usage:
    python varbin_extract.py tokens          # ordered token dump per blob
    python varbin_extract.py keys            # infer key -> value cross-tab
    python varbin_extract.py text            # flat searchable text per blob
"""
import glob
import json
import os
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VB = os.path.join(ROOT, "work", "varbin")
OUT = os.path.join(ROOT, "extracted", "netpak_varbin")

WSTR = 0x31
SIZES = {0x10: 1, 0x11: 1, 0x12: 4, 0x13: 4, 0x40: 1, 0x41: 4}
NAMES = {0x10: "u8", 0x11: "u8b", 0x12: "i32", 0x13: "f32",
         0x40: "u8c", 0x41: "u32"}


def blobs():
    return [(os.path.basename(p)[:-4], open(p, "rb").read())
            for p in sorted(glob.glob(os.path.join(VB, "*.bin")))]


def tokens(data):
    """Yield (offset, kind, value).  Resyncs to the next string on a bad id."""
    i, n = 0, len(data)
    while i < n:
        t = data[i]
        if t == WSTR and i + 5 <= n:
            (c,) = struct.unpack_from("<I", data, i + 1)
            end = i + 5 + c * 2
            if 0 < c <= 0x2000 and end <= n:
                txt = data[i + 5:end].decode("utf-16-le", "ignore")
                if all(ch.isprintable() for ch in txt):
                    yield i, "str", txt
                    i = end
                    continue
        s = SIZES.get(t)
        if s is not None and i + 1 + s <= n:
            raw = data[i + 1:i + 1 + s]
            if t == 0x13:
                (v,) = struct.unpack("<f", raw)
            elif t == 0x12:
                (v,) = struct.unpack("<i", raw)
            else:
                v = int.from_bytes(raw, "little")
            yield i, NAMES[t], v
            i += 1 + s
            continue
        # desync: skip to the next plausible string
        j = i + 1
        while j < n:
            if data[j] == WSTR and j + 5 <= n:
                (c,) = struct.unpack_from("<I", data, j + 1)
                end = j + 5 + c * 2
                if 0 < c <= 0x2000 and end <= n:
                    txt = data[j + 5:end].decode("utf-16-le", "ignore")
                    if all(ch.isprintable() for ch in txt):
                        break
            j += 1
        yield i, "skip", j - i
        i = j


def cmd_tokens():
    os.makedirs(OUT, exist_ok=True)
    for name, data in blobs():
        rows = [{"off": o, "k": k, "v": v} for o, k, v in tokens(data)]
        p = os.path.join(OUT, name + ".tokens.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        ns = sum(1 for r in rows if r["k"] == "str")
        nk = sum(1 for r in rows if r["k"] == "skip")
        print(f"{name}  {len(data):>9,}B  {len(rows):>7,} tokens  "
              f"{ns:>6,} strings  {nk:>5,} resyncs")


def cmd_text():
    os.makedirs(OUT, exist_ok=True)
    for name, data in blobs():
        lines = []
        for o, k, v in tokens(data):
            if k == "str":
                lines.append(f"{o:08X}\t{v}")
        with open(os.path.join(OUT, name + ".txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"{name}  {len(lines):,} strings")


def cmd_keys():
    """Infer key -> value pairs from string adjacency, then cross-tabulate."""
    # a "key" is a string that recurs across the corpus and is followed by a string
    pair_count = Counter()
    for name, data in blobs():
        toks = list(tokens(data))
        for a, b in zip(toks, toks[1:]):
            if a[1] == "str" and b[1] == "str":
                pair_count[(a[2], b[2])] += 1

    key_freq = Counter()
    for (k, v), c in pair_count.items():
        key_freq[k] += c

    print(f"{len(pair_count):,} distinct (label, value) adjacencies\n")
    print("--- most frequent labels ---")
    for k, c in key_freq.most_common(30):
        vals = [(v, n) for (kk, v), n in pair_count.items() if kk == k]
        vals.sort(key=lambda t: -t[1])
        top = ", ".join(f"{v}×{n}" for v, n in vals[:6])
        print(f"  {k:<22s} {c:>6,}   -> {top}")

    out = {}
    for k, c in key_freq.most_common(400):
        if c < 20:
            continue
        vals = [(v, n) for (kk, v), n in pair_count.items() if kk == k]
        vals.sort(key=lambda t: -t[1])
        out[k] = {"total": c, "values": vals[:60]}
    with open(os.path.join(HERE, "varbin_keytab.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\nwrote varbin_keytab.json ({len(out)} labels)")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "keys"
    {"tokens": cmd_tokens, "text": cmd_text, "keys": cmd_keys}[mode]()
