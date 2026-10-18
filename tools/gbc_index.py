#!/usr/bin/env python3
"""gbc_index.py - index the GBox2 .gbc script tree into a browsable cross-reference.

The .gbc files are plain GBK-encoded source for GBox2's in-house scripting
language: a C-like declarative DSL where classes hold members, widget
instances and method bodies.

Recovered per file:
  * #include graph (textual dependency)
  * class / style / #layer blocks, with methods, members and widget trees
  * the runtime interface registry  RegInterface(...) / FindInterface(...) /
    implicit m_i_XXX bindings   (the real dependency graph)
  * #enum / #define constants
  * @"..." localisation literals
  * csvr::call(...) server RPC sites

Outputs (under extracted/gbc_index/):
  index.html      dashboard: files, classes, interfaces, dependency graph
  strings.html    localisation inventory
  data.json       full machine-readable dataset
  src/*.html      per-file line-anchored source viewer

Usage:
    python gbc_index.py [--root DIR] [--out DIR] [--stats]
"""
import argparse
import bisect
import collections
import html
import json
import pathlib
import re
import sys

DEFAULT_ROOT = pathlib.Path("extracted/sysnet")
DEFAULT_OUT = pathlib.Path("extracted/gbc_index")

# ---------------------------------------------------------------- roles ----
ROLE_DIRS = {
    "ui": "UI screen",
    "webobj": "world object",
    "world": "world / terrain",
    "tab": "table access",
    "tabedit": "table editor",
    "EditeTools": "dev tool",
    "pub": "shared service",
    "sound": "audio",
    "country": "localisation",
    "unknown": "unnamed blob",
}
ROLE_ROOT = {
    "def.gbc": "global defs",
    "ver_def.gbc": "global defs",
    "index.gbc": "boot",
    "loginorder.gbc": "boot",
    "tabedit.gbc": "table editor",
}

# ------------------------------------------------------------- masking ----
def mask(src):
    """Blank comments and string bodies while preserving offsets and newlines.

    Returns (masked_text, kinds) where kinds[i] is
      0 = code, 1 = comment, 2 = string
    """
    n = len(src)
    out = list(src)
    kinds = bytearray(n)
    spans = []          # (start, end, kind, is_at_literal)
    i = 0
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            if j < 0:
                j = n
            for k in range(i, j):
                kinds[k] = 1
                out[k] = " "
            spans.append((i, j, 1, False))
            i = j
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                kinds[k] = 1
                if src[k] != "\n":
                    out[k] = " "
            spans.append((i, j, 1, False))
            i = j
        elif c == '"':
            is_at = i > 0 and src[i - 1] == "@"
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == '"':
                    j += 1
                    break
                j += 1
            for k in range(i, j):
                kinds[k] = 2
            # keep the quotes, blank the body so braces can't confuse depth
            body_end = j - 1 if j - 1 > i and src[j - 1] == '"' else j
            for k in range(i + 1, body_end):
                if src[k] != "\n":
                    out[k] = "_"
            spans.append((i, j, 2, is_at))
            i = j
        else:
            i += 1
    return "".join(out), kinds, spans


def line_starts(src):
    starts = [0]
    i = src.find("\n")
    while i >= 0:
        starts.append(i + 1)
        i = src.find("\n", i + 1)
    return starts


# ------------------------------------------------------------- patterns ----
RE_BLOCK = re.compile(
    r"^\s*(?:(class|style)\s+([\w\u4e00-\u9fff]+)\s*"
    r"(?::\s*([\w\u4e00-\u9fff]+(?:\s*,\s*[\w\u4e00-\u9fff]+)*))?"
    r"|#layer\s+([\w\u4e00-\u9fff]+))\s*\{?\s*$")
RE_METHOD = re.compile(
    r"^\s*(static\s+)?([A-Za-z_]\w*)\s+([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)\s*\((.*)\)\s*\{?\s*$")
RE_MEMBER = re.compile(
    r"^\s*([A-Za-z_]\w*)\s+([A-Za-z_][\w]*)\s*(:|\{|;|=|$)")
RE_INCLUDE = re.compile(r'^\s*#include\s+"([^"]+)"')
RE_DEFINE = re.compile(r"^\s*#define\s+([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)\s*(.*)$")
RE_ENUM = re.compile(r"^\s*#enum\s+(.+)$")
RE_REG = re.compile(r'RegInterface\s*\(\s*([^,()]+?)\s*,\s*"([^"]+)"')
RE_FIND = re.compile(r'FindInterface\s*\(\s*"([^"]+)"')
RE_MI = re.compile(r"\bm_i_([\w\u4e00-\u9fff]+)")
RE_RPC = re.compile(r'csvr::call\s*\(\s*([^,]+?)\s*,\s*"([^"]+)"')
RE_SYSTEM = re.compile(r'^\s*#system\s+(\w+)\s+"([^"]*)"')

CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

# The eight sysnet blobs whose names never hashed back.  Their identities are
# inferred from the #include statements of their neighbours plus their own
# contents; treat these as candidates, not proof.
UNNAMED_CANDIDATES = {
    "unknown/06e1f5c5.gbc": ("login_url.gbc",
        "defines class GLogUrl:GObj; login_url.gbc is an unresolved include "
        "and this is the only blob that looks like a URL holder"),
    "unknown/bbe04483.gbc": ("login/reg_dlg.gbc",
        "registers _RegDlg, defines GReg/GRegSub, calls _IP_RegByClient"),
    "unknown/8244870b.gbc": ("login/login_dlg.gbc",
        "registers _LoginDlg and _LoginDlg2; server-list login dialog"),
    "unknown/3e919c92.gbc": ("login/sysobj.gbc",
        "registers _PubIni; _GCfgIni config reader plus the logingatelink style"),
    "unknown/255b97b3.gbc": ("login/enterbtn.gbc",
        "defines EnterBtn:GSysImgBtn and registers iBtn (weakest guess)"),
    "unknown/c017c280.gbc": ("login_ver.gbc (zh build)",
        "login build config: 中文版 + lm enabled, _FILE_DIR_ = ch/"),
    "unknown/d5da1c62.gbc": ("login_ver.gbc (en build)",
        "login build config: 英文版 enabled, _FILE_DIR_ = en/"),
    "unknown/9c1f75ed.gbc": ("login aggregator (name not referenced)",
        "includes all five login scripts; registers _WebLogin and DownDemo"),
}


def role_of(rel):
    parts = rel.split("/")
    if len(parts) > 1:
        return ROLE_DIRS.get(parts[0], parts[0])
    return ROLE_ROOT.get(parts[0], "script")


def mkid(rel):
    return rel[:-4].replace("/", "__") if rel.endswith(".gbc") else rel.replace("/", "__")


