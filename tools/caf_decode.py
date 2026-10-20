#!/usr/bin/env python3
"""caf_decode.py - decode Chrono Tales .caf character animations (Cal3D).

The five shipped `.caf` files are stock libcal3d `CalCoreAnimation`
documents: one clip per file, a flat array of per-bone tracks, and a flat
array of 32-byte keyframes per track.  GBox did **not** invent a container
here — unlike `.cmz`, there are no reversed tags and no packed headers.

Loader: VA `0x5ABF70` (CalCoreAnimation::load), which calls the per-track
parser at VA `0x5ABD90` (CalCoreTrack::load).  Both live in
`E:\\GBox2\\src\\client\\cal3d\\src\\coreanimation.cpp` — the source path and
the `"loadCoreAnimation track delay err"` message are at file offset
`0x36a4c4`.  The magic dword `'CAF\\0'` is compared against the literal at
VA `0x6D4620` (`mov ecx, 0x6d4620; cmp ebx, [ecx]` at `0x5ABFBB`).

File layout
-----------

    CalCoreAnimation header, 16 bytes
    0x00  char[4]  "CAF\\0"
    0x04  u32      version            (accepted range 699..1100 at 0x5ABFDA;
                                       the shipped files are all 1000)
    0x08  f32      duration, seconds  (must be > 0 at 0x5AC010)
    0x0C  u32      n_tracks

    then n_tracks x track:
    u32  bone_id                     (must be >= 0 at 0x5ABDC4)
    u32  n_keys                      (must be > 0 at 0x5ABDEE)
    n_keys x 32-byte keyframe:
        f32  time
        f32  tx, ty, tz               centimetres
        f32  qx, qy, qz, qw           bone-local rotation, **inverse**

Field roles are read off the instructions, not inferred:

  * `mov [esi], ecx` at `0x5AC004` takes the header word at file +8 into
    animation+0, and `fld [esi]; fcomp 0.0` at `0x5AC010` treats it as a
    float that must be strictly positive - so +8 is the duration.
  * the header word at +12 becomes n_tracks (`mov [esi+0xc]`, `0x5AC036`)
    and is the loop bound at `0x5AC17C`.
  * the track parser reads bone_id, then n_keys, then allocates
    `n_keys << 5` bytes (`0x5ABE00`) and `rep movs` the keys verbatim, so a
    key is exactly 32 bytes.
  * it then converts only the three floats at key+4, +8, +0xC by `* 0.01`
    (`fmul [0x6D0738]` at `0x5ABE94`/`0x5ABE9D`/`0x5ABEB3`) and copies the
    four at key+0x10..+0x1C untouched - so +0 is the time, +4 is the
    translation in centimetres, +0x10 is the rotation.
  * after the key loop it sets `track+0xc` (the playback step) to
    `keys[1].time - keys[0].time` when n_keys > 2, else a constant 0.03333
    (`0x5ABE4C`..`0x5ABE5D`).  The animation loader then requires every
    track to agree with track 0 on that value to within 0.001, else it
    raises `"loadCoreAnimation track delay err"` (`0x5AC0E4`).  That assert
    is a structural check we can reuse: it is why all 43 tracks share one
    key grid.

Notes that contradict earlier guesses:

  * there is **no** ticksPerSecond field.  `1000` at +4 is the Cal3D
    format version (`CAF_VERSION`), and the loader rejects anything outside
    699..1100.
  * +8 is **not** n_bones; it is a float duration.  `0x3E888889` is
    `0.266666681` = 8/30 s and `0x3F000000` is `0.5` = 15/30 s.  Reading
    either as an integer bone count is what produced the "43 bones vs 0.5"
    ambiguity in the brief.
  * +12 is **not** n_anims = 5; it is n_tracks = 43.  A `.caf` is one clip.

Keyframe times are `i / 30` to within 1.4e-8 (stored as f32), the step is
1/30 s, and the last key time equals the header duration exactly.

Pose convention (same as `.cmz`, §4.4): rotation is the inverse of the
bone->parent rotation, so `R_world[i] = R_world[parent] * conj(q[i])`, and
translations are centimetres (`* 0.01`).  Key 0 of `jumpstart.caf` is
byte-identical to the 43-bone rig's bind pose for all 43 bones; the other
clips agree on the non-animated bones and differ only where they animate,
which is what binds the clips to the rig.

Usage
-----
    python caf_decode.py                 # decode -> extracted/models/caf/*.json
    python caf_decode.py check           # parse all 5, assert byte-exact, no output
    python caf_decode.py check --json    # ... print a table
"""
import argparse
import json
import pathlib
import struct
import sys
import zipfile

