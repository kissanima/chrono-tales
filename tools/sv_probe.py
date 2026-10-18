#!/usr/bin/env python3
"""Probe extracted/netpak/SysFileVer.var for its compression/encryption layer.

Context:
  - SysFileVer.var is entry #129 in netpak.gpak.  After the GPAK per-page XOR
    (sub_416BB0) is undone, the logical file is still high-entropy garbage.
  - 49 other .var files in netpak decode (via GPAK XOR alone) into a valid ZIP
    ("PK\\x03\\x04"); this one does not -> an extra transform is applied.
  - The engine has an "UnZipVar" routine (errors: zipformat / pass) and embeds
    zlib 1.0.4/1.1.3/1.1.4, so a ZIP+zlib container is the prime suspect.

This script tries, empirically, every cheap transform and reports which (if any)
reveals a known container magic (PK zip / gzip / zlib / xz / bzip2 / lzma) or a
readable string layout.
"""
import struct, zlib, bz2, lzma, io, sys, os

SRC = "extracted/netpak/SysFileVer.var"
OUT = "extracted/var"

def log(*a):
    print(*a, flush=True)

data = open(SRC, "rb").read()
N = len(data)
log(f"size = {N} bytes")

# ---------------------------------------------------------------- header view
hdr = struct.unpack_from("<4I", data, 0)
log("first 16 bytes hex :", data[:16].hex())
log("u32 LE @0,4,8,12   :", [hex(x) for x in hdr])
log("  as BE 4-byte     :", data[:4].hex(), data[4:8].hex(), data[8:12].hex())
# entropy / byte stats
from collections import Counter
cnt = Counter(data)
nul = cnt.get(0, 0)
printable = sum(v for b, v in cnt.items() if 9 <= b <= 13 or 32 <= b < 127)
log(f"NULs={nul} ({nul/N:.1%})  printable={printable} ({printable/N:.1%})")

def try_decompress(buf, label):
    """Try every decompressor at the front of buf; return (name, out) or None."""
    res = []
    # zlib (with header)
    try:
        out = zlib.decompress(buf)
        res.append(("zlib", out))
    except Exception:
        pass
    # raw deflate
    try:
        d = zlib.decompressobj(-15)
        out = d.decompress(buf)
        if out:
            res.append(("raw-deflate", out))
    except Exception:
        pass
    # gzip
    try:
        out = gzip.decompress(buf) if False else None
    except Exception:
        pass
    try:
        import gzip
        out = gzip.decompress(buf)
        res.append(("gzip", out))
    except Exception:
        pass
    # lzma / xz
    try:
        out = lzma.decompress(buf)
        res.append(("lzma/xz", out))
    except Exception:
        pass
    # bzip2
    try:
        out = bz2.decompress(buf)
        res.append(("bzip2", out))
    except Exception:
        pass
    for name, out in res:
        log(f"  [{label}] {name} OK -> {len(out)} bytes, head {out[:8].hex()}")
    return res

# (a) decompression at offsets 0,4,8,12,16 on raw file
log("\n=== (a) raw decompression at skips 0/4/8/12/16 ===")
for skip in (0, 4, 8, 12, 16):
    try_decompress(data[skip:], f"raw skip{skip}")

# ---------------------------------------------------------------- PK scan
def has_pk(buf):
    return buf.find(b"PK\x03\x04") >= 0 or buf.find(b"PK\x05\x06") >= 0

log("\n=== (b) GPAK LFSR applied to WHOLE file (documented seeds + grid) ===")
LFSR_MASK = 0xE7BD2160
def gpak_stream(seed, n):
    out = bytearray(n)
    k = seed & 0xFFFFFFFF
    for i in range(n):
        k = (((k << 1) & 0xFFFFFFFF) & LFSR_MASK) + 1
        out[i] = k & 0xFF
    return bytes(out)
seeds = [134, 129, 0xd7e9272b, 0x5243ee5f, 1380183647, 0, 1, 0x12345678, 0x57575757]
best = None
for s in seeds:
    ks = gpak_stream(s, N)
    dec = bytes(a ^ b for a, b in zip(data, ks))
    if has_pk(dec):
        log(f"  seed {s:#x}: FOUND PK at {dec.find(b'PK')}")
    # measure printable ratio of first 64 bytes
    pr = sum(1 for b in dec[:256] if 32 <= b < 127) / 256
    if best is None or pr > best[1]:
        best = (s, pr)
log(f"  best printable-ratio seed {best[0]:#x} = {best[1]:.2%} (none yielded PK)")