# -------------------------------------------------------------- parsing ----
def parse_file(path, rel):
    raw = path.read_bytes()
    try:
        src = raw.decode("gbk")
    except UnicodeDecodeError:
        src = raw.decode("gbk", "replace")

    lines = src.splitlines()
    masked, kinds, spans = mask(src)
    mlines = masked.splitlines()
    starts = line_starts(src)

    # align lengths defensively
    if len(mlines) < len(lines):
        mlines += [""] * (len(lines) - len(mlines))

    depth_before, depth_after = [], []
    d = 0
    for ml in mlines:
        depth_before.append(d)
        d += ml.count("{") - ml.count("}")
        depth_after.append(d)

    info = {
        "id": mkid(rel),
        "path": rel,
        "role": role_of(rel),
        "bytes": len(raw),
        "lines": len(lines),
        "includes": [],
        "defines": [],
        "enums": [],
        "blocks": [],
        "reg": [],
        "uses": [],
        "rpc": [],
        "strings": [],
    }

    base = pathlib.PurePosixPath(rel).parent

    for i, ml in enumerate(mlines):
        # ---- directives -------------------------------------------------
        # NB: string bodies are masked out of `ml`, so paths and values must
        # be read from the original line.  A commented-out directive leaves
        # `ml` blank, which is exactly the guard we want.
        if not ml.lstrip().startswith("#"):
            continue
        orig = lines[i] if i < len(lines) else ""
        m = RE_INCLUDE.match(orig)
        if m:
            tgt = m.group(1)
            resolved = str(base / tgt) if base.parts else tgt
            # normalise ../
            parts = []
            for seg in resolved.split("/"):
                if seg == "..":
                    if parts:
                        parts.pop()
                elif seg not in ("", "."):
                    parts.append(seg)
            norm = "/".join(parts)
            # existence is resolved later against the whole tree
            info["includes"].append({
                "raw": tgt, "target": norm, "line": i + 1, "exists": False,
            })
            continue
        m = RE_SYSTEM.match(orig)
        if m:
            info["defines"].append({"name": "#system " + m.group(1),
                                    "value": m.group(2), "line": i + 1})
            continue
        m = RE_DEFINE.match(orig)
        if m:
            val = m.group(2)
            cut = val.find("//")
            if cut >= 0 and '"' not in val[:cut]:
                val = val[:cut]
            info["defines"].append({"name": m.group(1), "value": val.strip(),
                                    "line": i + 1})
            continue
        m = RE_ENUM.match(orig)
        if m:
            vals = [v.strip() for v in m.group(1).split(",") if v.strip()]
            info["enums"].append({"name": vals[0] if vals else "", "values": vals,
                                  "line": i + 1})
            continue

    # ---- blocks (class / style / #layer) --------------------------------
    # `#layer` panels nest inside a class body, so blocks are recognised at
    # any depth; the body ends when the brace depth returns to the block's
    # own enclosing depth.
    for i, ml in enumerate(mlines):
        m = RE_BLOCK.match(ml)
        if not m:
            continue
        if m.group(1):
            kind = m.group(1)
            name = m.group(2)
            # `class A:B,style1,style2` -> base B plus mixin styles
            parents = [p.strip() for p in (m.group(3) or "").split(",") if p.strip()]
            parent = parents[0] if parents else ""
            mixins = parents[1:]
        else:
            kind = "layer"
            name = m.group(4)
            parent = ""
            mixins = []
        outer = depth_before[i]
        # body start
        if "{" in ml:
            bs = i
        else:
            bs = i + 1
            while bs < len(mlines) and "{" not in mlines[bs]:
                bs += 1
        if bs >= len(mlines):
            continue
        be = bs
        while be + 1 < len(mlines) and depth_after[be] > outer:
            be += 1
        blk = {"kind": kind, "name": name, "base": parent, "mixins": mixins,
               "line": i + 1, "end": be + 1, "methods": [], "members": []}
        info["blocks"].append(blk)
        _parse_body(mlines, depth_before, depth_after, bs, be, blk)

    # ---- registry / uses / rpc -----------------------------------------
    for m in RE_REG.finditer(src):
        ln = bisect.bisect_right(starts, m.start())
        info["reg"].append({"name": m.group(2), "expr": m.group(1).strip(), "line": ln})
    for m in RE_FIND.finditer(src):
        ln = bisect.bisect_right(starts, m.start())
        info["uses"].append({"name": m.group(1), "how": "FindInterface", "line": ln})
    for m in RE_MI.finditer(src):
        ln = bisect.bisect_right(starts, m.start())
        info["uses"].append({"name": "_" + m.group(1), "how": "m_i_", "line": ln})
    for m in RE_RPC.finditer(src):
        ln = bisect.bisect_right(starts, m.start())
        info["rpc"].append({"svc": m.group(1).strip()[:80], "method": m.group(2), "line": ln})

    # ---- strings --------------------------------------------------------
    for s, e, k, is_at in spans:
        if k != 2:
            continue
        body_end = e - 1 if e - 1 > s and src[e - 1] == '"' else e
        text = src[s + 1:body_end].replace('\\"', '"').replace("\\\\", "\\")
        if not text:
            continue
        if is_at:
            loc = True
        elif CJK.search(text):
            loc = False          # CJK but not marked as a localised literal
        else:
            continue             # plain ASCII string: path / resource / keyword
        ln = bisect.bisect_right(starts, s)
        info["strings"].append({"text": text, "line": ln, "loc": loc})

    return info


def _parse_body(mlines, depth_before, depth_after, bs, be, blk):
    """Walk one block body, attaching methods / members / widget subtrees.

    Method bodies are skipped wholesale, so locals inside them are never
    mistaken for class members.  Widget initialiser blocks are descended
    into, and whatever they declare is attributed to the enclosing widget.
    """
    stack = []      # (owner_name, depth)

    def find_block_end(start):
        """Index of the line closing the block opened by `start`, or None.

        The opening brace may sit on the declaration line or on a following
        line, with only blank lines in between.
        """
        k = start
        while k < be and "{" not in mlines[k]:
            if k > start and mlines[k].strip():
                return None          # next non-blank line isn't a brace
            k += 1
        if k >= be:
            return None
        d0 = depth_before[start]
        while k + 1 < be and depth_after[k] > d0:
            k += 1
        return k

    i = bs + 1
    while i < be:
        db = depth_before[i]
        while stack and db <= stack[-1][1]:
            stack.pop()
        owner = stack[-1][0] if stack else ""
        ml = mlines[i]
        if not ml.strip():
            i += 1
            continue

        m = RE_METHOD.match(ml)
        if m:
            e = find_block_end(i)
            if e is not None:
                args = m.group(4).strip()
                blk["methods"].append({
                    "name": m.group(3), "ret": m.group(2), "args": args,
                    "nargs": 0 if not args else args.count(",") + 1,
                    "static": bool(m.group(1)), "line": i + 1, "owner": owner,
                })
                i = e + 1        # jump past the method body entirely
                continue
            i += 1
            continue

        m = RE_MEMBER.match(ml)
        if m:
            typ, nm, term = m.group(1), m.group(2), m.group(3)
            e = find_block_end(i)
            if term in (":", "{"):
                kind = "widget"
            elif term in (";", "="):
                kind = "var"
            else:               # bare `Type name` — widget only if a block follows
                kind = "widget" if e is not None else "var"
            blk["members"].append({"type": typ, "name": nm, "kind": kind,
                                   "line": i + 1, "owner": owner})
            if kind == "widget" and e is not None and e > i:
                stack.append((nm, db))      # descend, attributing children to it
                # skip the opening-brace line, which sits at the widget's own
                # depth and would otherwise pop the frame we just pushed
                j = i + 1
                while j < e and depth_before[j] <= db:
                    j += 1
                i = j
                continue
        i += 1


