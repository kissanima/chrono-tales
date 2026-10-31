#!/usr/bin/env python3
"""action_link.py - resolve which animation pack every actor uses.

This closes the last open link in the animation chain (GPAK_FORMAT.md 4.9).
There are two independent selection paths, and both end in an
`action/<guid>.zip` — a ZIP of `.caf` clips, one clip per file:

  NPC / monster / mount
      npctabedit.模型  ->  SysFileVer[模型]  ->  "modle=,model/X.cmz;tex=...,tex/Y;
                                                 action=,action/Z.zip"
      The spec is inline: the skeleton and the clip pack come from one row.

  player character
      模板|人类男 / 模板|人类女  ->  ziptabedit row  ->  原始动作包 (base pack)
                                                        + 附加动作包 (add pack)
      Both pack names are themselves SysFileVer keys -> action/<guid>.zip.

Cross-check: every `_D` (dynamic) alias in SysFileVer points straight at an
`action/` zip, and it must agree with the `action=` field of the matching `_Z`
(static) spec. The tool fails loudly if any alias disagrees.

Output: extracted/tables/action_links.csv   (one row per NPC model)
        extracted/tables/action_packs.csv   (one row per pack reference)
"""
import collections
import csv
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import varfile  # noqa: E402

TABLES = ROOT / "extracted" / "tables"
ACTION_DIR = ROOT / "extracted" / "netpak" / "action"
REG = ROOT / "extracted" / "netpak" / "SysFileVer.var"


def load_registry():
    reg = {}
    for r in varfile.load(str(REG))["value"]:
        if len(r) > 1 and isinstance(r[0], str) and isinstance(r[1], str):
            reg[r[0]] = r[1]
    return reg


def split_spec(spec):
    """`modle=,model/X.cmz;tex=n,tex/Y;action=,action/Z.zip` -> dict."""
    out = {"model": None, "action": None, "textures": [], "materials": []}
    for part in (spec or "").split(";"):
        toks = [t.strip() for t in part.split(",")]
        for i, t in enumerate(toks):
            if t.startswith("model/"):
                out["model"] = t
            elif t.startswith("action/"):
                out["action"] = t
            elif t.startswith("tex/"):
                out["textures"].append(t)
                if i and not toks[i - 1].startswith(("tex/", "model/", "action/")):
                    out["materials"].append(toks[i - 1])
    return out


def guid_of(path):
    return path.rsplit("/", 1)[-1].split(".")[0].lower() if path else None


def shipped(path):
    """Does this archive path exist on disk (named or by hash)?"""
    if not path:
        return False
    p = ROOT / "extracted" / "netpak" / path
    return p.exists()


def main():
    reg = load_registry()
    npc = list(csv.DictReader(
        open(TABLES / "npctabedit.csv", encoding="utf-8-sig")))
    zipt = list(csv.DictReader(
        open(TABLES / "ziptabedit.csv", encoding="utf-8-sig")))

    # ---------------- NPC path ----------------
    rows, seen = [], set()
    for r in npc:
        name = (r.get("模型") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        spec = reg.get(name, "")
        parts = split_spec(spec)
        rows.append({
            "npc_id": r.get("ID", ""),
            "npc_name": (r.get("显示名") or "").strip(),
            "model": name,
            "cmz": parts["model"] or "",
            "cmz_shipped": shipped(parts["model"]),
            "action": parts["action"] or "",
            "action_shipped": shipped(parts["action"]),
            "n_tex": len(parts["textures"]),
            "n_tex_shipped": sum(1 for t in parts["textures"] if shipped(t)),
        })
    rows.sort(key=lambda x: x["model"])

    # ---------------- player path ----------------
    packs = []
    for r in zipt:
        for col in ("原始动作包", "附加动作包"):
            pk = (r.get(col) or "").strip()
            if not pk:
                continue
            path = reg.get(pk, "")
            packs.append({
                "system": r["系统名"],
                "role": col,
                "pack": pk,
                "action": path if path.startswith("action/") else "",
                "action_shipped": shipped(path),
                "index": r.get("查找索引", ""),
                "replacement": r.get("替换动作", ""),
            })

    # ---------------- consistency ----------------
    # every `_D` alias must name the same action GUID as its `_Z` sibling's spec
    bad, checked = [], 0
    for name, spec in reg.items():
        if not name.endswith("_D") or not spec.startswith("action/"):
            continue
        z = name[:-2] + "_Z"
        if z not in reg:
            continue
        checked += 1
        want = guid_of(split_spec(reg[z])["action"])
        got = guid_of(spec)
        if want and want != got:
            bad.append((name, want, got))

    # ---------------- report ----------------
    n = len(rows)
    wm = sum(1 for r in rows if r["cmz"])
    wa = sum(1 for r in rows if r["action"])
    print(f"NPC models            : {n}")
    print(f"  with .cmz skeleton  : {wm}")
    print(f"  with action pack    : {wa}")
    print(f"  pack shipped        : {sum(1 for r in rows if r['action_shipped'])}")
    print(f"  skeleton shipped    : {sum(1 for r in rows if r['cmz_shipped'])}")
    print(f"player pack references: {len(packs)} "
          f"({len({p['pack'] for p in packs})} distinct names)")
    print(f"  resolved to action/ : {sum(1 for p in packs if p['action'])}")
    print(f"  shipped             : {sum(1 for p in packs if p['action_shipped'])}")
    print(f"_D/_Z alias cross-check: {checked} pairs, {len(bad)} disagreements")
    for b in bad[:5]:
        print("   MISMATCH", b)
    if bad:
        raise SystemExit("alias cross-check failed")

    with open(TABLES / "action_links.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(TABLES / "action_packs.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(packs[0]))
        w.writeheader()
        w.writerows(packs)

    # what is actually on disk
    on_disk = sorted(p.name for p in ACTION_DIR.glob("*.zip")) \
        if ACTION_DIR.exists() else []
    print(f"\naction zips on disk   : {len(on_disk)} {on_disk}")
    print(f"wrote {TABLES / 'action_links.csv'} ({n} rows)")
    print(f"wrote {TABLES / 'action_packs.csv'} ({len(packs)} rows)")


if __name__ == "__main__":
    main()
