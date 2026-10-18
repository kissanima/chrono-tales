#!/usr/bin/env python3
"""Census of every `sbus` header field across all shipped .cmz files.

Answers two questions that the per-file dumps cannot:

  * which header fields are constant (format markers) and which vary (payload);
  * whether the two residual unknowns at +21 / +25 are ever non-zero.  They
    are 0 in all 15 shipped parts, which is why their meaning has to come from
    the disassembly rather than from the data.

Usage:
    python sbus_census.py [--root DIR]
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cmz  # noqa: E402
import sbus_decode as sd  # noqa: E402

FIELDS = ["part_index", "n_verts", "n_faces", "n_uv", "unk21", "unk25"]


def census(root):
    seen = {f: set() for f in FIELDS}
    parts = rejected = trailing = 0
    for p in sorted(root.rglob("*.cmz")):
        d = p.read_bytes()
        mesh = next(((o, e) for o, t, e in cmz.walk(d) if t == "MESH"), None)
        if mesh is None:
            continue
        marks = cmz.find_sbus(d, mesh[0] + 8, mesh[1])
        for k, off in enumerate(marks):
            end = marks[k + 1] if k + 1 < len(marks) else mesh[1]
            r = sd.decode(d[off:end])
            if r is None:
                rejected += 1
                continue
            parts += 1
            if r["consumed"] != r["blob_len"]:
                trailing += 1
            for f in FIELDS:
                seen[f].add(r[f])
    return parts, rejected, trailing, seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path,
                    default=pathlib.Path("extracted/netpak/unknown"))
    a = ap.parse_args()

    parts, rejected, trailing, seen = census(a.root)
    print(f"{parts} sbus parts decoded, {rejected} rejected, "
          f"{trailing} with bytes after the stream")
    print()
    for f in FIELDS:
        vals = sorted(seen[f])
        if len(vals) == 1:
            print(f"  {f:<11s} constant  {vals[0]}")
        else:
            print(f"  {f:<11s} {len(vals):>3d} distinct  "
                  f"min={min(vals)} max={max(vals)}")
    print()
    print("residual unknowns:")
    print("  +21 (unk21) values:", sorted(seen["unk21"]))
    print("  +25 (unk25) values:", sorted(seen["unk25"]))
    print()
    print("bytes after the stream are the *next* part's name header, not mesh "
          "data - see sbus_decode.part_name().")


if __name__ == "__main__":
    main()