# ------------------------------------------------------------ dataset ----
def build(root):
    files = []
    for p in sorted(root.rglob("*.gbc")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        files.append(parse_file(p, rel))

    # resolve includes against the real tree
    paths = {f["path"] for f in files}
    for f in files:
        for inc in f["includes"]:
            inc["exists"] = inc["target"] in paths

    # interface providers / consumers
    providers = collections.defaultdict(list)
    for f in files:
        for r in f["reg"]:
            providers[r["name"]].append(f["path"])

    consumers = collections.defaultdict(set)
    for f in files:
        for u in f["uses"]:
            if u["name"] in providers:
                consumers[u["name"]].add(f["path"])

    interfaces = []
    for name, provs in providers.items():
        users = sorted(consumers.get(name, ()))
        interfaces.append({
            "name": name,
            "providers": sorted(set(provs)),
            "users": users,
            "nuses": sum(1 for f in files for u in f["uses"] if u["name"] == name),
        })
    interfaces.sort(key=lambda x: -len(x["users"]))

    # unresolved interface references (builtin / native)
    unresolved = collections.Counter()
    for f in files:
        for u in f["uses"]:
            if u["name"] not in providers:
                unresolved[u["name"]] += 1

    # edges
    inc_edges = []
    for f in files:
        for inc in f["includes"]:
            if inc["exists"]:
                inc_edges.append([f["path"], inc["target"]])
    iface_edges = collections.Counter()
    for f in files:
        for u in f["uses"]:
            for prov in set(providers.get(u["name"], ())):
                if prov != f["path"]:
                    iface_edges[(f["path"], prov)] += 1

    # type hierarchy: class -> base class / mixin styles
    typemap = collections.defaultdict(list)     # type name -> defining file
    for f in files:
        for b in f["blocks"]:
            if b["kind"] in ("class", "style"):
                typemap[b["name"]].append(f["path"])

    type_edges = collections.Counter()
    unresolved_types = collections.Counter()
    for f in files:
        for b in f["blocks"]:
            if b["kind"] != "class":
                continue
            for parent in ([b["base"]] if b["base"] else []) + b["mixins"]:
                provs = typemap.get(parent)
                if provs:
                    for p in set(provs):
                        if p != f["path"]:
                            type_edges[(f["path"], p)] += 1
                else:
                    unresolved_types[parent] += 1

    # include targets that no file in the tree satisfies
    missing_includes = collections.Counter()
    for f in files:
        for inc in f["includes"]:
            if not inc["exists"]:
                missing_includes[inc["target"]] += 1

    return {
        "files": files,
        "interfaces": interfaces,
        "unresolved": unresolved.most_common(),
        "unresolved_types": unresolved_types.most_common(),
        "typemap": {k: sorted(set(v)) for k, v in typemap.items()},
        "missing_includes": missing_includes.most_common(),
        "inc_edges": inc_edges,
        "iface_edges": [[a, b, c] for (a, b), c in iface_edges.items()],
        "type_edges": [[a, b, c] for (a, b), c in type_edges.items()],
    }


# --------------------------------------------------------------- stats ----
def stats(ds):
    files = ds["files"]
    nlines = sum(f["lines"] for f in files)
    blocks = [b for f in files for b in f["blocks"]]
    classes = [b for b in blocks if b["kind"] == "class"]
    styles = [b for b in blocks if b["kind"] == "style"]
    layers = [b for b in blocks if b["kind"] == "layer"]
    methods = [m for b in blocks for m in b["methods"]]
    members = [m for b in blocks for m in b["members"]]
    strings = [s for f in files for s in f["strings"]]
    regs = [r for f in files for r in f["reg"]]
    uses = [u for f in files for u in f["uses"]]
    rpc = [r for f in files for r in f["rpc"]]
    incs = [i for f in files for i in f["includes"]]
    enums = [e for f in files for e in f["enums"]]
    defines = [d for f in files for d in f["defines"]]
    print(f"files        {len(files):>7}")
    print(f"lines        {nlines:>7}")
    print(f"classes      {len(classes):>7}   styles {len(styles)}   layers {len(layers)}")
    print(f"methods      {len(methods):>7}   members {len(members)}")
    print(f"interfaces   {len(ds['interfaces']):>7}   reg sites {len(regs)}   use sites {len(uses)}")
    print(f"include edges{len(incs):>7}   resolved {sum(1 for i in incs if i['exists'])}")
    print(f"strings      {len(strings):>7}   (localised {sum(1 for s in strings if s['loc'])})")
    print(f"rpc sites    {len(rpc):>7}   enums {len(enums)}   defines {len(defines)}")
    print(f"iface edges  {len(ds['iface_edges']):>7}")
    print("roles:", dict(collections.Counter(f["role"] for f in files)))


# ---------------------------------------------------------------- HTML ----
CSS = """
*{box-sizing:border-box}
:root{
 --bg:#0e1116; --panel:#161b22; --panel2:#1b222c; --line:#232a34;
 --fg:#c9d1d9; --mut:#7d8794; --acc:#58a6ff; --grn:#3fb950; --org:#d29922;
 --red:#f85149; --pur:#bc8cff; --cyn:#39c5cf;
}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);
 font:14px/1.5 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Microsoft YaHei",sans-serif}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Courier New",monospace}
header{position:sticky;top:0;z-index:20;background:rgba(14,17,22,.94);
 backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.hd{max-width:1280px;margin:0 auto;padding:14px 22px 0}
h1{margin:0;font-size:17px;font-weight:600;letter-spacing:.2px}
h1 span{color:var(--mut);font-weight:400;font-size:13px;margin-left:10px}
.tabs{display:flex;gap:2px;margin-top:12px}
.tab{padding:8px 15px;cursor:pointer;color:var(--mut);border-bottom:2px solid transparent;
 font-size:13px;user-select:none}
.tab:hover{color:var(--fg)}
.tab.on{color:var(--fg);border-bottom-color:var(--acc)}
main{max-width:1280px;margin:0 auto;padding:20px 22px 80px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:13px 15px}
.card .n{font-size:23px;font-weight:600;letter-spacing:-.5px}
.card .l{font-size:11.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.6px;margin-top:2px}
.tools{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
input[type=search]{flex:1;min-width:220px;background:var(--panel);border:1px solid var(--line);
 color:var(--fg);padding:9px 13px;border-radius:7px;font-size:13px;outline:none}
input[type=search]:focus{border-color:var(--acc)}
.btn{background:var(--panel);border:1px solid var(--line);color:var(--mut);padding:8px 13px;
 border-radius:7px;cursor:pointer;font-size:12.5px}
.btn.on{color:var(--fg);border-color:var(--acc);background:#1c2b3f}
table{width:100%;border-collapse:collapse;font-size:12.8px}
th{text-align:left;color:var(--mut);font-weight:500;font-size:11.5px;text-transform:uppercase;
 letter-spacing:.5px;padding:8px 10px;border-bottom:1px solid var(--line);position:sticky;top:97px;
 background:var(--bg);z-index:10;cursor:pointer;white-space:nowrap}
th:hover{color:var(--fg)}
td{padding:6px 10px;border-bottom:1px solid #1a2028;vertical-align:top}
tr:hover td{background:#141a22}
.pill{display:inline-block;padding:1px 7px;border-radius:20px;font-size:10.5px;
 border:1px solid var(--line);color:var(--mut);white-space:nowrap}
.r-ui{color:var(--acc);border-color:#1f4b7a}
.r-webobj{color:var(--grn);border-color:#1e4a2b}
.r-world{color:var(--org);border-color:#5a4310}
.r-tab{color:var(--pur);border-color:#432a63}
.r-tabedit{color:var(--cyn);border-color:#17555b}
.r-EditeTools{color:#e3804f;border-color:#5c3018}
.r-pub{color:#9aa7b4;border-color:#333b46}
.r-sound{color:#d977b8;border-color:#5a2b48}
.r-root{color:var(--mut)}
.mut{color:var(--mut)}
.num{text-align:right;font-variant-numeric:tabular-nums}
.empty{color:var(--mut);padding:40px;text-align:center}
.panelbox{background:var(--panel);border:1px solid var(--line);border-radius:8px;
 padding:14px 16px;margin-bottom:16px}
.panelbox h2{margin:0 0 10px;font-size:11.5px;text-transform:uppercase;letter-spacing:.7px;
 color:var(--mut);font-weight:500}
.panelbox table{margin:0}
#graph{width:100%;height:600px;background:var(--panel);border:1px solid var(--line);
 border-radius:8px;display:block;cursor:grab}
#graph:active{cursor:grabbing}
.legend{display:flex;gap:16px;margin-top:10px;font-size:12px;color:var(--mut);flex-wrap:wrap}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
/* source viewer */
.wrap{display:grid;grid-template-columns:290px 1fr;gap:18px;max-width:1400px;margin:0 auto;
 padding:18px 22px 80px}
.side{position:sticky;top:80px;align-self:start;max-height:calc(100vh - 100px);overflow:auto;
 font-size:12.5px}
.side h3{font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:var(--mut);
 margin:16px 0 6px;font-weight:500}
.side h3:first-child{margin-top:0}
.side ul{list-style:none;margin:0;padding:0}
.side li{padding:2px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.side a{color:var(--fg)}
.code{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:auto;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;line-height:1.55}
.ln{display:flex}
.ln:hover{background:#141a22}
.ln .g{flex:0 0 56px;text-align:right;padding-right:14px;color:#4b5563;user-select:none;
 border-right:1px solid #1a2028}
.ln .c{padding-left:14px;white-space:pre-wrap;word-break:break-word}
.ln:target{background:#1d2a3a}
.k{color:#ff7b72}.s{color:#a5d6ff}.cm{color:#6b7480;font-style:italic}
.d{color:#d2a8ff}.n{color:#79c0ff}.t{color:#ffa657}
.bar{display:flex;gap:14px;align-items:center;padding:10px 22px;border-bottom:1px solid var(--line);
 font-size:12.5px;color:var(--mut);flex-wrap:wrap}
"""

JS_COMMON = """
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function fmt(n){return n.toLocaleString();}
"""

INDEX_BODY = """
<header><div class="hd">
  <h1>Chrono Tales &mdash; GBox2 script index <span>__SUBTITLE__</span></h1>
  <div class="tabs">
    <div class="tab on" data-t="ov">Overview</div>
    <div class="tab" data-t="files">Files</div>
    <div class="tab" data-t="cls">Classes</div>
    <div class="tab" data-t="ifc">Interfaces</div>
    <div class="tab" data-t="gr">Dependency graph</div>
  </div>
</div></header>
<main>
  <div id="ov" class="pane">
    <div class="cards">__CARDS__</div>
    <div class="panelbox"><h2>Roles</h2><div id="roles"></div></div>
    <div class="panelbox"><h2>Most-referenced interfaces</h2><div id="topifc"></div></div>
    <div class="panelbox"><h2>Largest scripts</h2><div id="topfiles"></div></div>
    <div class="panelbox"><h2>Include targets missing from the tree</h2>
      <p class="mut" style="margin:0 0 8px;font-size:12.5px">
      Referenced by <code>#include</code> but absent &mdash; a mobile UI tree
      (<code>mtui/</code>) plus other localisation builds.</p>
      <div id="missing"></div></div>
    <div class="panelbox"><h2>Unnamed blobs &mdash; candidate identities</h2>
      <p class="mut" style="margin:0 0 8px;font-size:12.5px">
      Inferred from neighbouring <code>#include</code> statements and each
      blob's own contents. Candidates, not proof.</p>
      <div id="cand"></div></div>
  </div>
  <div id="files" class="pane" hidden>
    <div class="tools"><input type="search" id="qf" placeholder="filter by path or role…">
      <span class="mut" id="cf"></span></div>
    <table id="tf"><thead><tr>
      <th data-k="path">path</th><th data-k="role">role</th>
      <th data-k="lines" class="num">lines</th><th data-k="ncls" class="num">cls</th>
      <th data-k="nmeth" class="num">meth</th><th data-k="ninc" class="num">inc</th>
      <th data-k="nreg" class="num">reg</th><th data-k="nuse" class="num">uses</th>
      <th data-k="nstr" class="num">str</th></tr></thead><tbody></tbody></table>
  </div>
  <div id="cls" class="pane" hidden>
    <div class="tools"><input type="search" id="qc" placeholder="filter classes by name or base…">
      <span class="mut" id="cc"></span></div>
    <table id="tc"><thead><tr><th data-k="name">class</th><th data-k="base">base</th>
      <th data-k="mixins">mixin styles</th>
      <th data-k="file">file</th><th data-k="nmeth" class="num">methods</th>
      <th data-k="nmem" class="num">members</th><th data-k="line" class="num">line</th>
      </tr></thead><tbody></tbody></table>
  </div>
  <div id="ifc" class="pane" hidden>
    <div class="tools"><input type="search" id="qi" placeholder="filter interfaces…">
      <span class="mut" id="ci"></span></div>
    <table id="ti"><thead><tr><th data-k="name">interface</th>
      <th data-k="providers">registered by</th><th data-k="nusers" class="num">users</th>
      <th data-k="nuses" class="num">refs</th></tr></thead><tbody></tbody></table>
  </div>
  <div id="gr" class="pane" hidden>
    <div class="tools">
      <button class="btn on" id="g-inc">#include graph</button>
      <button class="btn" id="g-ifc">interface graph</button>
      <button class="btn" id="g-typ">type hierarchy</button>
      <span class="mut" id="ginfo"></span>
    </div>
    <canvas id="graph"></canvas>
    <div class="legend" id="glegend"></div>
  </div>
</main>
"""

STRINGS_BODY = """
<header><div class="hd">
  <h1>Localisation inventory <span>__SUBTITLE__</span></h1>
  <div class="tabs"><div class="tab on">@"…" literals</div></div>
</div></header>
<main>
  <div class="tools"><input type="search" id="qs" placeholder="search text…">
    <span class="mut" id="cs"></span></div>
  <table id="ts"><thead><tr><th data-k="kind">kind</th><th data-k="text">text</th>
    <th data-k="file">file</th>
    <th data-k="line" class="num">line</th></tr></thead><tbody></tbody></table>
</main>
"""

SRC_BODY = """
<header><div class="hd">
  <h1>__PATH__ <span>__ROLE__</span></h1>
  <div class="tabs"><div class="tab on" onclick="location.href='../index.html'">&larr; index</div></div>
</div></header>
<div class="wrap">
  <div class="side">__SIDE__</div>
  <div>
    <div class="code">__CODE__</div>
  </div>
</div>
"""


def page(title, body, extra_js=""):
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
        f"{body}<script>{JS_COMMON}{extra_js}</script></body></html>"
    )


