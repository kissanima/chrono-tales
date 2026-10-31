#!/usr/bin/env python3
"""effect_ini.py - parse the GBoxII particle effect definitions.

`netpak/effect/*.txt` is the engine's plain-text effect format.  Only three
files ship in the retail archive (42da2fff, df3269c5, f30b8336) and all three
are engine demo effects (`m_sName='Test'`), but `SysFileVer.var` registers
1,777 `effect/...` paths -- the real library (skill hit effects, mount trails,
quest-complete bursts) is referenced but not shipped.

Grammar (GBK text, tabs are significant only for nesting):

    //Particle 1.00 for GBoxII
    m_sName='Test'                     global key = value
    :Atom                              element block (Atom / Ribbon)
        m_sTextureName='smoke123.jpg'
        m_vAtomScale=(3,(0.01,0.64),(0.56,0.73),(1,0.9))   keyframed curve
        :Emitter                       nested behaviour block
            m_vPosition=v3(0,0,1)
            m_fRoll=(2,-90,90)         flat tuple

Value forms:
    'text'                 quoted string
    -1 / 0.5               int or float
    v3(x,y,z)              vector
    (n, v1..vn)            flat tuple
    (n, (t,v) * n)         piecewise-linear keyframe curve, t normalised 0..1

Output: extracted/netpak/effect/_parsed.json + a one-screen summary.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EFF = ROOT / "extracted" / "netpak" / "effect"

NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def split_top(s):
    """Split on commas that are not inside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def num(tok):
    tok = tok.strip()
    if not NUM.fullmatch(tok):
        return tok
    return float(tok) if ("." in tok or "e" in tok.lower()) else int(tok)


def parse_value(raw):
    s = raw.strip().rstrip(";").strip()
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    m = re.fullmatch(r"v3\((.*)\)", s)
    if m:
        return {"v3": [num(x) for x in split_top(m.group(1))]}
    if s.startswith("(") and s.endswith(")"):
        parts = split_top(s[1:-1])
        n = int(num(parts[0])) if NUM.fullmatch(parts[0]) else 0
        rest = parts[1:]
        if rest and all(p.startswith("(") for p in rest):
            curve = []
            for p in rest:
                pair = [num(x) for x in split_top(p.strip("()"))]
                curve.append(pair)
            return {"curve": curve}
        return {"tuple": [num(x) for x in rest]}
    return num(s)


def parse(text):
    root = {"globals": {}, "blocks": []}
    stack = [root]
    for ln in text.splitlines():
        if not ln.strip() or ln.strip().startswith("//"):
            continue
        indent = len(ln) - len(ln.lstrip("\t"))
        body = ln.strip()
        if body.startswith(":"):
            # a block header opens a sibling of anything at >= this depth
            while len(stack) > 1 and stack[-1]["_indent"] >= indent:
                stack.pop()
            blk = {"type": body[1:], "props": {}, "blocks": [],
                   "_indent": indent}
            stack[-1]["blocks"].append(blk)
            stack.append(blk)
            continue
        if "=" not in body:
            continue
        k, v = body.split("=", 1)
        k = k.strip()
        while len(stack) > 1 and stack[-1]["_indent"] > indent:
            stack.pop()
        tgt = stack[-1]["props"] if len(stack) > 1 else stack[0]["globals"]
        tgt[k] = parse_value(v)
    strip_indent(root)
    return root


def strip_indent(node):
    if isinstance(node, dict):
        node.pop("_indent", None)
        for b in node.get("blocks", []):
            strip_indent(b)
    return node


def walk(node, depth=0):
    yield depth, node
    for b in node.get("blocks", []):
        yield from walk(b, depth + 1)


def summarize(path):
    text = path.read_text(encoding="gbk", errors="replace")
    doc = parse(text)
    g = doc["globals"]
    atoms = [b for b in doc["blocks"] if b["type"] == "Atom"]
    ribbons = [b for b in doc["blocks"] if b["type"] == "Ribbon"]
    kinds = {}
    for _, b in walk(doc):
        if b is not doc:
            kinds[b["type"]] = kinds.get(b["type"], 0) + 1
    textures = sorted({b["props"].get("m_sTextureName")
                       for b in doc["blocks"] if b["props"].get("m_sTextureName")})
    return {
        "file": path.name,
        "bytes": len(text.encode("gbk", "replace")),
        "name": g.get("m_sName"),
        "total_time": g.get("m_nTotalTime"),
        "sound": g.get("m_sSndFileName"),
        "atoms": len(atoms),
        "ribbons": len(ribbons),
        "blocks": kinds,
        "textures": textures,
        "doc": doc,
    }


def main():
    files = sorted(EFF.glob("*.txt"))
    if not files:
        raise SystemExit(f"no effect files under {EFF}")
    out = []
    for f in files:
        s = summarize(f)
        out.append(s)
        print(f"\n{s['file']}  {s['bytes']} B  name={s['name']!r}  "
              f"totalTime={s['total_time']}")
        print(f"  atoms={s['atoms']} ribbons={s['ribbons']}  "
              f"blocks={s['blocks']}")
        print(f"  textures={s['textures']}")
        for b in s["doc"]["blocks"]:
            tex = b["props"].get("m_sTextureName", "")
            subs = [x["type"] for x in b["blocks"]]
            print(f"    :{b['type']:6} {tex:22} {subs}")
    (EFF / "_parsed.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(out)} effects -> {EFF / '_parsed.json'}")


if __name__ == "__main__":
    sys.exit(main())
