"""Propagate known gpak filenames through `#include` directives.

Scripts are stored at their real paths, and each one names its siblings with
paths relative to its own directory ("../bag/bagcom.gbc").  Starting from the
names already recovered by hash-guessing, we can resolve those references,
hash them, and match them against the still-unnamed directory entries.  Repeat
to a fixpoint.
"""

import sys, os, re, posixpath, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gpak import GpakArchive, hash_name

PAKS = [r"D:\Games\Chrono Tales\gbox\lm_en\netpak.gpak",
        r"D:\Games\Chrono Tales\gbox\lm_en\sysnet.gpak"]
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "names.json")

archives = [GpakArchive(p) for p in PAKS]
by_hash = {}
for a in archives:
    for e in a.entries:
        by_hash[e.name_hash] = (a, e)

names = {int(k): v for k, v in json.load(open(CACHE)).items()}
print(f"starting with {len(names)} known names of {len(by_hash)} entries")

INCLUDE = re.compile(rb'#include\s*["<]([^">\r\n]{2,120})[">]')
# quoted asset references that look like relative paths, e.g. "img/ui.png"
QUOTED = re.compile(rb'"([A-Za-z0-9_\-./]{3,80}\.(?:gbc|png|jpg|txt|var|csv|xml))"')

def candidates_for(name, blob):
    """Yield plausible sibling paths referenced by this script."""
    base = posixpath.dirname(name.replace("\\", "/"))
    out = set()
    for m in INCLUDE.finditer(blob):
        ref = m.group(1).decode("gbk", "ignore").replace("\\", "/").strip()
        if not ref:
            continue
        out.add(posixpath.normpath(posixpath.join(base, ref)))
        out.add(ref.lstrip("./"))
    for m in QUOTED.finditer(blob):
        ref = m.group(1).decode("gbk", "ignore").replace("\\", "/").strip()
        out.add(posixpath.normpath(posixpath.join(base, ref)))
        out.add(ref.lstrip("./"))
    return {p for p in out if p and not p.startswith("..")}

rounds = 0
while True:
    rounds += 1
    gained = {}
    for h, name in list(names.items()):
        a, e = by_hash[h]
        blob, _ = a.read_chain(e.start_page)
        if b"#include" not in blob and b'"' not in blob:
            continue
        for cand in candidates_for(name, blob):
            ch = hash_name(cand, 0)
            if ch in by_hash and ch not in names and ch not in gained:
                gained[ch] = cand
    if not gained:
        break
    names.update(gained)
    print(f"  round {rounds}: +{len(gained)} -> {len(names)}")
    for ch, cand in list(gained.items())[:6]:
        print(f"      {cand}")

print(f"\ntotal {len(names)} / {len(by_hash)} entries named ({100*len(names)/len(by_hash):.0f}%)")
json.dump({str(k): v for k, v in sorted(names.items())}, open(CACHE, "w"), indent=1)
print(f"wrote {CACHE}")