KEYWORDS = {
    "class", "style", "void", "int", "float", "string", "var", "obj", "static",
    "if", "else", "for", "while", "switch", "case", "default", "return",
    "break", "continue", "new", "nil", "null", "this", "pid", "true", "false",
    "enum", "sizeof",
}
RE_TOK = re.compile(r"[A-Za-z_]\w*|\d+\.?\d*")


def build_code_html(src, kinds, starts, lines):
    """Highlight using the precomputed kind array."""
    out = []
    nlines = len(lines)
    for i in range(nlines):
        s = starts[i]
        e = s + len(lines[i])
        raw = lines[i]
        seg = []
        pos = s
        # group consecutive chars by kind
        j = 0
        while j < len(raw):
            k = kinds[s + j] if s + j < len(kinds) else 0
            k2 = j
            while k2 < len(raw) and (kinds[s + k2] if s + k2 < len(kinds) else 0) == k:
                k2 += 1
            chunk = raw[j:k2]
            if k == 1:
                seg.append('<span class="cm">%s</span>' % html.escape(chunk))
            elif k == 2:
                seg.append('<span class="s">%s</span>' % html.escape(chunk))
            else:
                seg.append(_hl_code(chunk))
            j = k2
        text = "".join(seg) or "&nbsp;"
        out.append(f'<div class="ln" id="L{i+1}"><div class="g">{i+1}</div>'
                   f'<div class="c">{text}</div></div>')
    return "".join(out)


