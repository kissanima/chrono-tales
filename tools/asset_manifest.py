#!/usr/bin/env python3
"""Decode the two asset-manifest `.var` files and use them to name archive entries.

`netpak/unknown/3602e346.bin` and `sysnet/unknown/3602e346.bin` share a name
hash but are different files: each is the packer's own manifest of what it
intended to ship.

    magic  0xFFABC2BB  == MAGIC_BASE (0xFFABC257) + 100
    body   tagged `.var` grammar with seed 100 (NOT plain, NOT seed 0)

So they are ordinary `.var` files read by `varfile.load` — the encrypted branch
that `SYSFILEVER.md` records as "read from the writer but never exercised" is
exercised here, and it validates: 0 string-checksum failures and exact
consumption to the last byte in both files.

Layout, as emitted:

    [ "FW405IGdT5",                    // 10-char pack-build tag
      [ ["action/1062cc2c.zip", 79172, 1335165054], ... ] ]

i.e. `[tag, [[path, size, mtime], ...]]`.  Paths use forward slashes and the
manifest's own casing; sizes are the *uncompressed* payload length.

Why this matters: it is the packer's name table, so hashing a manifest path with
`gpak.hash_name` recovers the archive's filename key directly.  It supersedes
pure guessing for every path the packer listed.

    python tools/asset_manifest.py check          # parse both, assert byte-exact
    python tools/asset_manifest.py stats          # per-directory shipped/missing
    python tools/asset_manifest.py names          # dry run: new names found
    python tools/asset_manifest.py names --apply  # merge into tools/names.json
    python tools/asset_manifest.py csv -o m.csv   # dump every manifest row
"""
import argparse
import collections
import csv
import datetime
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import gpak                                        # noqa: E402
import varfile                                     # noqa: E402

ARCHIVES = ("netpak", "sysnet")
NAMES = HERE / "names.json"
MANIFEST_NAME = "3602e346.bin"


def manifest_path(arch):
    return ROOT / "extracted" / arch / "unknown" / MANIFEST_NAME


def decode(arch):
    """-> dict(tag, rows) where rows is [(path, size, mtime), ...]."""
    path = manifest_path(arch)
    if not path.is_file():
        raise SystemExit(f"missing {path} — run gpak_extract.py extract first")
    d = varfile.load(path)
    if d["consumed"] != d["total"]:
        raise SystemExit(f"{arch}: left {d['total'] - d['consumed']} bytes undecoded")
    tag, rows = d["value"]
    return {"arch": arch, "tag": tag, "rows": rows, "stats": d,
            "path": path, "total": d["total"]}


def all_manifests():
    return [decode(a) for a in ARCHIVES]


def archive_hashes(arch):
    """-> dict nameHash -> Entry for one shipped archive."""
    a = gpak.GpakArchive(str(ROOT / "gbox" / "lm_en" / f"{arch}.gpak"))
    return {e.name_hash: e for e in a.entries}


def resolved(m):
    """-> dict nameHash -> (path, size, mtime) for manifest rows that ship.

    `gpak.hash_name` already lower-cases ASCII and normalises `\\` to `/`, so
    the manifest's own casing is irrelevant to the match; it is only used as the
    display name.
    """
    entries = archive_hashes(m["arch"])
    out = {}
    for row in m["rows"]:
        p = row[0]
        h = gpak.hash_name(p)
        if h in entries:
            out[h] = (p, entries[h].size, row[2] if len(row) > 2 else None)
    return out


def cmd_check(a):
    bad = 0
    for m in all_manifests():
        s = m["stats"]
        ok = s["consumed"] == s["total"] and s["bad_checksums"] == 0
        bad += 0 if ok else 1
        print(f"  {m['arch']:8s} {m['total']:>7d} bytes  seed {s['flag']:#x}  "
              f"rows {len(m['rows']):>5d}  strings {s['strings']:>5d}  "
              f"bad-checksums {s['bad_checksums']}  "
              f"{'exact' if ok else 'MISMATCH'}")
    print(f"\n{len(ARCHIVES) - bad}/{len(ARCHIVES)} manifests parse byte-exactly")
    return 0 if bad == 0 else 1


