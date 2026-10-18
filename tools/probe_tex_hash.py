"""Chase the model-texture path hash.

Materials inside .53wf name their texture as a bare filename (蘑菇001.jpg).  The
archive stores entries under the 32-bit MPQ hash of a *path*, so the question is
what the client prepends.

This probe:
  1. collects every image entry (jpg/png) from both archives, keyed by hash
  2. builds a prefix vocabulary from every place a path fragment can be seen
     (resolved names.json paths, script image references, the client's strings)
  3. hashes  prefix + name  under both plausible string encodings and reports hits
"""
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from gpak import crypt_table, hash_name  # noqa: E402

TBL = crypt_table()
MASK = 0xFFFFFFFF


def hash_bytes(bs, ht=0):
    """MPQ HashString over raw bytes (the C `char*` reading)."""
    base = (ht & 7) << 8
    s1, s2 = 0x7FED7FED, 0xEEEEEEEE
    for c in bs:
        if c == 0x5C:
            c = 0x2F
        elif 0x41 <= c <= 0x5A:
            c += 0x20
        s1 = (s1 + s2) & MASK
        s1 ^= TBL[base + (c & 0xFF)]
        s2 = (s2 + c + ((s2 << 5) & MASK) + s1 + 3) & MASK
    return s1


def hash_state(bs, ht=0):
    """MPQ hash state after consuming `bs`, so a prefix can be continued cheaply."""
    base = (ht & 7) << 8
    s1, s2 = 0x7FED7FED, 0xEEEEEEEE
    for c in bs:
        if c == 0x5C:
            c = 0x2F
        elif 0x41 <= c <= 0x5A:
            c += 0x20
        s1 = (s1 + s2) & MASK
        s1 ^= TBL[base + (c & 0xFF)]
        s2 = (s2 + c + ((s2 << 5) & MASK) + s1 + 3) & MASK
    return s1, s2


def continue_hash(state, bs, ht=0):
    base = (ht & 7) << 8
    s1, s2 = state
    for c in bs:
        if c == 0x5C:
            c = 0x2F
        elif 0x41 <= c <= 0x5A:
            c += 0x20
        s1 = (s1 + s2) & MASK
        s1 ^= TBL[base + (c & 0xFF)]
        s2 = (s2 + c + ((s2 << 5) & MASK) + s1 + 3) & MASK
    return s1


def hash_name_gbk(name):
    return hash_bytes(name.encode("gbk"))


def hash_name_utf16(name):
    """The wide-char reading: UTF-16 code units, two hash rounds above 0xFF."""
    base = 0
    s1, s2 = 0x7FED7FED, 0xEEEEEEEE
    for ch in name:
        c = ord(ch)
        if c == 0x5C:
            c = 0x2F
        elif 0x41 <= c <= 0x5A:
            c += 0x20
        if c > 0xFF:
            s1 = (s1 + s2) & MASK
            s1 ^= TBL[base + (c & 0xFF)]
            s2 = (s2 + c + ((s2 << 5) & MASK) + s1 + 3) & MASK
            c >>= 8
        s1 = (s1 + s2) & MASK
        s1 ^= TBL[base + (c & 0xFF)]
        s2 = (s2 + c + ((s2 << 5) & MASK) + s1 + 3) & MASK
    return s1


# --------------------------------------------------------------------------- #
# 1. what the archive holds
# --------------------------------------------------------------------------- #
def image_hashes():
    """hash -> 'archive/path' for every jpg/png entry in both archives."""
    out = {}
    for arc in ("netpak", "sysnet"):
        man = os.path.join(ROOT, "extracted", arc, "_manifest.csv")
        if not os.path.exists(man):
            continue
        for r in csv.DictReader(open(man, encoding="utf-8")):
            if r["detected"] in ("jpg", "png"):
                out.setdefault(int(r["hash"], 16), f"{arc}:{r['path']}")
    return out


def texture_names():
    """Every distinct texture filename referenced by a decoded mesh."""
    names = set()
    mdir = os.path.join(ROOT, "extracted", "models")
    for f in os.listdir(mdir):
        if not f.endswith(".json") or f.startswith("_"):
            continue
        d = json.load(open(os.path.join(mdir, f), encoding="utf-8"))
        for m in d.get("materials") or []:
            t = m.get("texture") or m.get("tex")
            if t:
                names.add(t)
    return sorted(names)