MAGIC = b"CAF\x00"
VERSION_MIN, VERSION_MAX = 0x2BB, 0x44C        # VA 0x5ABFDA / 0x5ABFE5
HEADER_SIZE = 16
KEY_SIZE = 32                                   # n_keys << 5 at 0x5ABE00
SCALE = 0.01                                    # VA 0x6D0738
DEFAULT_DELAY = struct.unpack("<f", struct.pack("<I", 0x3D08850A))[0]  # 0.03333
DELAY_TOL = 0.001                               # 0x6D4628, used at 0x5AC0EC

CAF_ZIP = "extracted/netpak/action/e41c7990.zip"


class CafError(Exception):
    pass


# ------------------------------------------------------------------ parsing
def decode(data, name="<mem>"):
    """Parse one .caf.  Raises CafError on any structural violation."""
    if len(data) < HEADER_SIZE:
        raise CafError(f"{name}: {len(data)} bytes is shorter than the header")
    if data[:4] != MAGIC:
        raise CafError(f"{name}: bad magic {data[:4]!r}")
    version, duration, n_tracks = struct.unpack_from("<If I".replace(" ", ""),
                                                     data, 4)
    if not (VERSION_MIN <= version <= VERSION_MAX):
        raise CafError(f"{name}: version {version} outside "
                       f"[{VERSION_MIN}, {VERSION_MAX}]")
    if not duration > 0.0:
        raise CafError(f"{name}: duration {duration} is not positive")
    if n_tracks <= 0:
        raise CafError(f"{name}: n_tracks {n_tracks}")

    off = HEADER_SIZE
    tracks = []
    for _ in range(n_tracks):
        if off + 8 > len(data):
            raise CafError(f"{name}: truncated at track header 0x{off:x}")
        bone, n_keys = struct.unpack_from("<II", data, off)
        off += 8
        if bone < 0:
            raise CafError(f"{name}: negative bone id {bone}")
        if n_keys <= 0:
            raise CafError(f"{name}: track {bone} has {n_keys} keys")
        end = off + n_keys * KEY_SIZE
        if end > len(data):
            raise CafError(f"{name}: track {bone} needs {n_keys * KEY_SIZE} "
                           f"bytes, only {len(data) - off} left")
        keys = []
        for k in range(n_keys):
            t, tx, ty, tz, qx, qy, qz, qw = struct.unpack_from(
                "<8f", data, off + k * KEY_SIZE)
            keys.append((t, tx, ty, tz, qx, qy, qz, qw))
        off = end
        tracks.append(dict(bone=bone, n_keys=n_keys, keys=keys))

    return dict(name=name, size=len(data), version=version,
                duration=duration, n_tracks=n_tracks,
                end=off, tracks=tracks)


