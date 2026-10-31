"""Link .53wf meshes to their archive textures.

The archive is indexed by the MPQ hash of a *path*, and the paths look like

    model/ECED0FB0.fw3        (mesh)
    tex/BDBC1E57.jpg          (texture)

i.e. literal ASCII directories and an 8-hex-digit asset GUID as the filename.
The GUID is an art-pipeline id, not derivable from the Chinese filename the
material carries, so the GUID <-> filename table has to be read out of the
entity database (varbin), where each decoration record lists a model path and
the texture path that goes with it.

This tool:
  1. reads the decoration table's (model, tex) pairs out of the varbin dumps
  2. hashes both paths and resolves them to real archive entries
  3. prints the verified mesh -> texture links and writes texmap.json
"""
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from gpak import hash_name  # noqa: E402
import gpak  # noqa: E402

MODEL_RE = re.compile(r"^model/[0-9A-Fa-f]{8}\.fw3$")
TEX_RE = re.compile(r"^tex/[0-9A-Fa-f]{8}\.(jpg|png)$")

# Identified by looking at the archive's square power-of-two images.  The
# material's own extension is a hard constraint (every hash-verified pair has
# matching extensions), which cuts the candidate set to the 10 model-texture
# AUTHORITATIVE: the six intro scene maps (.zmap) carry the game's own
# name -> file-GUID bindings (see tools/zmap.py / GPAK_FORMAT.md 4.10).  Each
# GUID below hashes to a live archive entry -- 108/108 object-texture
# references verify -- so every entry here is hash-verified.  This table
# SUPERSEDES the earlier visual/tentative guesses and corrects three of them:
# 木01/木02 were swapped, the old 圣坛003 tentative was actually 石头03, and
# 顶02/墙01 had look-alike files.  It also adds four names the guessing stage
# never had: 木箱, 石头01, 石头02, 石头03.
ZMAP = {
    "圣坛003.jpg": (0x7F2AECB1, "9C3F8774.jpg via intro zmap"),
    "墙01.jpg": (0xA72C4AF3, "5F42D358.jpg via intro zmap"),
    "小镇门.png": (0x696EBE10, "5380F0A5.png via intro zmap"),
    "新手村树叶.png": (0x67A10380, "2B0B24D1.png via intro zmap"),
    "新手村树叶1.png": (0x37DB0179, "84364C81.png via intro zmap"),
    "新手村树干.jpg": (0x1F640537, "2179A47E.jpg via intro zmap"),
    "新手村藤.png": (0x25F654F0, "6AF77F91.png via intro zmap"),
    "木01.jpg": (0x55747BAC, "5A1005D7.jpg via intro zmap"),
    "木02.jpg": (0x4F33AE61, "CF794CC6.jpg via intro zmap"),
    "木箱.jpg": (0x20916C6A, "DF514440.jpg via intro zmap"),
    "石头01.png": (0x4C117E2E, "B35D1B68.png via intro zmap"),
    "石头02.jpg": (0xB2A097B5, "99888485.jpg via intro zmap"),
    "石头03.png": (0x09E3A85E, "CCAED5C9.png via intro zmap"),
    "蘑菇001.jpg": (0xDD462F88, "BDBC1E57.jpg via intro zmap"),
    "顶01.jpg": (0xCDDA84AF, "A1C6FE42.jpg via intro zmap"),
    "顶02.jpg": (0x63A3CFE3, "B642CAD3.jpg via intro zmap"),
}

# Meshes that ship with no material table (the .53wf doesn't carry a MAT3
# block for them) but have a definitive single texture from elsewhere.  The
# renderer falls back to this when `materials` is empty.
MESH_TEX = {
    0x879926BF: (0x20916C6A, "wooden crate: texture is the crate itself"),
    0x4C85D7C7: (0x4F11538C, "flower07: varbin decoration table"),
}


def load_archives():
    """hash -> (archive, path) for every entry in both archives.

    Prefers files that actually exist on disk: since the asset manifest was
    applied, extracted files live under their canonical paths
    (netpak/tex/<guid>.<ext>, netpak/model/<guid>.fw3, ...), and an entry's
    archive hash is the MPQ hash of that relative path -- so hash the
    relative path of every real file and fall back to the (stale)
    _manifest.csv `unknown/<hash>` paths only when the file is missing.
    """
    out = {}
    for arc in ("netpak", "sysnet"):
        base = os.path.join(ROOT, "extracted", arc)
        for dirpath, _, files in os.walk(base):
            for fn in files:
                rel = os.path.relpath(os.path.join(dirpath, fn), base)
                rel = rel.replace("\\", "/")
                if "/" not in rel or rel.startswith("_") or rel.endswith(".var"):
                    continue
                h = gpak.hash_name(rel.lower(), 0) & 0xFFFFFFFF
                out.setdefault(h, (arc, rel))
        man = os.path.join(base, "_manifest.csv")
        if os.path.exists(man):
            for r in csv.DictReader(open(man, encoding="utf-8")):
                out.setdefault(int(r["hash"], 16), (arc, r["path"]))
    return out