def _hl_code(chunk):
    if chunk.lstrip().startswith("#"):
        return '<span class="d">%s</span>' % html.escape(chunk)
    res = []
    last = 0
    for m in RE_TOK.finditer(chunk):
        tok = m.group(0)
        if tok[0].isdigit():
            cls = "n"
        elif tok in KEYWORDS:
            cls = "k"
        elif tok and tok[0].isupper():
            cls = "t"
        else:
            continue
        if m.start() > last:
            res.append(html.escape(chunk[last:m.start()]))
        res.append(f'<span class="{cls}">{html.escape(tok)}</span>')
        last = m.end()
    if last < len(chunk):
        res.append(html.escape(chunk[last:]))
    return "".join(res)


# ------------------------------------------------------------- writing ----
def write_src_pages(files, out):
    src_dir = out / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    included_by = collections.defaultdict(set)
    for f in files:
        for inc in f["includes"]:
            if inc["exists"]:
                included_by[inc["target"]].add(f["path"])

    for f in files:
        p = DEFAULT_ROOT / f["path"]
        raw = p.read_bytes()
        try:
            src = raw.decode("gbk")
        except UnicodeDecodeError:
            src = raw.decode("gbk", "replace")
        lines = src.splitlines()
        masked, kinds, spans = mask(src)
        starts = line_starts(src)
        code = build_code_html(src, kinds, starts, lines)
        f["_code_html"] = code
        body = (SRC_BODY.replace("__PATH__", html.escape(f["path"]))
                .replace("__ROLE__", html.escape(f["role"]))
                .replace("__SIDE__", _side(f, included_by))
                .replace("__CODE__", code))
        (src_dir / f"{f['id']}.html").write_text(
            page(f"{f['path']} — GBox2 script", body), encoding="utf-8")
        f.pop("_code_html", None)