def cmd_stats(a):
    for m in all_manifests():
        res = resolved(m)
        entries = archive_hashes(m["arch"])
        per = collections.Counter()
        miss = collections.Counter()
        for row in m["rows"]:
            top = row[0].split("/")[0]
            per[top] += 1
            if gpak.hash_name(row[0]) not in entries:
                miss[top] += 1
        print(f"--- {m['arch']}: manifest {len(m['rows'])} paths, "
              f"archive {len(entries)} entries, {len(res)} hash-matched")
        print(f"    {'dir':16s} {'listed':>7s} {'missing':>8s}")
        for k, v in per.most_common():
            print(f"    {k:16s} {v:7d} {miss.get(k, 0):8d}")
        unexplained = set(entries) - set(res)
        print(f"    -> {len(unexplained)} archive entries NOT in this manifest")
        print()
    return 0


def cmd_names(a):
    names = json.load(open(NAMES, encoding="utf-8"))
    have = {int(k): v for k, v in names.items()}

    found = {}
    for m in all_manifests():
        for h, (p, size, ts) in resolved(m).items():
            found.setdefault(h, p)

    new = {h: p for h, p in found.items() if h not in have}
    recase = {h: p for h, p in found.items() if h in have and have[h] != p}
    conflicts = {h: (have[h], p) for h, p in recase.items()
                 if have[h].lower() != p.lower()}

    total = 0
    for arch in ARCHIVES:
        total += len(archive_hashes(arch))

    print(f"names.json now      {len(have):>4d} / {total}")
    print(f"manifest resolves   {len(found):>4d} / {total}")
    print(f"  new               {len(new):>4d}")
    print(f"  present, recased  {len(recase):>4d}")
    print(f"  real conflicts    {len(conflicts):>4d}")
    merged = dict(have)
    merged.update(found)
    print(f"merged coverage     {len(merged):>4d} / {total} "
          f"({100.0 * len(merged) / total:.1f}%)")

    if conflicts:
        print("\nCONFLICTS (same path, different case-insensitive name):")
        for h, (old, newp) in sorted(conflicts.items()):
            print(f"  {h:08X}  old={old}  new={newp}")

    if a.verbose:
        for h, p in sorted(new.items(), key=lambda kv: kv[1]):
            print(f"  + {h:08X}  {p}")

    if not a.apply:
        print("\nre-run with --apply to write tools/names.json")
        return 0

    payload = {str(h): p for h, p in sorted(merged.items())}
    NAMES.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
    print(f"\nwrote {len(payload)} names to {NAMES}")
    return 0


def cmd_csv(a):
    out = open(a.out, "w", newline="", encoding="utf-8-sig") if a.out else sys.stdout
    w = csv.writer(out)
    w.writerow(["archive", "path", "size", "mtime", "utc", "hash", "shipped"])
    for m in all_manifests():
        entries = archive_hashes(m["arch"])
        for row in m["rows"]:
            p = row[0]
            h = gpak.hash_name(p)
            ts = row[2] if len(row) > 2 else None
            utc = (datetime.datetime.fromtimestamp(ts, datetime.UTC)
                   .strftime("%Y-%m-%d %H:%M:%S") if ts else "")
            w.writerow([m["arch"], p, row[1] if len(row) > 1 else "",
                        ts if ts is not None else "", utc,
                        f"{h:08X}", "yes" if h in entries else "no"])
    if a.out:
        out.close()
        print(f"wrote {a.out}")
    return 0


def cmd_unexplained(a):
    """Archive entries no manifest row names — the residual unknown set."""
    named = {}
    for m in all_manifests():
        named.update({h: p for h, (p, _, _) in resolved(m).items()})
    for arch in ARCHIVES:
        entries = archive_hashes(arch)
        rest = sorted(set(entries) - set(named))
        print(f"--- {arch}: {len(rest)} / {len(entries)} entries unnamed")
        for h in rest:
            e = entries[h]
            print(f"    {h:08X} {e.size:>9d} bytes")
        print()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="parse both manifests, assert byte-exact")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("stats", help="per-directory shipped/missing")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("names", help="resolve hashes, merge into names.json")
    p.add_argument("--apply", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="list every newly resolved name")
    p.set_defaults(fn=cmd_names)

    p = sub.add_parser("csv", help="dump every manifest row")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_csv)

    p = sub.add_parser("unexplained", help="archive entries no manifest names")
    p.set_defaults(fn=cmd_unexplained)

    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
