"""Probe netpak: are .53wf/.cmz model names the directory keys?"""
import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gpak import GpakArchive, hash_name

a = GpakArchive(r"D:\Games\Chrono Tales\gbox\lm_en\netpak.gpak")
by_hash = {e.name_hash: e for e in a.entries}

# GBK runs of 2+ chars that are followed by a NUL-ish / not an extension
gbk_run = re.compile(rb"(?:[\x81-\xfe][\x40-\xfe]){1,20}")
ext_run = re.compile(rb"(?:[\x81-\xfe][\x40-\xfe]|[A-Za-z0-9_]){1,40}\.(?:png|jpg|tga|bmp)")

names = set()
tex = set()
for e in a.entries:
    blob, _ = a.read_chain(e.start_page)
    if not (blob.startswith(b"53WF") or blob.startswith(b"CMZ\x00")):
        continue
    for m in ext_run.finditer(blob):
        try:
            tex.add(m.group().decode("gbk"))
        except UnicodeDecodeError:
            pass
    for m in gbk_run.finditer(blob):
        try:
            s = m.group().decode("gbk")
        except UnicodeDecodeError:
            continue
        if 2 <= len(s) <= 24:
            names.add(s)

print(f"{len(names)} GBK tokens, {len(tex)} texture refs from models")
print("sample tokens:", sorted(names)[:20])
print("sample textures:", sorted(tex)[:10])

EXTS = ["", ".53wf", ".cmz", ".png", ".jpg"]
PRE = ["", "model/", "model2d/", "char/", "obj/", "img/", "res/", "model/char/"]
hits = {}
for base in list(names) + list(tex):
    for p in PRE:
        for x in EXTS:
            v = p + base + x
            h = hash_name(v, 0)
            if h in by_hash and by_hash[h].name is None:
                hits[v] = by_hash[h]
print(f"\n{len(hits)} new matches")
for v, e in sorted(hits.items(), key=lambda t: t[1].index)[:30]:
    print(f"  {v:44s} e{e.index} size={e.size}")
