#!/usr/bin/env python3
"""Identify each netpak `varbin` payload as a game table.

Two independent signals, in order of strength:

1. **Column count.**  It must equal the table editor's `m_szItemName` length.
   This alone pins most blobs.

2. **Field-type signature.**  Each `tabedit` schema declares a widget code per
   column.  Those codes map onto the serialised kind:

       widget   meaning        serialised
       ------   -------------  ----------
       1        plain box      int
       2        bitfield       int
       256      string box     str
       257      string+list    str
       258      explanation    str
       1024     group          list
       2048+    flip / path    list
       >= 3840  cross-table    list

   A blob's decoded records must then agree column-by-column.  This is what
   separates the tables that share a column count, and it is checked rather
   than guessed.

A **hard structural rule** sits between them: no `tabedit` schema declares a
blob column (the widget codes are only the ones listed above), so any payload
whose records contain a `bytes` field is not a table at all.  That is what the
six map payloads are — they were previously all filed under `forcetabedit`
because they happened to have six columns.

Assignment is **one-to-one**: a table editor owns at most one payload. Without
that rule the highest-scoring candidate wins for *every* blob that ties on
column count, which silently files unrelated tables under the same editor —
`pathedit` was claiming three 5-column payloads and `unionbuffedit` two
8-column ones. The greedy pass below ranks every (blob, table) pair by
signature agreement and hands each table to its best claimant; a blob that
loses every table it wanted falls through to `unshipped` or `ambiguous`.

`exact` therefore means "unique column count *and* uncontested"; a payload that
had to out-score a rival is `signature`. When the two signals still leave a tie
the blob is reported as `ambiguous` with `table: null`, rather than guessed.

Five payloads have **no shipped editor** at all — their column counts (88, 2,
12, 8, 5) match no `tabedit` schema, or the schema they match is already owned
by a better fit. They are named descriptively from their contents and flagged
`synthetic`, so the exporter can still emit them with positional `c0..cN`
headers rather than losing the rows:

    433267c3  questtab      1,399 rows x 88  天梯/主线/支线/日常 quests
    e15a7343  sceneobjtab   1,982 rows x  2  scene -> 64 object ids
    dd447626  starsoultab      26 rows x 12  星魂 (star soul) definitions
    60792c4a  starbufftab     331 rows x  8  星运 buff effects, referenced by
                                             startabedit's per-sign buff lists
    b0a40a84  nodegraphtab    252 rows x  5  navigation node graph: node id,
                                             name, x, z, neighbour ids
"""
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import varbin_tree  # noqa: E402

SCHEMA = os.path.join(HERE, "table_schemas.json")
OUT = os.path.join(HERE, "varbin_identity.json")

# payloads with no shipped editor: name -> (synthetic table, description)
UNSHIPPED = {
    "433267c3": ("questtab", "任务表 (天梯/主线/支线/日常), 88 columns, "
                             "no shipped editor"),
    "e15a7343": ("sceneobjtab", "场景物件表: scene name -> 64 object ids, "
                                "no shipped editor"),
    "dd447626": ("starsoultab", "星魂表 (star soul definitions), 12 columns, "
                                "no shipped editor"),
    "60792c4a": ("starbufftab", "星运表: buff effect definitions referenced by "
                                "startabedit's per-sign lists, 8 columns, "
                                "no shipped editor"),
    "b0a40a84": ("nodegraphtab", "寻路节点图: node id, name, x, z, neighbour "
                                 "ids, 5 columns, no shipped editor"),
}

# widget code -> serialised kind
WIDGET = {1: "int", 2: "int", 256: "str", 257: "str", 258: "str"}
LISTY = {1024, 2048, 2049}


def expect(types):
    """Schema widget codes -> expected serialised kind per column."""
    out = []
    for t in types:
        if t in WIDGET:
            out.append(WIDGET[t])
        elif t in LISTY or t >= 3840:
            out.append("list")
        else:
            out.append(None)          # unknown code: do not score
    return out


def kind(v):
    if v is None:
        return "null"
    if isinstance(v, bool) or isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, bytes):
        return "bytes"
    if isinstance(v, (list, tuple)):
        return "list"
    return "?"


def observed(recs):
    """Per-column serialised kind, ignoring nulls; 'mixed' where they differ."""
    out = []
    for i in range(len(recs[0])):
        ks = {kind(r[i]) for r in recs if isinstance(r, list)} - {"null"}
        out.append(ks.pop() if len(ks) == 1 else "mixed")
    return out


def strings_of(v, out):
    if isinstance(v, str):
        if v:
            out.append(v)
    elif isinstance(v, (list, tuple)):
        for x in v:
            strings_of(x, out)
    return out


def sig_score(obs, exp):
    """How many columns agree; int and float are interchangeable."""
    hits = 0
    for o, e in zip(obs, exp):
        if e is None or o in ("mixed", "null"):
            continue
        if o == e or (e == "int" and o == "float"):
            hits += 1
    return hits


