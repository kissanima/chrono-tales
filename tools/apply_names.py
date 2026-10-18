#!/usr/bin/env python3
"""Apply recovered names to already-extracted entries, in place.

`gpak_extract.py extract` names an entry when it runs, so names recovered
*afterwards* (by `varfile.py xref --merge`, `propagate_names.py`, or a manual
addition to names.json) leave the extracted tree stale: the files sit in
`unknown/<hash>.<ext>` even though the hash now has a path.

This renames those files instead of re-extracting 88 MB.

    python tools/apply_names.py            # dry run: report what would move
    python tools/apply_names.py --apply    # do it

Note the two hash encodings: `unknown/` filenames are **hex**, while the keys
in `names.json` are **decimal**.  Comparing them directly reports zero overlap
and makes it look like nothing is recoverable.
"""
import argparse
import json
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
EXTRACTED = ROOT / "extracted"
NAMES = HERE / "names.json"
ARCHIVES = ("netpak", "sysnet")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually move the files (default is a dry run)")
    args = ap.parse_args()

    names = json.load(open(NAMES, encoding="utf-8"))
    moved = skipped = unknown = 0
    total = 0

    for arch in ARCHIVES:
        unk = EXTRACTED / arch / "unknown"
        if not unk.is_dir():
            continue
        base = EXTRACTED / arch
        for f in sorted(unk.iterdir()):
            if not f.is_file():
                continue
            total += 1
            h = int(f.name.split(".")[0], 16)
            path = names.get(str(h))
            if not path:
                unknown += 1
                continue
            dest = base / path
            if dest.exists():
                print(f"  skip (exists) {arch}/{f.name} -> {path}")
                skipped += 1
                continue
            print(f"  {'move' if args.apply else 'would move'} "
                  f"{arch}/unknown/{f.name} -> {path}")
            if args.apply:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(dest))
            moved += 1

    verb = "moved" if args.apply else "would move"
    print(f"\n{total} entries in unknown/, {verb} {moved}, "
          f"already present {skipped}, still unnamed {unknown}")
    if not args.apply and moved:
        print("re-run with --apply to perform the moves")


if __name__ == "__main__":
    main()
