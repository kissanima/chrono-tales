#!/usr/bin/env python3
"""Run every declared check in the repo and report pass/fail.

The repo has 60+ scripts and, before this, exactly one validator.  Nothing
proved that a change in one format did not silently break another, and two real
regressions already shipped that way:

  * `payloads()` lost 6 payloads after renames and still printed "42 / 48"
  * an orphan CSV from a corrected tie-break inflated the table count to 43

Both were *quiet*.  This runner makes them loud: every check states an expected
invariant in its output and exits non-zero on violation.

    python tools/check_all.py          # run everything
    python tools/check_all.py -v       # show each check's full output
    python tools/check_all.py -k gpak  # only checks whose name matches

Exit code is 0 only if every check passes.
"""
import argparse
import re
import subprocess
import sys
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
PY = sys.executable


class Check:
    """One invariant, asserted against a command's output.

    `pattern` is a regex the output must match; `forbid` is a regex it must not
    match.  Both are matched against the whole captured output, so a check can
    assert "48 payloads ... 0 failed" without caring about surrounding lines.
    """

    def __init__(self, name, args, pattern, forbid=None, why="", skip_ok=False):
        self.name = name
        self.args = args
        self.pattern = pattern
        self.forbid = forbid
        self.why = why
        self.skip_ok = skip_ok


# Every row here is a claim the project makes about the shipped data.
CHECKS = [
    Check("gpak-pages", ["tools/gpak_extract.py", "verify", "gbox/lm_en/netpak.gpak"],
          r"entries ok=148 bad=0.*page CRC ok=1843 bad=0",
          why="148 entries, 1843 data pages, no CRC failures"),
    Check("gpak-pages-sysnet", ["tools/gpak_extract.py", "verify", "gbox/lm_en/sysnet.gpak"],
          r"entries ok=336 bad=0.*page CRC ok=2704 bad=0",
          why="336 entries, 2704 data pages"),
    Check("gpak-writer", ["tools/gpak_repack.py", "roundtrip", "gbox/lm_en/netpak.gpak"],
          r"differing bytes: 0 /",
          why="the writer reproduces the archive byte-for-byte"),

    Check("varbin-tree", ["tools/varbin_tree.py", "check"],
          r"48 payloads parse byte-exactly, 0 failed",
          why="all 48 varbin payloads decode to their exact last byte; a count of "
              "42 means discovery lost the map payloads"),
    Check("varbin-identify", ["tools/varbin_identify.py"],
          r"identified 42/48 payloads",
          why="one-to-one editor assignment; 42 of 48 payloads are tables"),
    Check("tables", ["tools/varbin_tables.py"],
          r"42 tables, 19,634 rows",
          why="exactly 42 tables; 43 means a stale CSV survived a re-identification"),
    Check("maps", ["tools/map_decode.py"],
          r"6 intro/\*\.zmap files hash to exactly MAP_BLOBS",
          why="the six map payloads and the six intro files are one asset set, and "
              "two independent discovery paths must agree"),

    Check("manifest-netpak", ["tools/asset_manifest.py", "check"],
          r"netpak.*bad-checksums 0.*exact",
          why="seed-100 .var decodes with no checksum failures"),
    Check("manifest-sysnet", ["tools/asset_manifest.py", "check"],
          r"sysnet.*bad-checksums 0.*exact",
          why="same, second file"),
    Check("names-coverage", ["tools/asset_manifest.py", "names"],
          r"merged coverage\s+433 / 484",
          forbid=r"real conflicts\s+[1-9]",
          why="433/484 names, and no disagreement with earlier recovery stages"),

    Check("sbus", ["tools/sbus_validate.py", "--strict"],
          r"all checks passed",
          why="bone range, weight sum, unit normals, index range across 9 .cmz"),

    Check("explorer", ["tools/build_explorer.py"],
          r"archive\s+433/484 filenames recovered",
          why="the explorer's figures are derived, so this re-reads every index"),
]

# checks that only make sense once the caf track has landed
OPTIONAL = [
    Check("caf-decode", ["tools/caf_decode.py", "check"],
          r"byte-exact on 5/5",
          why="all five .caf clips consume their file exactly -- this is the real "
              "proof of layout, since it cannot pass on a wrong stride"),
    Check("caf-validate", ["tools/caf_validate.py", "--strict"],
          r"all checks passed",
          why="independent properties only: unit quaternions, bone id range, "
              "keytime bounds, no duplicates",
          skip_ok=True),
]


def run(c):
    try:
        p = subprocess.run([PY] + c.args, cwd=str(ROOT), capture_output=True,
                           text=True, timeout=600)
    except FileNotFoundError:
        return None, "script missing"
    except subprocess.TimeoutExpired:
        return None, "timed out after 600s"
    return p, (p.stdout or "") + (p.stderr or "")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-k", "--filter", default="")
    ap.add_argument("--all", action="store_true",
                    help="include checks whose script does not exist yet")
    a = ap.parse_args()

    checks = list(CHECKS)
    if a.all:
        checks += OPTIONAL

    rows = []
    for c in checks:
        if a.filter and a.filter not in c.name:
            continue
        p, out = run(c)
        if p is None:
            if out == "script missing":
                rows.append((c, "SKIP", "not implemented yet"))
                continue
            rows.append((c, "FAIL", out))
            continue
        if p.returncode != 0:
            rows.append((c, "FAIL", f"exit {p.returncode}\n{out.strip()[-800:]}"))
            continue
        if not re.search(c.pattern, out, re.S | re.M):
            rows.append((c, "FAIL", f"output did not match /{c.pattern}/\n"
                                    f"{out.strip()[-800:]}"))
            continue
        if c.forbid and re.search(c.forbid, out, re.S | re.M):
            rows.append((c, "FAIL", f"forbidden /{c.forbid}/ present\n"
                                    f"{out.strip()[-800:]}"))
            continue
        rows.append((c, "ok", ""))

    width = max((len(c.name) for c, _, _ in rows), default=10)
    for c, status, detail in rows:
        print(f"  {c.name:<{width}}  {status:<4}  {c.why if status == 'ok' else ''}")
        if status != "ok":
            print(f"  {'':<{width}}  ....  {c.why}")
            for line in detail.splitlines():
                print(f"  {'':<{width}}        {line}")
        if a.verbose and status == "ok":
            pass

    bad = [r for r in rows if r[1] == "FAIL"]
    skipped = [r for r in rows if r[1] == "SKIP"]
    print(f"\n{len(rows) - len(bad) - len(skipped)}/{len(rows) - len(skipped)} checks "
          f"passed" + (f", {len(skipped)} skipped" if skipped else ""))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
