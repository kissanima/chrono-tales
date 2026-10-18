"""Decode the `varbin` entity database as a tagged tree.

Every varbin payload is the same tagged serialisation as `SysFileVer.var`,
just unencrypted (see `varfile.py`).  That replaces the earlier attempt to
infer a positional numeric grammar, which stalled at 48% field coverage
because the record boundaries were never actually fixed-width.

    python tools/varbin_tree.py check      # parse all 48, assert byte-exact
    python tools/varbin_tree.py show  <id> # pretty-print one payload
    python tools/varbin_tree.py schema     # shape of every payload
    python tools/varbin_tree.py csv <id>   # records of one payload as CSV

`<id>` is the 8-hex archive entry hash, e.g. `0580853e`.
"""
import argparse
import collections
import csv
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
from gpak import unwrap, wrap_tag  # noqa: E402
import varfile  # noqa: E402

UNKNOWN = pathlib.Path("extracted/netpak/unknown")


def payloads():
    """Yield (id, decoded) for every varbin payload in the install."""
    for f in sorted(UNKNOWN.glob("*.zip")):
        raw = f.read_bytes()
        if wrap_tag(raw) != 0x602376D7:
            continue
        yield f.stem, varfile.load_varbin(unwrap(raw))


def shape(v):
    """Collapse a decoded tree to a structural signature."""
    if isinstance(v, list):
        inner = [shape(x) for x in v]
        if not inner:
            return "[]"
        # all elements the same shape -> collapse
        if all(x == inner[0] for x in inner):
            return f"[{len(v)} x {inner[0]}]"
        return "[" + ",".join(inner[:6]) + ("..." if len(inner) > 6 else "") + "]"
    if isinstance(v, tuple):
        return "vec3"
    if isinstance(v, bytes):
        return f"blob({len(v)})"
    if isinstance(v, str):
        return "str" if v else "''"
    if v is None:
        return "null"
    return type(v).__name__


def cmd_check(a):
    ok = bad = 0
    for name, d in payloads():
        if d["consumed"] == d["total"]:
            ok += 1
            print(f"  {name}  {d['total']:>8d} bytes  arrays {d['arrays']:>6d}  "
                  f"strings {d['strings']:>6d}  blobs {d['blobs']}")
        else:
            bad += 1
            print(f"  {name}  !! left {d['total'] - d['consumed']}")
    print(f"\n{ok} payloads parse byte-exactly, {bad} failed")
    return 0 if bad == 0 else 1


def cmd_show(a):
    for name, d in payloads():
        if name != a.id:
            continue
        print(json.dumps(d["value"], ensure_ascii=False, indent=1)[:a.limit])
        print(f"\nconsumed {d['consumed']}/{d['total']}")
        return 0
    sys.exit(f"no varbin {a.id}")


def cmd_schema(a):
    for name, d in payloads():
        print(f"{name}  {d['total']:>8d} bytes")
        print(f"   {shape(d['value'])}")


def cmd_csv(a):
    for name, d in payloads():
        if name != a.id:
            continue
        rows = d["value"]
        if not isinstance(rows, list):
            sys.exit("top level is not an array")
        out = open(a.out, "w", newline="", encoding="utf-8-sig") if a.out else sys.stdout
        w = csv.writer(out)
        for rec in rows:
            w.writerow([json.dumps(x, ensure_ascii=False) if isinstance(x, (list, tuple))
                        else ("" if x is None else x)
                        for x in (rec if isinstance(rec, list) else [rec])])
        if a.out:
            out.close()
            print(f"wrote {len(rows)} rows to {a.out}")
        return 0
    sys.exit(f"no varbin {a.id}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="parse every payload, assert byte-exact")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("show", help="pretty-print one payload")
    p.add_argument("id")
    p.add_argument("-n", "--limit", type=int, default=4000)
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("schema", help="structural shape of every payload")
    p.set_defaults(fn=cmd_schema)

    p = sub.add_parser("csv", help="one payload's records as CSV")
    p.add_argument("id")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_csv)

    a = ap.parse_args()
    try:
        sys.exit(a.fn(a) or 0)
    except varfile.VarError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
