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

MODEL_RE = re.compile(r"^model/[0-9A-Fa-f]{8}\.fw3$")
TEX_RE = re.compile(r"^tex/[0-9A-Fa-f]{8}\.(jpg|png)$")

# Identified by looking at the archive's square power-of-two images.  The
# material's own extension is a hard constraint (every hash-verified pair has
# matching extensions), which cuts the candidate set to the 10 model-texture
# PNGs, and the art itself is unambiguous: hanging vines, a teal tree, a
# yellow-green tree, and -- for the mesh whose material is called "小镇门.png"
# but is actually a drooping flower plant -- pink flower buds and a stem
# (696ebe10) mapped to the buds/stem/leaves.  These are content matches, NOT
# hash-verified.
INFERRED = {
    "新手村藤.png": (0x25F654F0, "hanging vines, alpha cutout"),
    "新手村树叶.png": (0x37DB0179, "teal tree canopy, alpha cutout"),
    "新手村树叶1.png": (0x67A10380, "yellow-green tree canopy, alpha cutout"),
    "小镇门.png": (0x696EBE10, "pink flower buds and stem (despite the name)"),
    "顶01.jpg": (0xCDDA84AF, "orange roof tiles"),
    "墙01.jpg": (0xA3818155, "stone brick with moss"),
    "木01.jpg": (0x05C8952E, "dark vertical planks"),
    "木02.jpg": (0x55747BAC, "dark timber beams"),
    "顶02.jpg": (0x659837F7, "pale cracked stone"),
}

# Plausible but not demonstrable: the tree bark and the altar.
TENTATIVE = {
    "新手村树干.jpg": (0x184A22B3, "dark bark-like grain"),
    "圣坛003.jpg": (0x09E3A85E, "grey stone slabs"),
}

# Meshes that ship with no material table (the .53wf doesn't carry a MAT3
# block for them) but have a definitive single texture from elsewhere.  The
# renderer falls back to this when `materials` is empty.
MESH_TEX = {
    0x879926BF: (0x20916C6A, "wooden crate: texture is the crate itself"),
    0x4C85D7C7: (0x4F11538C, "flower07: varbin decoration table"),
}


def load_archives():
    """hash -> (archive, path) for every entry in both archives."""
    out = {}
    for arc in ("netpak", "sysnet"):
        man = os.path.join(ROOT, "extracted", arc, "_manifest.csv")
        if not os.path.exists(man):
            continue
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

    for tag, table in (("visual", INFERRED), ("tentative", TENTATIVE)):
        for name, (h, note) in table.items():
            if name in byname:
                continue
            ent = arcs.get(h)
            if not ent:
                print(f"  !! {tag} texture not in archive: {name} -> {h:08x}")
                continue
            byname[name] = {"tex_hash": h, "tex_entry": ent, "via": note,
                            "verified": False, "confidence": tag}

    nv = sum(1 for v in byname.values() if v.get("verified"))
    ni = sum(1 for v in byname.values()
             if not v.get("verified") and v.get("confidence") == "visual")
    nt = len(byname) - nv - ni
    print(f"\ntotal material names mapped: {len(byname)}  "
          f"(hash-verified {nv}, visual {ni}, tentative {nt})")

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