def _side(f, included_by):
    out = ["<h3>file</h3><ul>",
           f'<li class="mut">{f["bytes"]:,} B &middot; {f["lines"]:,} lines &middot; GBK</li></ul>']

    def block(title, items):
        if not items:
            return
        out.append(f"<h3>{title} ({len(items)})</h3><ul>")
        out.extend(items)
        out.append("</ul>")

    block("includes", [
        (f'<li><a href="{mkid(i["target"])}.html#L1">{html.escape(i["raw"])}</a></li>'
         if i["exists"] else
         f'<li class="mut" title="not present in tree">{html.escape(i["raw"])}</li>')
        for i in f["includes"]])

    block("included by", [
        f'<li><a href="{mkid(p)}.html#L1">{html.escape(p)}</a></li>'
        for p in sorted(included_by.get(f["path"], ()))[:40]])

    block("registers", [
        f'<li><a href="#L{r["line"]}">{html.escape(r["name"])}</a></li>'
        for r in f["reg"]])

    seen, rows = set(), []
    for u in f["uses"]:
        if u["name"] not in seen:
            seen.add(u["name"])
            rows.append(u)
    block("uses", [
        f'<li><a href="#L{u["line"]}">{html.escape(u["name"])}</a>'
        f' <span class="mut">{u["how"]}</span></li>' for u in rows])

    defs = []
    for b in f["blocks"]:
        base = f' <span class="mut">: {html.escape(b["base"])}</span>' if b["base"] else ""
        mix = ""
        if b.get("mixins"):
            mix = (' <span class="mut">+ '
                   + ", ".join(html.escape(m) for m in b["mixins"]) + '</span>')
        defs.append(f'<li><a href="#L{b["line"]}">{html.escape(b["name"])}</a>{base}{mix}</li>')
        for m in b["methods"][:200]:
            owner = f' <span class="mut">{html.escape(m["owner"])}</span>' if m["owner"] else ""
            defs.append(f'<li style="padding-left:12px">'
                        f'<a href="#L{m["line"]}" class="mut">{html.escape(m["name"])}</a>{owner}</li>')
    block("definitions", defs)

    block("enums", [
        f'<li><a href="#L{e["line"]}" class="mut">{html.escape(", ".join(e["values"][:3]))}'
        f'{"…" if len(e["values"]) > 3 else ""}</a></li>' for e in f["enums"]])
    return "".join(out)