# --------------------------------------------------------------- rig binding
def load_rigs(root="extracted/models/cmz"):
    """{stem: bones} for every decoded .cmz skeleton.

    The stem is whatever `cmz.py` last wrote, which may predate an
    `apply_names.py` rename: `2682c5d5.json` still records
    `extracted/netpak/unknown/2682c5d5.cmz`, but the same rig now lives at
    `extracted/netpak/model/b056501a.cmz`.  So the *stem* is only a label here
    - the binding decision is made on bone geometry, never on the name.  See
    `rig_aliases()` for the recovered stem -> current-filename mapping.
    """
    out = {}
    for p in sorted(pathlib.Path(root).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(d, dict) and d.get("bones"):
            out[p.stem] = d["bones"]
    return out


def rig_aliases(root="extracted/models/cmz", cmz_dir="extracted/netpak/model"):
    """{json stem: current on-disk .cmz name} for rigs whose stem is stale.

    Matches on skeleton content (bone names + parents), because that is the
    only identity that survives a rename.  Returns **only unambiguous** stems:
    the seven 119-bone rigs share one identical skeleton and differ only in
    their skinned parts, so content cannot tell them apart and they are
    deliberately omitted rather than guessed.
    """
    stems = {}
    for p in sorted(pathlib.Path(root).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not (isinstance(d, dict) and d.get("bones")):
            continue
        if pathlib.Path(d.get("path", "")).exists():
            continue                      # JSON is still current
        stems[p.stem] = [(b["name"], b["parent"]) for b in d["bones"]]
    if not stems:
        return {}
    on_disk = {}
    for p in sorted(pathlib.Path(cmz_dir).glob("*.cmz")):
        try:
            rig = cmz_bones(p)
        except Exception:
            continue
        if rig:
            on_disk[p.stem] = [(b["name"], b["parent"]) for b in rig]
    out = {}
    for stem, want in stems.items():
        hits = [name for name, key in on_disk.items() if key == want]
        if len(hits) == 1:
            out[stem] = hits[0]
    return out


def cmz_bones(path):
    """Bones of a .cmz, via the repo's own parser (`tools/cmz.py`)."""
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from cmz import parse_skel                     # noqa: E402
    from sbus_decode import chunk_bounds           # noqa: E402
    d = pathlib.Path(path).read_bytes()
    sk = next((b for b in chunk_bounds(d) if b[1] == "skel"), None)
    if not sk:
        return None
    bones, _ = parse_skel(d, sk[0] + 4)
    return bones


def rig_score(anim, bones):
    """(#bones whose key 0 equals the bind pose, #bones compared).

    A track's first keyframe should reproduce the rig's bind pose wherever
    the clip does not animate that bone, so the rig a clip was authored
    against scores high and every other rig scores ~0.  This is what ties
    the clips to a skeleton - the bone ids alone would match any rig with
    >= n_tracks bones.
    """
    hit = 0
    for tr in anim["tracks"]:
        b = tr["bone"]
        if b >= len(bones):
            return 0, 0
        t = tr["keys"][0]
        ref = bones[b]
        dp = max(abs(t[1 + i] - ref["offset"][i]) for i in range(3))
        dr = max(abs(t[4 + i] - ref["rot"][i]) for i in range(4))
        if dp < 1e-3 and dr < 1e-4:
            hit += 1
    return hit, len(anim["tracks"])


def resolve_rig(anim, rigs):
    """-> (stem, hits, n_compared) for the rig this clip was authored on.

    Only rigs that can hold every bone id are eligible: a clip that indexes
    bone 200 cannot belong to a 43-bone skeleton, no matter how well the
    surviving keys line up.  Filtering first is what keeps an out-of-range
    id from silently disqualifying every rig and skipping the range check.
    """
    ids = [tr["bone"] for tr in anim["tracks"]]
    scored = []
    for stem, bones in rigs.items():
        if any(b >= len(bones) for b in ids):
            continue
        hit, n = rig_score(anim, bones)
        if n:
            scored.append((hit, stem, n))
    if not scored:
        return None, 0, 0
    scored.sort(reverse=True)
    best = scored[0]
    return best[1], best[0], best[2]


# --------------------------------------------------------------------- input
def iter_clips(zip_path=CAF_ZIP, root=None):
    """Yield (name, bytes) for every .caf, from a zip or a directory."""
    if root:
        for p in sorted(pathlib.Path(root).rglob("*.caf")):
            yield p.name, p.read_bytes()
        return
    z = zipfile.ZipFile(zip_path)
    for n in z.namelist():
        if n.lower().endswith(".caf"):
            yield n, z.read(n)


def check(zip_path=CAF_ZIP, root=None, verbose=True):
    """Parse every clip and assert the stream is consumed exactly.

    "Exactly" means the last track's last key ends on the final byte of the
    file: no slack, no padding, no trailing chunk, nothing to resync against.
    If the stride or the header size were wrong this cannot hold for five
    files at once.
    """
    ok = True
    rows = []
    for name, data in iter_clips(zip_path, root):
        try:
            a = decode(data, name)
        except CafError as e:
            ok = False
            if verbose:
                print(f"FAIL {name}: {e}")
            continue
        exact = a["end"] == len(data)
        ok = ok and exact
        rows.append(a)
        if verbose:
            print(f'{name:14} v{a["version"]}  dur={a["duration"]:.6f}s  '
                  f'tracks={a["n_tracks"]:3}  keys={sum(t["n_keys"] for t in a["tracks"]):4}  '
                  f'consumed={a["end"]}/{len(data)}  '
                  f'{"exact" if exact else "SLACK " + str(len(data) - a["end"])}')
    if verbose and rows:
        print(f'\n{len(rows)} clips, {sum(r["n_tracks"] for r in rows)} tracks, '
              f'{sum(t["n_keys"] for r in rows for t in r["tracks"])} keys, '
              f'byte-exact on {sum(1 for r in rows if r["end"] == r["size"])}/{len(rows)}')
    return ok, rows


# -------------------------------------------------------------------- export
def to_json(anim, rig_stem=None, aliases=None):
    return dict(name=anim["name"], size=anim["size"], version=anim["version"],
                duration=anim["duration"], n_tracks=anim["n_tracks"],
                rig=rig_stem,
                # the .cmz stem in extracted/models/cmz may predate a rename;
                # `rig_file` is the name the skeleton actually has on disk now
                rig_file=(aliases or {}).get(rig_stem, rig_stem),
                tracks=[dict(bone=t["bone"], n_keys=t["n_keys"],
                             keys=[list(k) for k in t["keys"]])
                        for t in anim["tracks"]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="decode",
                    choices=["decode", "check"])
    ap.add_argument("--zip", default=CAF_ZIP)
    ap.add_argument("--root", default=None, help="read loose *.caf from a dir")
    ap.add_argument("--out", default="extracted/models/caf")
    a = ap.parse_args()

    ok, _ = check(a.zip, a.root)
    if a.cmd == "check":
        if not ok:
            print("FAILED")
            return 1
        print("OK - every clip consumed byte-exactly")
        return 0

    rigs = load_rigs()
    aliases = rig_aliases()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f'\n{"clip":14} {"duration":>9} {"tracks":>6} {"keys":>5} {"step":>8}  rig')
    for name, data in iter_clips(a.zip, a.root):
        anim = decode(data, name)
        rig, hit, n = resolve_rig(anim, rigs)
        step = anim["tracks"][0]["keys"][1][0] - anim["tracks"][0]["keys"][0][0]
        now = f' (now {aliases[rig]}.cmz)' if rig in aliases else ""
        print(f'{name.replace(".caf", ""):14} {anim["duration"]:9.6f} '
              f'{anim["n_tracks"]:6} '
              f'{sum(t["n_keys"] for t in anim["tracks"]):5} {step:8.5f}  '
              f'{rig} (key0 == bind pose on {hit}/{n} bones){now}')
        stem = pathlib.Path(name).stem
        (out / (stem + ".json")).write_text(
            json.dumps(to_json(anim, rig, aliases), indent=1), encoding="utf-8")
    print(f'\nwrote {len(list(out.glob("*.json")))} JSON to {out}')
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