def main():
    schemas = json.load(open(SCHEMA, encoding="utf-8"))
    by_count = defaultdict(list)
    for sname, s in schemas.items():
        cols = [c for c in s["columns"] if c]
        by_count[len(cols)].append((sname, s, len(cols)))

    result = {}
    info = {}                      # blob -> scored candidates, for pass 2

    # ---- pass 1: decode, apply the map rule, score every candidate ---------
    for name, d in sorted(varbin_tree.payloads()):
        recs = d["value"]
        if not isinstance(recs, list) or not recs or not isinstance(recs[0], list):
            result[name] = {"records": None, "columns": None, "table": None,
                            "confidence": "n/a", "note": "not a table"}
            continue

        n_recs, n_cols = len(recs), len(recs[0])
        obs = observed(recs)

        # hard rule: a tabedit table has no blob column
        if "bytes" in obs:
            result[name] = {
                "records": n_recs, "columns": n_cols, "table": None,
                "editor_title": "", "confidence": "map",
                "note": "contains raw blob fields; map payload, not a table",
            }
            continue

        vocab = set(strings_of(recs, []))
        scored = []
        for sname, s, _ in by_count.get(n_cols, []):
            exp = expect(s["types"])
            colnames = {c for c in s["columns"] if c}
            scored.append((sig_score(obs, exp),
                           len(colnames & vocab), sname, s))
        # best first; the tuple tail keeps the order deterministic
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
        info[name] = {"recs": n_recs, "cols": n_cols, "scored": scored}

    # ---- pass 2: greedy one-to-one assignment ------------------------------
    # Every (blob, table) pair competes globally, so a table cannot be handed to
    # two payloads and a strong fit cannot be displaced by a weak one.
    pairs = []
    for name, d in info.items():
        for rank, (sig, voc, sname, s) in enumerate(d["scored"]):
            pairs.append((sig, voc, rank, name, sname, s))
    pairs.sort(key=lambda t: (-t[0], -t[1], t[2], t[3]))

    owner = {}                     # table -> blob that won it
    assign = {}                    # blob -> (sig, voc, sname, schema)
    for sig, voc, _rank, name, sname, s in pairs:
        if name in assign or sname in owner:
            continue
        assign[name] = (sig, voc, sname, s)
        owner[sname] = name

    # best score any rival blob reached for a given table
    rival = defaultdict(int)
    for sig, _voc, _rank, name, sname, _s in pairs:
        if owner.get(sname) != name:
            rival[sname] = max(rival[sname], sig)

    # ---- pass 3: label, naming editor-less payloads descriptively ----------
    print(f"{'blob':10s} {'recs':>6s} {'cols':>5s}  {'table':22s} "
          f"{'conf':>10s} {'sig':>4s} {'alt':>4s} {'voc':>4s}")
    print("-" * 88)

    for name in sorted(info):
        d = info[name]
        n_recs, n_cols, scored = d["recs"], d["cols"], d["scored"]

        if name not in assign:
            if name in UNSHIPPED:
                sname, desc = UNSHIPPED[name]
                result[name] = {
                    "records": n_recs, "columns": n_cols, "table": sname,
                    "editor_title": desc, "confidence": "unshipped",
                    "synthetic": True, "signature_hits": 0, "column_hits": 0,
                }
                print(f"{name:10s} {n_recs:6d} {n_cols:5d}  {sname:22s} "
                      f"{'unshipped':>10s}")
                continue
            result[name] = {
                "records": n_recs, "columns": n_cols, "table": None,
                "editor_title": "", "confidence": "ambiguous",
                "note": "every matching editor is claimed by a better fit",
            }
            print(f"{name:10s} {n_recs:6d} {n_cols:5d}  {'(ambiguous)':22s} "
                  f"{'ambiguous':>10s}")
            continue

        sig, voc, sname, s = assign[name]
        alt = rival[sname]
        if sig > alt:
            conf = "exact" if len(scored) == 1 and alt == 0 else "signature"
        elif voc > 0:
            conf = "vocab"
        else:
            conf = "ambiguous"
            sname, s = None, None

        title = (s["editor_title"] or sname) if s else ""
        print(f"{name:10s} {n_recs:6d} {n_cols:5d}  {(sname or '(unknown)'):22s} "
              f"{conf:>10s} {sig:4d} {alt:4d} {voc:4d}")
        result[name] = {
            "records": n_recs,
            "columns": n_cols,
            "table": sname,
            "editor_title": title,
            "confidence": conf,
            "signature_hits": sig,
            "column_hits": voc,
        }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    ident = [r for r in result.values() if r.get("table")]
    conf = defaultdict(int)
    for r in result.values():
        conf[r.get("confidence")] += 1
    print(f"\nidentified {len(ident)}/{len(result)} payloads")
    print(f"confidence: {dict(sorted(conf.items()))}")
    print(f"total records: {sum(r.get('records') or 0 for r in result.values()):,}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