INDEX_JS = """
const D = __DATA__;
const F = D.files, C = D.classes, I = D.interfaces;
const byPath = {}; F.forEach(f=>byPath[f.path]=f);

/* ---------- overview ---------- */
document.getElementById('roles').innerHTML =
 '<table><tbody>' + Object.entries(D.roles).sort((a,b)=>b[1]-a[1]).map(([r,n])=>
  `<tr><td><span class="pill r-${r.replace(/[^a-z]/gi,'')}">${esc(r)}</span></td>
   <td class="num">${fmt(n)}</td>
   <td class="mut">${(n/F.length*100).toFixed(1)}%</td></tr>`).join('') + '</tbody></table>';

document.getElementById('topifc').innerHTML =
 '<table><tbody>' + I.slice(0,12).map(i=>
  `<tr><td class="mono">${esc(i.name)}</td><td class="num">${fmt(i.users.length)}</td>
   <td class="mut mono">${esc(i.providers[0]||'')}</td></tr>`).join('') + '</tbody></table>';

document.getElementById('topfiles').innerHTML =
 '<table><tbody>' + F.slice().sort((a,b)=>b.lines-a.lines).slice(0,12).map(f=>
  `<tr><td><a href="src/${f.id}.html">${esc(f.path)}</a></td>
   <td class="num">${fmt(f.lines)}</td></tr>`).join('') + '</tbody></table>';

document.getElementById('missing').innerHTML =
 '<table><tbody>' + D.missing_includes.map(([t,n])=>
  `<tr><td class="mono">${esc(t)}</td><td class="num">${n}</td></tr>`).join('')
 + '</tbody></table>';

document.getElementById('cand').innerHTML =
 '<table><tbody>' + D.candidates.map(c=>
  `<tr><td class="mono"><a href="src/${c.id}.html">${esc(c.path)}</a></td>
   <td class="mono" style="color:var(--grn)">${esc(c.name)}</td></tr>
   <tr><td colspan="2" class="mut" style="padding-top:0;font-size:11.5px">${esc(c.ev)}</td></tr>`
  ).join('') + '</tbody></table>';

/* ---------- tabs ---------- */
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  t.classList.add('on');
  document.querySelectorAll('.pane').forEach(p=>p.hidden=true);
  document.getElementById(t.dataset.t).hidden=false;
  if(t.dataset.t==='gr') drawGraph();
});

/* ---------- files table ---------- */
function renderFiles(q){
  q=(q||'').toLowerCase();
  const rows=F.filter(f=>!q||f.path.toLowerCase().includes(q)||f.role.toLowerCase().includes(q));
  document.querySelector('#tf tbody').innerHTML = rows.map(f=>
    `<tr><td><a href="src/${f.id}.html">${esc(f.path)}</a></td>
     <td><span class="pill r-${f.role.replace(/[^a-z]/gi,'')}">${esc(f.role)}</span></td>
     <td class="num">${fmt(f.lines)}</td><td class="num">${f.ncls||''}</td>
     <td class="num">${f.nmeth||''}</td><td class="num">${f.ninc||''}</td>
     <td class="num">${f.nreg||''}</td><td class="num">${f.nuse||''}</td>
     <td class="num">${f.nstr||''}</td></tr>`).join('');
  document.getElementById('cf').textContent = rows.length+' / '+F.length+' files';
}
document.getElementById('qf').oninput = e=>renderFiles(e.target.value);
renderFiles('');

/* ---------- classes ---------- */
function renderCls(q){
  q=(q||'').toLowerCase();
  const rows=C.filter(c=>!q||c.name.toLowerCase().includes(q)||(c.base||'').toLowerCase().includes(q)
    ||(c.mixins||[]).some(m=>m.toLowerCase().includes(q)));
  document.querySelector('#tc tbody').innerHTML = rows.slice(0,3000).map(c=>
    `<tr><td class="mono">${esc(c.name)}</td>
     <td class="mono mut">${esc(c.base||'')}</td>
     <td class="mut">${(c.mixins||[]).map(m=>`<span class="pill">${esc(m)}</span>`).join(' ')}</td>
     <td><a href="src/${byPath[c.file].id}.html#L${c.line}">${esc(c.file)}</a></td>
     <td class="num">${c.nmeth||''}</td><td class="num">${c.nmem||''}</td>
     <td class="num mut">${c.line}</td></tr>`).join('');
  document.getElementById('cc').textContent = rows.length+' / '+C.length+' classes';
}
document.getElementById('qc').oninput = e=>renderCls(e.target.value);
renderCls('');

/* ---------- interfaces ---------- */
function renderIfc(q){
  q=(q||'').toLowerCase();
  const rows=I.filter(i=>!q||i.name.toLowerCase().includes(q));
  document.querySelector('#ti tbody').innerHTML = rows.slice(0,3000).map(i=>
    `<tr><td class="mono">${esc(i.name)}</td>
     <td>${i.providers.map(p=>`<a href="src/${byPath[p].id}.html">${esc(p)}</a>`).join(', ')}</td>
     <td class="num">${fmt(i.users.length)}</td><td class="num">${fmt(i.nuses)}</td></tr>`).join('');
  document.getElementById('ci').textContent = rows.length+' / '+I.length+' interfaces';
}
document.getElementById('qi').oninput = e=>renderIfc(e.target.value);
renderIfc('');

/* ---------- graph ---------- */
let gMode='inc', gNodes=[], gEdges=[], gScale=1, gOff={x:0,y:0}, gDrag=null, gDrawn=false;
function buildGraph(){
  const deg={}, E=[];
  const raw = gMode==='inc' ? D.inc_edges : (gMode==='ifc' ? D.iface_edges : D.type_edges);
  raw.forEach(e=>{
    const a=byPath[e[0]], b=byPath[e[1]];
    if(!a||!b||a.path===b.path) return;
    E.push([a.path,b.path,e[2]||1]);
    deg[a.path]=(deg[a.path]||0)+1; deg[b.path]=(deg[b.path]||0)+1;
  });
  const nodes = Object.keys(deg).map(p=>({p, d:deg[p], x:0,y:0,vx:0,vy:0,
      r: Math.min(18, 3+Math.sqrt(deg[p])*1.7)}));
  // seed layout on a spiral by role cluster angle
  const roles = {};
  nodes.forEach((n,i)=>{
    const r0 = byPath[n.p].role; roles[r0]=roles[r0]||Object.keys(roles).length;
    const ang = roles[r0]*2.399 + i*0.11;
    const rad = 60 + Math.sqrt(i)*26;
    n.x = 640 + Math.cos(ang)*rad; n.y = 300 + Math.sin(ang)*rad;
  });
  const idx={}; nodes.forEach((n,i)=>idx[n.p]=i);
  const L = E.map(([a,b,w])=>[idx[a],idx[b],w]);
  // force settle
  for(let it=0; it<420; it++){
    const k = it<260 ? 0.9 : 0.35;
    for(let i=0;i<nodes.length;i++){
      const a=nodes[i];
      for(let j=i+1;j<nodes.length;j++){
        const b=nodes[j];
        let dx=b.x-a.x, dy=b.y-a.y, d2=dx*dx+dy*dy||0.01;
        if(d2>90000) continue;
        const d=Math.sqrt(d2), f=(1400/d2)*k;
        const fx=dx/d*f, fy=dy/d*f;
        a.vx-=fx; a.vy-=fy; b.vx+=fx; b.vy+=fy;
      }
    }
    for(const [i,j,w] of L){
      const a=nodes[i], b=nodes[j];
      let dx=b.x-a.x, dy=b.y-a.y, d=Math.sqrt(dx*dx+dy*dy)||0.01;
      const target=70+Math.min(120, w*6);
      const f=((d-target)*0.012)*k;
      const fx=dx/d*f, fy=dy/d*f;
      a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy;
    }
    for(const n of nodes){
      n.vx += (640-n.x)*0.0012*k; n.vy += (300-n.y)*0.0012*k;
      n.x += Math.max(-14,Math.min(14,n.vx)); n.y += Math.max(-14,Math.min(14,n.vy));
      n.vx*=0.55; n.vy*=0.55;
    }
  }
  gNodes=nodes; gEdges=L;
  document.getElementById('ginfo').textContent =
    `${nodes.length} nodes, ${L.length} edges`;
  document.getElementById('glegend').innerHTML =
    '<span><span class="dot" style="background:#58a6ff"></span>ui</span>'+
    '<span><span class="dot" style="background:#3fb950"></span>webobj</span>'+
    '<span><span class="dot" style="background:#d29922"></span>world</span>'+
    '<span><span class="dot" style="background:#bc8cff"></span>tab</span>'+
    '<span><span class="dot" style="background:#39c5cf"></span>tabedit</span>'+
    '<span><span class="dot" style="background:#e3804f"></span>EditeTools</span>'+
    '<span><span class="dot" style="background:#9aa7b4"></span>pub</span>'+
    '<span class="mut">click a node to open its source</span>';
}
const ROLE_COLOR={ui:'#58a6ff',webobj:'#3fb950',world:'#d29922',tab:'#bc8cff',
  tabedit:'#39c5cf',EditeTools:'#e3804f',pub:'#9aa7b4',sound:'#d977b8'};
function drawGraph(){
  const cv=document.getElementById('graph');
  const dpr=window.devicePixelRatio||1;
  cv.width=cv.clientWidth*dpr; cv.height=cv.clientHeight*dpr;
  const g=cv.getContext('2d'); g.scale(dpr,dpr);
  if(!gNodes.length||gMode!==cv.dataset.mode){ cv.dataset.mode=gMode; buildGraph(); }
  g.clearRect(0,0,cv.clientWidth,cv.clientHeight);
  g.save(); g.translate(gOff.x,gOff.y); g.scale(gScale,gScale);
  g.strokeStyle='rgba(120,140,170,.16)'; g.lineWidth=1;
  for(const [i,j] of gEdges){
    const a=gNodes[i], b=gNodes[j];
    g.beginPath(); g.moveTo(a.x,a.y); g.lineTo(b.x,b.y); g.stroke();
  }
  for(const n of gNodes){
    const role=byPath[n.p].role;
    g.fillStyle=ROLE_COLOR[role]||'#7d8794';
    g.beginPath(); g.arc(n.x,n.y,n.r,0,6.2832); g.fill();
    g.strokeStyle='rgba(14,17,22,.85)'; g.lineWidth=1.5; g.stroke();
  }
  g.restore();
  cv.onmousemove=e=>{
    const rc=cv.getBoundingClientRect();
    const mx=(e.clientX-rc.left-gOff.x)/gScale, my=(e.clientY-rc.top-gOff.y)/gScale;
    let hit=null;
    for(const n of gNodes){
      if((n.x-mx)**2+(n.y-my)**2 < (n.r+3)**2){ hit=n; break; }
    }
    cv.style.cursor = gDrag?'grabbing':(hit?'pointer':'grab');
    cv.title = hit? hit.p : '';
  };
  cv.onclick=e=>{
    const rc=cv.getBoundingClientRect();
    const mx=(e.clientX-rc.left-gOff.x)/gScale, my=(e.clientY-rc.top-gOff.y)/gScale;
    for(const n of gNodes){
      if((n.x-mx)**2+(n.y-my)**2 < (n.r+3)**2){
        location.href='src/'+byPath[n.p].id+'.html'; return;
      }
    }
  };
  cv.onmousedown=e=>{gDrag={x:e.clientX-gOff.x,y:e.clientY-gOff.y};};
  window.addEventListener('mouseup',()=>{gDrag=null;});
  cv.onmousemove=e=>{
    if(gDrag){ gOff.x=e.clientX-gDrag.x; gOff.y=e.clientY-gDrag.y; drawGraph(); return; }
    const rc=cv.getBoundingClientRect();
    const mx=(e.clientX-rc.left-gOff.x)/gScale, my=(e.clientY-rc.top-gOff.y)/gScale;
    let hit=null;
    for(const n of gNodes) if((n.x-mx)**2+(n.y-my)**2<(n.r+3)**2){hit=n;break;}
    cv.style.cursor=hit?'pointer':'grab'; cv.title=hit?hit.p:'';
  };
  cv.onwheel=e=>{e.preventDefault(); gScale*= e.deltaY<0?1.1:0.91;
    gScale=Math.max(.25,Math.min(3,gScale)); drawGraph();};
}
document.getElementById('g-inc').onclick=()=>{
  gMode='inc'; document.getElementById('g-inc').classList.add('on');
  document.getElementById('g-ifc').classList.remove('on');
  document.getElementById('g-typ').classList.remove('on'); drawGraph();
};
document.getElementById('g-ifc').onclick=()=>{
  gMode='ifc'; document.getElementById('g-ifc').classList.add('on');
  document.getElementById('g-inc').classList.remove('on');
  document.getElementById('g-typ').classList.remove('on'); drawGraph();
};
document.getElementById('g-typ').onclick=()=>{
  gMode='typ'; document.getElementById('g-typ').classList.add('on');
  document.getElementById('g-inc').classList.remove('on');
  document.getElementById('g-ifc').classList.remove('on'); drawGraph();
};
window.addEventListener('resize',()=>{ if(!document.getElementById('gr').hidden) drawGraph(); });
"""