# --------------------------------------------------------------------------- #
# 2. prefix vocabulary
# --------------------------------------------------------------------------- #
def ancestors(path):
    """Every parent directory of a slash path, stopping at the root."""
    out = []
    d = path
    seen = set()
    while d and d not in seen:
        seen.add(d)
        out.append(d)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return out


def prefixes_from_names_json():
    p = os.path.join(HERE, "names.json")
    out = set()
    for path in json.load(open(p, encoding="utf-8")).values():
        d = os.path.dirname(path.replace("\\", "/"))
        for a in ancestors(d):
            out.add(a + "/" if a else "")
    return out


def prefixes_from_scripts():
    """Directory of every image path a .gbc mentions."""
    pat = re.compile(r"([A-Za-z0-9_/\\-]+)\.(?:jpg|png)", re.I)
    out = set()
    for arc in ("netpak", "sysnet"):
        for base, _, files in os.walk(os.path.join(ROOT, "extracted", arc)):
            for fn in files:
                if not fn.lower().endswith(".gbc"):
                    continue
                try:
                    txt = open(os.path.join(base, fn), "rb").read().decode("gbk", "ignore")
                except OSError:
                    continue
                for m in pat.finditer(txt):
                    d = os.path.dirname(m.group(1).replace("\\", "/"))
                    if d:
                        out.add(d + "/")
    return out


def prefixes_from_binary():
    """Path fragments visible in the client's own string table."""
    exe = os.path.join(ROOT, "gbox", "lm_en", "chronotales.exe")
    if not os.path.exists(exe):
        return set()
    d = open(exe, "rb").read()
    out = set()
    for m in re.finditer(rb"[\x20-\x7e]{3,}", d):
        s = m.group().decode("ascii")
        if "/" in s or "\\" in s:
            s = s.replace("\\", "/")
            for a in ancestors(os.path.dirname(s)):
                out.add(a + "/" if a else "")
    return out


CURATED = [
    "", "model/", "models/", "model/tex/", "model/texture/", "model/model/",
    "texture/", "textures/", "tex/", "map/", "maps/", "scene/", "world/",
    "obj/", "mesh/", "meshes/", "res/", "resource/", "data/", "image/",
    "images/", "img/", "pic/", "pic2d/", "com2d/", "sysfx/", "fx/", "effect/",
    "plant/", "plants/", "prop/", "props/", "static/", "staticmesh/", "art/",
    "game/", "client/", "bin/", "sysbin/", "gbox/", "net/", "pub/",
]


def build_prefixes():
    out = set(CURATED)
    out |= prefixes_from_names_json()
    out |= prefixes_from_scripts()
    out |= prefixes_from_binary()
    # second level: prefix a generic dir with every first-level dir
    first = {p for p in out if p.count("/") == 1}
    for f in first:
        for g in ("model/", "models/", "texture/", "tex/", "obj/", "mesh/"):
            out.add(f + g)
    return sorted(out)


# --------------------------------------------------------------------------- #
# 3. sweep
# --------------------------------------------------------------------------- #
def main():
    imgs = image_hashes()
    names = texture_names()
    prefs = build_prefixes()
    print(f"image entries      : {len(imgs)}")
    print(f"texture names      : {len(names)}")
    print(f"prefix candidates  : {len(prefs)}")
    print()

    hits = []
    for p in prefs:
        gbk_state = hash_state(p.encode("gbk"))
        for n in names:
            h = continue_hash(gbk_state, n.encode("gbk"))
            if h in imgs:
                hits.append(("gbk", p + n, h, imgs[h]))
        # wide-char reading is only meaningful for the decoded form
        hu = hash_name_utf16(p + n)
        if hu in imgs:
            hits.append(("uni", p + n, hu, imgs[hu]))

    if not hits:
        print("no match")
        return
    seen = set()
    for enc, path, h, arc in hits:
        k = (enc, path)
        if k in seen:
            continue
        seen.add(k)
        print(f"MATCH [{enc:3}] {path:40} {h:08x} -> {arc}")
    print(f"\n{len(seen)} distinct matches")


if __name__ == "__main__":
    main()
