"""Chrono Tales (.gpak) extraction pipeline.

Usage:
    python gpak_extract.py list   <archive.gpak>
    python gpak_extract.py verify <archive.gpak>
    python gpak_extract.py names  <archive.gpak> [more...]     build names.json
    python gpak_extract.py wrap   <archive.gpak>               tally compression wrappers
    python gpak_extract.py extract <archive.gpak> <outdir>     extract everything

Filenames are not stored in the archive.  The directory keeps only a 32-bit
MPQ-style hash of the path, so names are recovered by hashing candidate strings
harvested from the decoded payloads (see `names`).  Entries whose name cannot be
recovered are written as unknown/<hash>.<sniffed-ext>.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gpak import (GpakArchive, WRAP_CHECKED, WRAP_STORED, WRAP_ZLIB,
                 hash_name, sniff, wrap_tag)

HERE = os.path.dirname(os.path.abspath(__file__))
NAME_CACHE = os.path.join(HERE, "names.json")

STR_RE = re.compile(rb"[\x20-\x7e]{3,90}")
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-./]{3,90}")

MAGIC_SCAN = [
    (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"),
    (b"PK\x03\x04", "zip"), (b"RIFF", "riff"), (b"OggS", "ogg"),
    (b"BM", "bmp"), (b"DDS ", "dds"), (b"ID3", "mp3"),
    (b"53WF", "53wf"), (b"CMZ\x00", "cmz"), (b"BMVA", "bmva"),
    (b"GPAK", "gpak"), (b"KAPG", "gpak"),
]


def deep_sniff(blob: bytes) -> str:
    """Sniff the type, tolerating a small container prefix and GBK text."""
    for off in range(0, min(17, len(blob))):
        for magic, ext in MAGIC_SCAN:
            if blob.startswith(magic, off):
                return ext
    head = blob[:4096]
    if not head:
        return "bin"
    # GBK / ASCII script text: GBox scripts are plain source (.gbc)
    try:
        text = head.decode("gbk")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        good = sum(1 for ch in text
                   if ch in "\r\n\t" or 32 <= ord(ch) < 127 or 0x4E00 <= ord(ch) <= 0x9FFF)
        if good / len(text) > 0.90:
            markers = ("#include", "class ", "style ", "{", "}", "//", ";")
            if sum(1 for m in markers if m in text) >= 3:
                return "gbc"
            return "txt"
    ascii_ok = sum(1 for b in head if 9 <= b <= 13 or 32 <= b < 127)
    if ascii_ok / len(head) > 0.85:
        return "txt"
    return "bin"


# --------------------------------------------------------------------------- #
# name recovery
# --------------------------------------------------------------------------- #

def harvest(archives) -> tuple[set, "Counter"]:
    import collections
    tokens, dirs = set(), collections.Counter()
    for a in archives:
        for e in a.entries:
            blob, _ = a.read_chain(e.start_page)
            for m in STR_RE.finditer(blob):
                s = m.group().decode("ascii", "ignore")
                for tok in TOKEN_RE.findall(s):
                    tok = tok.replace("\\", "/").strip("/")
                    if not tok:
                        continue
                    tokens.add(tok)
                    for part in tok.split("/")[:-1]:
                        if part and part not in (".", ".."):
                            dirs[part] += 1
    return tokens, dirs


def build_names(archives) -> dict:
    lookup = {}
    for a in archives:
        for e in a.entries:
            lookup[e.name_hash] = (a, e)

    tokens, dirs = harvest(archives)
    print(f"  {len(tokens)} tokens, {len(dirs)} directory names")

    variants = set()
    for t in tokens:
        variants.add(t)
        parts = t.split("/")
        for i in range(1, len(parts)):
            variants.add("/".join(parts[i:]))
    prefixes = ["", "sysbin/", "com2d/", "res/", "sys/", "img/", "model/",
                "snd/", "ui/", "tabedit/", "country/en/", "webobj/"]
    prefixes += [d + "/" for d, _ in dirs.most_common(80)]
    seen = set()
    prefixes = [p for p in prefixes if not (p in seen or seen.add(p))]
    basenames = {t.split("/")[-1] for t in tokens
                 if "." in t.split("/")[-1] and len(t.split("/")[-1]) <= 48}
    for b in basenames:
        for p in prefixes:
            variants.add(p + b)
    print(f"  {len(variants)} hash candidates")

    names = {}
    for v in variants:
        h = hash_name(v, 0)
        if h in lookup:
            names[h] = v
    print(f"  resolved {len(names)} of {len(lookup)} entries")
    return names


def load_names() -> dict:
    if os.path.exists(NAME_CACHE):
        with open(NAME_CACHE, "r", encoding="utf-8") as fh:
            return {int(k): v for k, v in json.load(fh).items()}
    return {}


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_list(path):
    a = GpakArchive(path)
    names = load_names()
    print(f"{path}")
    print(f"  version={a.version} entries={a.entry_count} capacity={a.capacity} "
          f"pages={a.pages_total} dirPages={a.dir_pages} freePages={a.free_pages}")
    print(f"  {'idx':>4} {'size':>9} {'page':>5} {'hash':>10}  name")
    for e in a.entries:
        print(f"  {e.index:4d} {e.size:9d} {e.start_page:5d} {e.name_hash:#010x}  "
              f"{names.get(e.name_hash, '?')}")


def cmd_verify(path):
    a = GpakArchive(path)
    ok, bad = a.verify()
    crc_ok = crc_bad = 0
    for e in a.entries:
        _, order = a.read_chain(e.start_page)
        for p in order:
            if a.page_crc_ok(p):
                crc_ok += 1
            else:
                crc_bad += 1
    print(f"{path}: entries ok={ok} bad={bad} | page CRC ok={crc_ok} bad={crc_bad}")
    return bad == 0 and crc_bad == 0


def cmd_names(paths):
    archives = [GpakArchive(p) for p in paths]
    names = build_names(archives)
    existing = load_names()
    existing.update(names)
    with open(NAME_CACHE, "w", encoding="utf-8") as fh:
        json.dump({str(k): v for k, v in sorted(existing.items())}, fh, indent=1)
    print(f"  wrote {NAME_CACHE} ({len(existing)} names)")


def safe(name: str) -> str:
    name = name.replace("\\", "/").lstrip("/")
    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
    cleaned = []
    for p in parts:
        cleaned.append(re.sub(r'[<>:"|?*\x00-\x1f]', "_", p))
    return "/".join(cleaned)


def cmd_extract(path, outdir):
    a = GpakArchive(path)
    names = load_names()
    tag = os.path.splitext(os.path.basename(path))[0]
    root = os.path.join(outdir, tag)
    os.makedirs(root, exist_ok=True)

    manifest = []
    named = 0
    for e in a.entries:
        blob, order = a.read_chain(e.start_page)
        data = blob[:e.size]
        name = names.get(e.name_hash)
        ext = deep_sniff(data)
        if name:
            rel = safe(name)
            named += 1
        else:
            rel = f"unknown/{e.name_hash:08x}.{ext}"
        dest = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
        manifest.append(dict(index=e.index, name=name, path=rel, size=e.size,
                             start_page=e.start_page, pages=len(order),
                             hash=f"{e.name_hash:08x}", mtime=e.mtime,
                             hash2=f"{e.name_hash2:08x}", detected=ext))

    with open(os.path.join(root, "_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    import csv
    with open(os.path.join(root, "_manifest.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(manifest[0].keys()))
        w.writeheader()
        w.writerows(manifest)

    print(f"{path}: {len(manifest)} files -> {root} ({named} named, "
          f"{len(manifest)-named} unnamed)")
    return manifest


def cmd_wrap(path):
    """Tally the per-file compression wrappers (GPAK_FORMAT.md §2.7)."""
    a = GpakArchive(path)
    tally = {}
    examples = {}
    for e in a.entries:
        data = a.read_entry(e)
        tag = wrap_tag(data)
        key = "none" if tag is None else f"{tag:#010x}"
        tally[key] = tally.get(key, 0) + 1
        examples.setdefault(key, (e.name_hash, len(data)))
    label = {f"{WRAP_ZLIB:#010x}": "compressed (ZIP, entry 'varbin')",
             f"{WRAP_STORED:#010x}": "stored verbatim",
             f"{WRAP_CHECKED:#010x}": "stored + additive fold",
             "none": "raw file, no wrapper"}
    print(f"{path}: {a.entry_count} entries")
    for k, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        h, ln = examples[k]
        print(f"  {k:>12s}  {label.get(k, '?'):<32s} {n:>4d}   "
              f"e.g. {h:08x} ({ln} B)")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "list":
        cmd_list(argv[2])
    elif cmd == "verify":
        return 0 if cmd_verify(argv[2]) else 1
    elif cmd == "names":
        cmd_names(argv[2:])
    elif cmd == "wrap":
        cmd_wrap(argv[2])
    elif cmd == "extract":
        outdir = argv[3] if len(argv) > 3 else os.path.join(HERE, "extracted")
        cmd_extract(argv[2], outdir)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