STRINGS_JS = """
const S=__DATA__;
function render(q){
  q=(q||'').toLowerCase();
  const rows=S.filter(s=>!q||s.text.toLowerCase().includes(q));
  document.querySelector('#ts tbody').innerHTML = rows.slice(0,4000).map(s=>
    `<tr><td>${s.loc?'<span class="pill" style="color:var(--acc);border-color:#1f4b7a">@</span>'
                  :'<span class="pill">plain</span>'}</td>
     <td>${esc(s.text)}</td>
     <td><a href="src/${s.id}.html#L${s.line}">${esc(s.file)}</a></td>
     <td class="num mut">${s.line}</td></tr>`).join('');
  document.getElementById('cs').textContent = rows.length+' / '+S.length+' strings';
}
document.getElementById('qs').oninput=e=>render(e.target.value);
render('');
"""


def write_index(ds, out, stats_text):
    files = ds["files"]
    for f in files:
        f["ncls"] = sum(1 for b in f["blocks"] if b["kind"] == "class")
        f["nmeth"] = sum(len(b["methods"]) for b in f["blocks"])
        f["ninc"] = len(f["includes"])
        f["nreg"] = len(f["reg"])
        f["nuse"] = len(f["uses"])
        f["nstr"] = len(f["strings"])

    classes = []
    for f in files:
        for b in f["blocks"]:
            if b["kind"] == "class":
                classes.append({"name": b["name"], "base": b["base"],
                                "mixins": b["mixins"], "file": f["path"],
                                "line": b["line"], "nmeth": len(b["methods"]),
                                "nmem": len(b["members"])})
    classes.sort(key=lambda c: (-c["nmeth"], c["name"]))

    roles = collections.Counter(f["role"] for f in files)

    candidates = []
    for path, (name, ev) in UNNAMED_CANDIDATES.items():
        f = next((x for x in files if x["path"] == path), None)
        if f:
            candidates.append({"path": path, "id": f["id"], "name": name, "ev": ev})

    data = {
        "files": [{k: f[k] for k in ("id", "path", "role", "lines", "bytes",
                                     "ncls", "nmeth", "ninc", "nreg", "nuse", "nstr")}
                  for f in files],
        "classes": classes,
        "interfaces": [{"name": i["name"], "providers": i["providers"],
                        "users": i["users"], "nuses": i["nuses"]}
                       for i in ds["interfaces"]],
        "inc_edges": ds["inc_edges"],
        "iface_edges": ds["iface_edges"],
        "type_edges": ds["type_edges"],
        "missing_includes": ds["missing_includes"],
        "candidates": candidates,
        "roles": dict(roles),
    }

    cards = "".join(
        f'<div class="card"><div class="n">{v}</div><div class="l">{l}</div></div>'
        for v, l in stats_text)

    body = INDEX_BODY.replace("__SUBTITLE__", "235 scripts &middot; GBK source") \
                     .replace("__CARDS__", cards)
    js = INDEX_JS.replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
    (out / "index.html").write_text(
        page("Chrono Tales — GBox2 script index", body, js), encoding="utf-8")

    # strings page
    srows = []
    for f in files:
        for s in f["strings"]:
            srows.append({"text": s["text"], "file": f["path"], "id": f["id"],
                          "line": s["line"], "loc": s["loc"]})
    nloc = sum(1 for s in srows if s["loc"])
    sbody = STRINGS_BODY.replace(
        "__SUBTITLE__",
        f"{len(srows):,} user-visible strings &middot; {nloc:,} are @\"&hellip;\" literals")
    sjs = STRINGS_JS.replace("__DATA__", json.dumps(srows).replace("</", "<\\/"))
    (out / "strings.html").write_text(
        page("Localisation inventory", sbody, sjs), encoding="utf-8")


def main():
    global DEFAULT_ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    DEFAULT_ROOT = a.root

    if not a.root.exists():
        sys.exit(f"no such tree: {a.root}")

    print(f"scanning {a.root} …")
    ds = build(a.root)
    stats(ds)

    if a.stats:
        return

    a.out.mkdir(parents=True, exist_ok=True)
    files = ds["files"]
    print("writing source pages …")
    write_src_pages(files, a.out)

    nlines = sum(f["lines"] for f in files)
    nblocks = sum(len(f["blocks"]) for f in files)
    nmeth = sum(len(b["methods"]) for f in files for b in f["blocks"])
    nstr = sum(len(f["strings"]) for f in files)
    nreg = sum(len(f["reg"]) for f in files)
    stats_text = [
        (len(files), "scripts"),
        (f"{nlines:,}", "lines of code"),
        (nblocks, "class / style blocks"),
        (f"{nmeth:,}", "methods"),
        (len(ds["interfaces"]), "registered interfaces"),
        (f"{len(ds['inc_edges']):,}", "#include edges"),
        (f"{len(ds['iface_edges']):,}", "interface edges"),
        (f"{nstr:,}", "localised strings"),
        (f"{nreg:,}", "registry sites"),
    ]
    print("writing index …")
    write_index(ds, a.out, stats_text)

    payload = {
        "root": str(a.root),
        "files": files,
        "interfaces": ds["interfaces"],
        "unresolved": ds["unresolved"],
        "inc_edges": ds["inc_edges"],
        "iface_edges": ds["iface_edges"],
    }
    (a.out / "data.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"done -> {a.out}")


if __name__ == "__main__":
    main()