# reverse-byte-order variant of the file
rev = data[::-1]
log("  reversed whole file: PK present? ", has_pk(rev))
try_decompress(rev, "reversed")

# ---------------------------------------------------------------- XOR probes
log("\n=== (c) XOR transforms ===")
# (c1) single-byte XOR brute (look for PK at offset 0)
found_single = None
for k in range(256):
    dec = bytes(b ^ k for b in data)
    if dec[:4] == b"PK\x03\x04":
        found_single = k
        log(f"  single-byte XOR key {k:#x} -> PK at 0")
        break
if found_single is None:
    log("  single-byte XOR: no key reveals PK at offset 0 (not a 1-byte repeating cipher)")

# (c2) XOR with first 16 bytes as repeating key, then decompress
key16 = data[:16]
dec16 = bytes(data[i] ^ key16[i % 16] for i in range(N))
log("  XOR first16 repeating: PK? ", has_pk(dec16))
try_decompress(dec16, "xor16")

# (c3) XOR with the ASCII string "SysFileVer" (and null-terminated) repeating
for ks in (b"SysFileVer", b"SysFileVer\x00", b"sysfilever"):
    dec = bytes(data[i] ^ ks[i % len(ks)] for i in range(N))
    if has_pk(dec):
        log(f"  XOR key {ks!r}: FOUND PK")
    try_decompress(dec, f"xor:{ks.decode('latin1')}")

# (c4) RC4 with candidate keys
def rc4(key, buf):
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) & 0xFF
        S[i], S[j] = S[j], S[i]
    out = bytearray(len(buf))
    i = j = 0
    for k in range(len(buf)):
        i = (i + 1) & 0xFF
        j = (j + S[i]) & 0xFF
        S[i], S[j] = S[j], S[i]
        out[k] = buf[k] ^ S[(S[i] + S[j]) & 0xFF]
    return bytes(out)

rc4_keys = {
    "SysFileVer": b"SysFileVer",
    "first16": data[:16],
    "hash_d7e9272b_LE": struct.pack("<I", 0xd7e9272b),
    "hash_d7e9272b_BE": struct.pack(">I", 0xd7e9272b),
    "hash2_5243ee5f_LE": struct.pack("<I", 0x5243ee5f),
    "namehash_combo": struct.pack("<II", 0xd7e9272b, 0x5243ee5f),
}
log("\n=== (c4) RC4 candidates ===")
for name, key in rc4_keys.items():
    dec = rc4(key, data)
    if has_pk(dec):
        log(f"  RC4 key {name}: FOUND PK at {dec.find(b'PK')}")
    else:
        pr = sum(1 for b in dec[:256] if 32 <= b < 127) / 256
        log(f"  RC4 key {name}: no PK (printable {pr:.2%})")

# ---------------------------------------------------------------- zip sniff
log("\n=== (d) ZIP member scan (raw + best candidates) ===")
def zip_report(buf, label):
    idx = buf.find(b"PK\x03\x04")
    if idx < 0:
        log(f"  {label}: no local file header")
        return
    log(f"  {label}: PK\\x03\\x04 at {idx}")
    n = buf.find(b"PK\x01\x02")  # central dir
    e = buf.find(b"PK\x05\x06")  # end of central dir
    log(f"     central dir PK\\x01\\x02 at {n}, EOCD PK\\x05\\x06 at {e}")

zip_report(data, "raw")
zip_report(dec16, "xor16")
for name, key in rc4_keys.items():
    zip_report(rc4(key, data), f"rc4:{name}")

# ---------------------------------------------------------------- string scan
log("\n=== (e) length-prefixed ASCII string scan ===")
def scan_strings(buf, label, maxlen=200):
    found = 0
    for o in range(0, len(buf) - 4):
        ln = struct.unpack_from("<H", buf, o)[0]
        if 4 <= ln <= maxlen and o + 2 + ln <= len(buf):
            s = buf[o + 2:o + 2 + ln]
            if all(32 <= c < 127 for c in s):
                found += 1
                if found <= 5:
                    log(f"    u16 @{o:#x} len {ln}: {s[:60]!r}")
    for o in range(0, len(buf) - 5):
        ln = struct.unpack_from("<I", buf, o)[0]
        if 4 <= ln <= maxlen and o + 4 + ln <= len(buf):
            s = buf[o + 4:o + 4 + ln]
            if all(32 <= c < 127 for c in s):
                if found <= 5:
                    log(f"    u32 @{o:#x} len {ln}: {s[:60]!r}")
    log(f"  {label}: u16-ASCII matches={found}")
scan_strings(data, "raw")

log("\nDONE. No container magic recovered -> see SYSFILEVER.md.")
