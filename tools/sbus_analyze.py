#!/usr/bin/env python3
"""Statistical structure analysis of .cmz `sbus` skin payloads.

Three probes, run per blob:

  1. period   - autocorrelation of the byte stream; a true record stride shows
                a spike at that stride and its multiples.
  2. tokens   - the most frequent 2/4-byte tokens and the histogram of the
                distances between consecutive occurrences (constant distance
                => fixed record stride).
  3. fields   - for each candidate stride and field offset, report whether the
                field looks bone-like (< bonecap), weight-like (<= 10000),
                or sentinel-like (0x7fff / 0xffff).
"""
import collections
import pathlib
import struct
import sys


def chunk_bounds(d):
    hits = []
    for i in range(8, len(d) - 4):
        s = d[i:i + 4][::-1]
        if s in (b"skel", b"MESH", b"MAP1", b"END."):
            if not hits or i >= hits[-1][0] + 4:
                hits.append((i, s.decode("latin1")))
    return [(o, t, hits[k + 1][0] if k + 1 < len(hits) else len(d))
            for k, (o, t) in enumerate(hits)]


def blobs(d, start, end):
    pos, i, out = [], start, []
    while True:
        j = d.find(b"sbus", i, end)
        if j < 0:
            break
        pos.append(j)
        i = j + 1
    for k, s in enumerate(pos):
        e = pos[k + 1] if k + 1 < len(pos) else end
        out.append(d[s:e])
    return out


def autocorr(b):
    """Normalised agreement of the byte stream with itself shifted by `lag`."""
    n = len(b)
    out = {}
    for lag in range(1, min(97, n // 2)):
        m = n - lag
        same = sum(1 for i in range(0, m, 1) if b[i] == b[i + lag])
        out[lag] = same / m
    return out


def token_distances(b, w):
    pos = collections.defaultdict(list)
    for i in range(len(b) - w + 1):
        pos[b[i:i + w]].append(i)
    res = []
    for tok, ps in pos.items():
        if len(ps) < 8:
            continue
        deltas = collections.Counter(ps[k + 1] - ps[k] for k in range(len(ps) - 1))
        res.append((len(ps), tok, deltas.most_common(4)))
    res.sort(reverse=True)
    return res[:6]


def field_report(pay, bonecap):
    """u16 and u8 views: which (stride, offset) fields look meaningful."""
    us = [struct.unpack_from("<H", pay, o)[0]
          for o in range(0, len(pay) - len(pay) % 2, 2)]
    n = len(us)
    rows = []
    for g in range(2, 13):
        for p in range(g):
            f = us[p::g]
            if not f:
                continue
            bone = sum(1 for v in f if v < bonecap) / len(f)
            wt = sum(1 for v in f if v <= 10000) / len(f)
            sent = sum(1 for v in f if v in (0x7fff, 0x7ffe, 0xffff, 0xfffe)) / len(f)
            if bone > 0.98 or sent > 0.5 or (wt > 0.98 and max(f) > 1000):
                rows.append((g, p, f"bone={bone:.0%} w<=1e4={wt:.0%} "
                                f"sentinel={sent:.0%} max={max(f)} n={len(f)}"))
    return rows


def main():
    files = sys.argv[1:] or sorted(
        str(p) for p in pathlib.Path("extracted/netpak").rglob("*.cmz"))
    for fn in files:
        p = pathlib.Path(fn)
        d = p.read_bytes()
        mesh = next((b for b in chunk_bounds(d) if b[1] == "MESH"), None)
        print("=" * 78)
        print(p.stem)
        if not mesh:
            continue
        off, _, end = mesh
        for k, blob in enumerate(blobs(d, off + 8, end)):
            if len(blob) < 32:
                continue
            hdr = struct.unpack_from("<7I", blob, 4)
            pay = blob[32:]
            cap = hdr[6]
            print(f"\n  sbus[{k}] pay={len(pay)} part={hdr[0]} A={hdr[2]} "
                  f"B={hdr[3]} cap={cap}")
            ac = autocorr(pay)
            top = sorted(ac.items(), key=lambda kv: -kv[1])[:5]
            print("    autocorr lags : " + "  ".join(f"{l}:{v:.2f}" for l, v in top))
            for cnt, tok, dl in token_distances(pay, 4):
                ds = " ".join(f"{x}x{d}" for d, x in dl)
                print(f"    tok {tok.hex()} n={cnt} deltas[{ds}]")
            for cnt, tok, dl in token_distances(pay, 2)[:3]:
                ds = " ".join(f"{x}x{d}" for d, x in dl)
                print(f"    u16 {tok.hex()} n={cnt} deltas[{ds}]")
            for g, pp, s in field_report(pay, cap)[:8]:
                print(f"    u16 stride={g} field[{pp}] {s}")


if __name__ == "__main__":
    main()