def varbin_pairs():
    """(object name, model path, tex path) from the varbin string dumps.

    The decoration records emit name / model / tex consecutively, so a model
    line is followed by its texture line.
    """
    pairs = []
    vdir = os.path.join(ROOT, "extracted", "netpak_varbin")
    if not os.path.isdir(vdir):
        return pairs
    for fn in sorted(os.listdir(vdir)):
        if not fn.endswith(".txt"):
            continue
        lines = [l.split("\t", 1)[1].strip()
                 for l in open(os.path.join(vdir, fn), encoding="utf-8", errors="replace")
                 if "\t" in l]
        for i, ln in enumerate(lines):
            if MODEL_RE.match(ln):
                tex = None
                if i + 1 < len(lines) and TEX_RE.match(lines[i + 1]):
                    tex = lines[i + 1]
                name = lines[i - 1] if i else ""
                pairs.append((name, ln, tex))
    return pairs


def mesh_materials():
    """archive mesh hash -> material texture filenames (from the decoded JSON)."""
    out = {}
    mdir = os.path.join(ROOT, "extracted", "models")
    for fn in sorted(os.listdir(mdir)):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        try:
            h = int(fn[:-5], 16)
        except ValueError:
            continue                    # texmap.json and friends
        d = json.load(open(os.path.join(mdir, fn), encoding="utf-8"))
        names = []
        for m in d.get("materials") or []:
            t = m.get("texture") or m.get("tex")
            if t and t not in names:
                names.append(t)
        out[h] = names
    return out


def main():
    arcs = load_archives()
    mats = mesh_materials()
    pairs = varbin_pairs()

    print(f"varbin (model, tex) pairs : {len(pairs)}")
    print()

    links = {}
    for name, mp, tp in pairs:
        mh = hash_name(mp, 0)
        if mh not in arcs:
            print(f"  !! model not in archive: {mp} -> {mh:08x}")
            continue
        tex = None
        if tp:
            th = hash_name(tp, 0)
            tex = (th, arcs.get(th))
        links[mh] = {"object": name, "model": mp, "tex_path": tp,
                     "tex_hash": tex[0] if tex else None,
                     "tex_entry": tex[1] if tex else None,
                     "materials": mats.get(mh, [])}
        hit = "OK " if tex and tex[1] else "MISS"
        tn = f"{tp} -> {tex[0]:08x} {tex[1][0]}/{tex[1][1]}" if tex and tex[1] else str(tp)
        print(f"  {hit} {mh:08x} {name:28} {mp:24} {tn}")

    # collapse to material-filename -> tex hash, which is what the renderer wants
    byname = {}
    for mh, L in links.items():
        if not L["tex_hash"] or not L["materials"]:
            continue
        for m in L["materials"]:
            byname.setdefault(m, {"tex_hash": L["tex_hash"], "tex_entry": L["tex_entry"],
                                  "via": L["object"], "verified": True,
                                  "confidence": "hash"})

    print()
    print(f"material names resolved by the entity DB: {len(byname)}")
    for k, v in sorted(byname.items()):
        print(f"  {k:16} -> {v['tex_hash']:08x}  ({v['tex_entry'][0]}/{v['tex_entry'][1]})  via {v['via']}")

    for tag, table in (("zmap", ZMAP),):
        for name, (h, note) in table.items():
            ent = arcs.get(h)
            if not ent:
                print(f"  !! {tag} texture not in archive: {name} -> {h:08x}")
                continue
            if name in byname and byname[name].get("verified"):
                continue          # varbin hash link already authoritative
            byname[name] = {"tex_hash": h, "tex_entry": ent, "via": note,
                            "verified": True, "confidence": "hash"}

    nv = sum(1 for v in byname.values() if v.get("verified"))
    print(f"\ntotal material names mapped: {len(byname)}  "
          f"(hash-verified {nv})")

    # ---- mesh-level fallback (no material table, but we know the texture) ----
    by_mesh = {}
    # 1) manual content matches
    for mh, (h, note) in MESH_TEX.items():
        ent = arcs.get(h)
        if ent:
            by_mesh[f"{mh:08x}"] = {"tex_hash": h, "tex_entry": ent,
                                    "via": note, "verified": False}
    # 2) meshes with empty materials that the varbin links
    for mh, L in links.items():
        if mh in [int(k, 16) for k in by_mesh]:
            continue
        if mats.get(mh):
            continue                       # submesh binding already covers it
        if L.get("tex_hash") and L.get("tex_entry"):
            by_mesh[f"{mh:08x}"] = {"tex_hash": L["tex_hash"],
                                    "tex_entry": L["tex_entry"],
                                    "via": "varbin (mesh-level)",
                                    "verified": True}
    print(f"mesh-level fallbacks: {len(by_mesh)}  (for meshes with no material table)")

    out = {"links": {f"{k:08x}": v for k, v in links.items()},
           "by_name": byname, "by_mesh": by_mesh}
    dst = os.path.join(ROOT, "extracted", "models", "texmap.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n-> {dst}")


if __name__ == "__main__":
    main()
