#!/usr/bin/env python3
"""Build a self-contained explorer over everything the pipeline recovered.

Writes `extracted/explorer/`:

    index.html          the whole UI (no external assets, no build step)
    data/index.js       manifest: stats, table list, map list, gallery links
    data/t_<table>.js   one file per table, loaded on demand
    data/assets.js      the 9,975 SysFileVer asset records

Why per-table files: the tables are 5.7 MB of CSV, and a single embedded blob
would make the page crawl.  Data is delivered as `window.__X = …` assignments
loaded with a dynamically injected <script>, **not** fetch() — fetch is blocked
by CORS on `file://`, script tags are not, so the explorer opens by
double-clicking index.html with no server of any kind.
"""
import csv
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

EXTRACTED = ROOT / "extracted"
TABLES = EXTRACTED / "tables"
MAPS = EXTRACTED / "maps"
OUT = EXTRACTED / "explorer"
DATA = OUT / "data"


def js_safe(obj):
    """JSON that survives being pasted into a .js file."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (s.replace("\u2028", "\\u2028")
             .replace("\u2029", "\\u2029")
             .replace("</", "<\\/"))


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = list(csv.reader(f))
    if not r:
        return [], []
    return r[0], r[1:]


def build_tables():
    ident = json.load(open(HERE / "varbin_identity.json", encoding="utf-8"))
    by_file = {}
    for blob, info in ident.items():
        if not info.get("table"):
            continue
        fname = pathlib.Path(info["table"]).stem + ".csv"
        by_file[fname] = (blob, info)

    tables = []
    for path in sorted(TABLES.glob("*.csv")):
        blob, info = by_file.get(path.name, ("", {}))
        cols, rows = read_csv(path)
        if not cols:
            continue
        tid = path.stem
        (DATA / f"t_{tid}.js").write_text(
            "window.__T=window.__T||{};window.__T[%s]=%s;\n"
            % (js_safe(tid), js_safe({"columns": cols, "rows": rows})),
            encoding="utf-8")
        tables.append({
            "id": tid,
            "file": path.name,
            "blob": blob,
            "title": info.get("editor_title") or "",
            "confidence": info.get("confidence") or "",
            "synthetic": bool(info.get("synthetic")),
            # a CSV in tables/ that no varbin blob claims is a cross-reference
            # this project derived (action_links.csv, action_packs.csv), not a
            # decoded payload -- it must not inflate the varbin table count
            # that check_all.py pins at 42.
            "derived": path.name not in by_file,
            "records": len(rows),
            "columns": len(cols),
        })

    # A table can lose its payload when identification is corrected, leaving a
    # t_<id>.js nobody references.  Prune so the data dir matches the manifest.
    keep = {"t_%s.js" % t["id"] for t in tables}
    for f in sorted(DATA.glob("t_*.js")):
        if f.name not in keep:
            f.unlink()
            print(f"pruned stale {f.name}")
    return tables


def build_assets():
    """SysFileVer.var -> the 9,975 asset records."""
    import varfile
    var = EXTRACTED / "netpak" / "SysFileVer.var"
    if not var.exists():
        return {"columns": [], "rows": [], "note": "SysFileVer.var not present"}
    r = varfile.load(str(var))
    v = r["value"]
    recs = v if isinstance(v, list) else [v]
    cols = list(varfile.FIELDS)
    rows = []
    for rec in recs:
        if not isinstance(rec, list):
            continue
        rows.append([json.dumps(x, ensure_ascii=False)
                     if isinstance(x, (list, tuple)) else ("" if x is None else x)
                     for x in rec])
    (DATA / "assets.js").write_text(
        "window.__A=%s;\n" % js_safe({"columns": cols, "rows": rows}),
        encoding="utf-8")
    return {"columns": cols, "count": len(rows)}


def count_archive():
    """Entry and filename-recovery counts, from the two extraction manifests.

    Never hardcode these: the manifests are written by `gpak_extract.py` and
    are the only record of how many entries the archives actually hold.
    """
    entries = 0
    for group in ("netpak", "sysnet"):
        p = EXTRACTED / group / "_manifest.json"
        if p.exists():
            entries += len(json.load(open(p, encoding="utf-8")))
    unknowns = 0
    for group in ("netpak", "sysnet"):
        d = EXTRACTED / group / "unknown"
        if d.is_dir():
            unknowns += sum(1 for _ in d.iterdir() if _.is_file())
    return entries, entries - unknowns


def count_scripts():
    """Script totals from the index the script browser itself builds."""
    p = EXTRACTED / "gbc_index" / "data.json"
    if not p.exists():
        return {}
    d = json.load(open(p, encoding="utf-8"))
    files = d.get("files", [])
    return {
        "files": len(files),
        "lines": sum(f.get("lines", 0) for f in files),
        "classes": sum(f.get("ncls", 0) for f in files),
        "methods": sum(f.get("nmeth", 0) for f in files),
        "interfaces": len(d.get("interfaces", [])),
        "iface_edges": len(d.get("iface_edges", [])),
        "inc_edges": len(d.get("inc_edges", [])),
    }


def count_meshes():
    """A .53wf file holds several submeshes, so file count != mesh count."""
    p = EXTRACTED / "models" / "_index.json"
    if not p.exists():
        return {}
    idx = json.load(open(p, encoding="utf-8"))
    return {
        "files": len(idx),
        "wf": sum(1 for m in idx if m.get("kind") == "53wf"),
        "cmz": sum(1 for m in idx if m.get("kind") == "cmz"),
        "submeshes": sum(m.get("mesh_count", 0) for m in idx),
        "materials": sum(m.get("material_count", 0) for m in idx),
    }


TILE_IDS = [517493, 517494, 517495, 518493, 518494, 518495]


def tile_paths():
    """hash -> (path, ix, iy).  MAKEMAPID(ix,iy) = ix + iy*1000 (def.gbc:110),
    so the file stem of a .zmap is its grid cell, not a copy id."""
    import gpak
    out = {}
    for mid in TILE_IDS:
        path = f"intro/intro{mid}.zmap"
        out[f"{gpak.hash_name(path):08x}"] = (path, mid % 1000, mid // 1000)
    return out


def build_maps():
    out = []
    tiles = tile_paths()
    for f in sorted(MAPS.glob("*.json")):
        m = json.load(open(f, encoding="utf-8"))
        path, ix, iy = tiles.get(m["blob"], ("", None, None))
        out.append({
            "blob": m["blob"],
            "path": path,
            "ix": ix,
            "iy": iy,
            "id": ix + iy * 1000 if ix is not None else None,
            "map_name": m.get("map_name", ""),
            "version": m.get("version"),
            "height": m.get("height", {}),
            "patches": m.get("height_patches", {}),
            "layer": m.get("layer", {}),
            "layers": [l.get("tex_name") for l in m.get("layers", []) if l],
            "light": m.get("light"),
            "fog": m.get("fog_colour"),
            "decorations": m.get("decorations", 0),
            "models": m.get("models", 0),
        })
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    tables = build_tables()
    assets = build_assets()
    maps = build_maps()
    entries, named = count_archive()
    meshes = count_meshes()

    stats = {
        "entries": entries,
        "named": named,
        # varbin payloads only: the two derived cross-reference CSVs in
        # tables/ (action_links, action_packs) are not varbin tables
        "tables": sum(1 for t in tables if not t["derived"]),
        "editors": sum(1 for t in tables
                       if not t["synthetic"] and not t["derived"]),
        "table_rows": sum(t["records"] for t in tables if not t["derived"]),
        "xref_tables": [t["id"] for t in tables if t["derived"]],
        "assets": assets.get("count", 0),
        "maps": len(maps),
        "scripts": count_scripts(),
        "meshes": meshes,
    }

    meta = {"stats": stats, "tables": tables, "maps": maps,
            "asset_columns": assets.get("columns", [])}
    (DATA / "index.js").write_text(
        "window.__META=%s;\n" % js_safe(meta), encoding="utf-8")

    (OUT / "index.html").write_text(PAGE, encoding="utf-8")

    s = stats["scripts"]
    print(f"tables   {stats['tables']} ({stats['table_rows']:,} rows, "
          f"{stats['editors']} with a shipped editor)"
          + (f"  [+{len(stats['xref_tables'])} derived: "
             f"{', '.join(stats['xref_tables'])}]" if stats['xref_tables'] else ""))
    print(f"assets   {stats['assets']:,}")
    print(f"maps     {stats['maps']}")
    print(f"meshes   {meshes.get('wf', 0)} files / {meshes.get('submeshes', 0)} "
          f"submeshes, {meshes.get('cmz', 0)} skeletons")
    print(f"scripts  {s.get('files', 0)} files, {s.get('lines', 0):,} lines")
    print(f"archive  {named}/{entries} filenames recovered")
    # every image the Maps view references must exist, or the page shows a
    # broken frame and nothing tells you.  The 3D renders are keyed by the
    # tile STEM (from the map path) while the relief/layer previews are keyed
    # by the archive HASH, so this catches mixing the two up.
    bad = []
    for m in maps:
        stem = (m.get("path") or "").rsplit("/", 1)[-1]
        stem = stem[:-5] if stem.endswith(".zmap") else stem
        for tag in ("oblique", "plan", "horizon"):
            f = MAPS / f"{stem}_3d_tex_{tag}.png"
            if not f.exists():
                bad.append(f.name)
        for tag in ("relief", "layer"):
            if not (MAPS / f"{m['blob']}_{tag}.png").exists():
                bad.append(f"{m['blob']}_{tag}.png")
    if not (MAPS / "world_intro_layout.png").exists():
        bad.append("world_intro_layout.png")
    print(f"map imgs {len(maps) * 5 + 1} referenced, missing {len(bad)}"
          + (f": {bad}" if bad else ""))
    print(f"wrote    {OUT / 'index.html'}")


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Chrono Tales — recovered data</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#12151a; --panel:#181c23; --panel2:#1e232c; --line:#2a3140;
  --fg:#dbe2ec; --dim:#8b97a8; --accent:#6fb2ff; --accent2:#7ee0b0;
  --warn:#e6b566; --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
html[data-theme=light]{
  --bg:#f4f6f9; --panel:#ffffff; --panel2:#eef1f6; --line:#d7dee8;
  --fg:#1b2430; --dim:#5c6b7f; --accent:#1f6fd0; --accent2:#0e8a5f;
  --warn:#a06a12;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
  "Helvetica Neue","PingFang SC","Microsoft YaHei",sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header{display:flex;align-items:center;gap:16px;padding:12px 20px;
  border-bottom:1px solid var(--line);background:var(--panel);
  position:sticky;top:0;z-index:20}
header h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.2px}
header .sub{color:var(--dim);font-size:12px}
header .spacer{flex:1}
button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
  border-radius:6px;padding:5px 11px;font:inherit;font-size:12px;cursor:pointer}
button:hover{border-color:var(--accent)}
button:disabled{opacity:.4;cursor:default}
input[type=search]{background:var(--panel2);border:1px solid var(--line);
  color:var(--fg);border-radius:6px;padding:6px 10px;font:inherit;
  font-size:13px;min-width:240px}
input[type=search]:focus{outline:none;border-color:var(--accent)}
#wrap{display:flex;min-height:calc(100vh - 57px)}
nav{width:250px;flex:0 0 250px;border-right:1px solid var(--line);
  background:var(--panel);padding:12px 0;overflow:auto;
  max-height:calc(100vh - 57px);position:sticky;top:57px}
nav .group{color:var(--dim);font-size:11px;text-transform:uppercase;
  letter-spacing:.08em;padding:10px 18px 6px}
nav .item{padding:6px 18px;cursor:pointer;display:flex;gap:8px;
  align-items:baseline;font-size:13px}
nav .item:hover{background:var(--panel2)}
nav .item.on{background:var(--panel2);box-shadow:inset 2px 0 0 var(--accent)}
nav .item .n{margin-left:auto;color:var(--dim);font:11px var(--mono)}
nav .item .tag{font-size:10px;color:var(--warn)}
main{flex:1;padding:20px 24px;min-width:0}
h2{font-size:16px;margin:0 0 4px}
h3{font-size:13px;margin:22px 0 8px;color:var(--dim);
  text-transform:uppercase;letter-spacing:.07em}
.lede{color:var(--dim);margin:0 0 18px;font-size:13px;max-width:70ch}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:12px;margin-bottom:8px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:9px;
  padding:14px 16px}
.card .k{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
.card .l{color:var(--dim);font-size:11px;text-transform:uppercase;
  letter-spacing:.07em;margin-top:3px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;padding:5px 10px;border-bottom:1px solid var(--line);
  white-space:nowrap;max-width:420px;overflow:hidden;text-overflow:ellipsis}
th{position:sticky;top:0;background:var(--panel2);z-index:2;font-weight:600;
  font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim)}
td.num{font-variant-numeric:tabular-nums;text-align:right}
tbody tr:hover{background:var(--panel2)}
.scroll{overflow:auto;max-height:calc(100vh - 250px);border:1px solid var(--line);
  border-radius:8px;background:var(--panel)}
.bar{display:flex;align-items:center;gap:10px;margin:12px 0;flex-wrap:wrap}
.bar .count{color:var(--dim);font-size:12px;font-variant-numeric:tabular-nums}
.chip{display:inline-block;padding:1px 7px;border-radius:999px;font-size:10.5px;
  border:1px solid var(--line);color:var(--dim);vertical-align:1px}
.chip.exact{border-color:var(--accent2);color:var(--accent2)}
.chip.signature{border-color:var(--warn);color:var(--warn)}
.chip.unshipped{border-color:#a06ac0;color:#b98ad4}
.chip.derived{border-color:var(--warn);color:var(--warn)}
.maps{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));
  gap:16px}
.map{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  overflow:hidden}
.map .imgs{display:flex;gap:1px;background:var(--line)}
.map .imgs img{width:50%;display:block;image-rendering:pixelated;
  background:#000}
.map .body{padding:12px 14px}
.map h4{margin:0 0 2px;font-size:13px;font-family:var(--mono)}
.map .m{color:var(--dim);font-size:12px;line-height:1.7}
.map .m b{color:var(--fg);font-weight:500}
.gal{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
  gap:14px}
.gal a{display:block;background:var(--panel);border:1px solid var(--line);
  border-radius:10px;overflow:hidden;color:var(--fg)}
.gal a:hover{border-color:var(--accent);text-decoration:none}
.gal img{width:100%;display:block;background:#0b0d10}
.gal .cap{padding:10px 12px;font-size:12.5px}
.gal .cap span{display:block;color:var(--dim);font-size:11.5px;margin-top:2px}
.empty{color:var(--dim);padding:40px 0;text-align:center}
.note{background:var(--panel);border:1px solid var(--line);
  border-left:2px solid var(--accent);border-radius:0 8px 8px 0;
  padding:12px 16px;font-size:12.5px;color:var(--dim);margin:16px 0}
.note b{color:var(--fg)}
.kv{font-family:var(--mono);font-size:12px;color:var(--dim)}
</style></head><body>

<header>
  <h1>Chrono Tales</h1>
  <span class="sub" id="hdrsub"></span>
  <span class="spacer"></span>
  <input type="search" id="q" placeholder="filter…" style="display:none">
  <button id="theme">theme</button>
</header>

<div id="wrap">
  <nav id="nav"></nav>
  <main id="main"><div class="empty">loading…</div></main>
</div>

<script src="data/index.js"></script>
<script>
"use strict";
const M = window.__META;
const $ = s => document.querySelector(s);
const el = (t, c, txt) => { const e = document.createElement(t); if (c) e.className = c;
  if (txt != null) e.textContent = txt; return e; };
const esc = s => String(s);

/* ---------- data loading (script injection, not fetch: file:// safe) ------ */
const loaded = {};
function loadScript(src){
  return new Promise((ok, bad) => {
    if (loaded[src]) return ok();
    const s = document.createElement("script");
    s.src = src; s.onload = () => { loaded[src] = 1; ok(); };
    s.onerror = () => bad(new Error("cannot load " + src));
    document.head.appendChild(s);
  });
}
async function tableData(id){
  await loadScript("data/t_" + id + ".js");
  return (window.__T || {})[id] || {columns:[],rows:[]};
}
async function assetData(){
  await loadScript("data/assets.js");
  return window.__A || {columns:[],rows:[]};
}

/* ---------- state ---------- */
const KINDS = ["overview","assets","maps","models","scripts"];
function viewFromHash(){
  const h = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (h.startsWith("t/")) return {kind:"tables", table:h.slice(2)};
  if (KINDS.includes(h)) return {kind:h};
  return {kind:"overview"};
}
function hashFor(v){
  return "#" + (v.table ? "t/" + encodeURIComponent(v.table) : v.kind);
}
let view = viewFromHash(), filter = "", page = 0;
const PAGE_SIZE = 100;

function go(v){
  page = 0;
  const h = hashFor(v);
  if (location.hash === h){ view = v; render(); }
  else location.hash = h;                 // hashchange drives the re-render
}
window.addEventListener("hashchange", () => {
  view = viewFromHash(); page = 0; filter = "";
  const q = $("#q"); if (q) q.value = "";
  render();
});

/* ---------- nav ---------- */
function buildNav(){
  const nav = $("#nav"); nav.textContent = "";
  const top = [["overview","Overview",null],["assets","Asset registry",M.stats.assets],
               ["maps","Maps",M.stats.maps],["models","Models & textures",null],
               ["scripts","Scripts",M.stats.scripts.files]];
  for (const [k,label,n] of top){
    const d = el("div","item" + (view.kind===k && !view.table ? " on":""));
    d.appendChild(el("span",null,label));
    if (n) d.appendChild(el("span","n",n.toLocaleString()));
    d.onclick = () => go({kind:k});
    nav.appendChild(d);
  }
  nav.appendChild(el("div","group","Tables — " + M.stats.tables));
  for (const t of M.tables){
    const d = el("div","item" + (view.table===t.id ? " on":""));
    d.appendChild(el("span",null,t.id));
    if (t.synthetic) d.appendChild(el("span","tag","no editor"));
    d.appendChild(el("span","n",t.records.toLocaleString()));
    d.title = (t.title || t.id) + " · " + t.columns + " cols · " + t.confidence;
    d.onclick = () => go({kind:"tables",table:t.id});
    nav.appendChild(d);
  }
}

/* ---------- renderers ---------- */
function statCards(){
  const s = M.stats, c = el("div","cards");
  const add = (k,l) => { const d = el("div","card");
    d.appendChild(el("div","k",k)); d.appendChild(el("div","l",l)); c.appendChild(d); };
  add(s.tables, "game tables");
  add(s.table_rows.toLocaleString(), "table rows");
  add(s.assets.toLocaleString(), "asset records");
  add(s.maps, "decoded maps");
  add(s.scripts.files, "scripts");
  add(s.named + " / " + s.entries, "filenames recovered");
  return c;
}

function renderOverview(){
  const m = $("#main"); m.textContent = "";
  m.appendChild(el("h2",null,"Recovered data"));
  m.appendChild(el("p","lede",
    "Everything here was decoded from the two .gpak archives and the client "+
    "binary — no asset was taken from a wiki or a modding kit. Browse the "+
    "tables, the asset registry, the maps, the meshes and the script index."));
  m.appendChild(statCards());

  m.appendChild(el("h3",null,"What is in here"));
  const rows = [
    ["Tables", M.stats.tables + " game tables, " + M.stats.table_rows.toLocaleString() +
      " rows — item, NPC, skill, buff, drop, quest, map and the rest"],
    ["Assets", M.stats.assets.toLocaleString() + " records from SysFileVer.var, the " +
      "engine's own asset registry, with path, group and model→texture links"],
    ["Maps", M.stats.maps + " .zmap scene documents — height field, terrain layers, " +
      "decoration and model placements"],
    ["Meshes", M.stats.meshes.wf + " .53wf mesh files holding " +
      M.stats.meshes.submeshes + " submeshes, plus " + M.stats.meshes.cmz +
      " .cmz skeletons with skinned geometry"],
    ["Scripts", M.stats.scripts.files + " uncompiled GBK scripts, browsable with a " +
      "real dependency graph"],
  ];
  const sc = el("div","scroll"); const tb = el("table");
  const thead = el("thead"), tr = el("tr");
  ["area","what"].forEach(h => tr.appendChild(el("th",null,h)));
  thead.appendChild(tr); tb.appendChild(thead);
  const tbody = el("tbody");
  for (const [a,b] of rows){ const r = el("tr");
    r.appendChild(el("td",null,a)); r.appendChild(el("td",null,b)); tbody.appendChild(r); }
  tb.appendChild(tbody); sc.appendChild(tb); m.appendChild(sc);

  m.appendChild(el("h3",null,"Reading it"));
  const n = el("div","note");
  n.innerHTML = "<b>Every table column has a real name.</b> " + M.stats.editors +
    " of these tables are declared by a shipped <code>tabedit/*.gbc</code> editor, " +
    "which lists its columns as parallel tuples — so the headers are not " +
    "positional guesses; <code>itemtabedit</code> alone has 52 documented " +
    "columns. The remaining " + (M.stats.tables - M.stats.editors) + " payloads " +
    "have no shipped editor and keep positional <code>c0..cN</code> headers; they " +
    "are marked “no editor” in the list.";
  m.appendChild(n);
}

function makeTable(cols, rows, total, extraHead){
  const sc = el("div","scroll");
  const tb = el("table");
  const thead = el("thead"), tr = el("tr");
  for (const c of cols) tr.appendChild(el("th",null,c));
  thead.appendChild(tr); tb.appendChild(thead);
  const tbody = el("tbody");
  const start = page * PAGE_SIZE, slice = rows.slice(start, start + PAGE_SIZE);
  for (const row of slice){
    const r = el("tr");
    for (let i = 0; i < cols.length; i++){
      const v = row[i] == null ? "" : row[i];
      const td = el("td", /^-?\d+(\.\d+)?$/.test(v) ? "num" : null);
      td.textContent = v; td.title = v;
      r.appendChild(td);
    }
    tbody.appendChild(r);
  }
  tb.appendChild(tbody); sc.appendChild(tb);
  return sc;
}

function pager(total){
  const bar = el("div","bar");
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const prev = el("button",null,"← prev"), next = el("button",null,"next →");
  prev.disabled = page === 0; next.disabled = page >= pages - 1;
  prev.onclick = () => { page--; render(); };
  next.onclick = () => { page++; render(); };
  bar.appendChild(prev); bar.appendChild(next);
  bar.appendChild(el("span","count",
    (page*PAGE_SIZE+1).toLocaleString() + "–" +
    Math.min(total,(page+1)*PAGE_SIZE).toLocaleString() + " of " +
    total.toLocaleString()));
  return bar;
}

function filterRows(cols, rows){
  if (!filter) return rows;
  const f = filter.toLowerCase();
  return rows.filter(r => r.some(v => v != null &&
    String(v).toLowerCase().includes(f)));
}

async function renderTable(id){
  const m = $("#main"); m.textContent = "";
  const t = M.tables.find(x => x.id === id);
  m.appendChild(el("h2",null, t.id));
  const sub = el("p","lede");
  sub.textContent = (t.title ? t.title + " · " : "") + t.records.toLocaleString() +
    " rows · " + t.columns + " columns · blob " + (t.blob || "?") +
    (t.synthetic ? " · no shipped editor" : "");
  m.appendChild(sub);

  const d = await tableData(id);
  const rows = filterRows(d.columns, d.rows);
  const bar = el("div","bar");
  if (t.derived) bar.appendChild(el("span","chip derived","derived"));
  else bar.appendChild(el("span","chip " + t.confidence, t.confidence));
  if (t.blob) bar.appendChild(el("span","kv", "varbin " + t.blob));
  m.appendChild(bar);
  m.appendChild(pager(rows.length));
  m.appendChild(makeTable(d.columns, rows, rows.length));
}

async function renderAssets(){
  const m = $("#main"); m.textContent = "";
  m.appendChild(el("h2",null,"Asset registry"));
  m.appendChild(el("p","lede",
    "SysFileVer.var is the engine's own registry: " + M.stats.assets.toLocaleString() +
    " records giving each asset its path, group and id. Hashing those paths is " +
    "what recovered 96 archive filenames that token harvesting could not reach."));
  const d = await assetData();
  const rows = filterRows(d.columns, d.rows);
  m.appendChild(pager(rows.length));
  m.appendChild(makeTable(d.columns, rows, rows.length));
}

function renderMaps(){
  const m = $("#main"); m.textContent = "";
  m.appendChild(el("h2",null,"Maps"));
  m.appendChild(el("p","lede",
    "Six .zmap scene documents — but not six maps: each file is one 256 m "+
    "TILE of a single world. def.gbc defines MAKEMAPID(ix,iy) = ix + iy*1000 "+
    "and world.gbc streams a (2*maprs+1)\u00b2 neighbourhood of tiles around "+
    "the player, so the file stem is the grid cell. These six are a 3\u00d72 "+
    "block (ix 493\u2013495, iy 517\u2013518) = 768\u00d7512 m of one world, which is "+
    "why every file is named w100 inside and still distinct on disk. Their "+
    "heights agree across every shared edge to \u226430 mm."));
  const wl = el("div","map");
  const wlImg = el("img");
  wlImg.src = "../maps/world_intro_layout.png";
  wlImg.alt = "stitched world layout";
  wlImg.style.width = "100%";
  wl.appendChild(wlImg);
  m.appendChild(wl);
  // the composed 3D renders: terrain + scenery instances + fixed placements,
  // textured, one oblique/plan/horizon triple per tile and one per world.
  // They are named after the tile STEM (intro517495), not the archive hash
  // the relief/layer previews use, so take it from the map path.
  const stemOf = mp => (mp.path || "").split("/").pop().replace(/\.zmap$/, "");
  const triples = [["world_intro", "stitched world"]];
  for (const mp of M.maps) triples.push([stemOf(mp), mp.blob]);
  const r = el("div","maps");
  for (const [stem, label] of triples){
    const c = el("div","map");
    const imgs = el("div","imgs");
    for (const [tag, alt] of [["oblique","oblique"],["plan","plan"],["horizon","horizon"]]){
      const a = el("a"); a.href = "../maps/" + stem + "_3d_tex_" + tag + ".png";
      const img = el("img");
      img.src = "../maps/" + stem + "_3d_tex_" + tag + ".png";
      img.alt = label + " " + alt; img.loading = "lazy";
      a.appendChild(img); imgs.appendChild(a);
    }
    c.appendChild(imgs);
    c.appendChild(el("h4", null, label + " — textured 3D"));
    c.appendChild(el("div","m", "oblique · plan · horizon — click for full size"));
    r.appendChild(c);
  }
  m.appendChild(r);
  const g = el("div","maps");
  for (const mp of M.maps){
    const c = el("div","map");
    const imgs = el("div","imgs");
    const i1 = el("img"); i1.src = "../maps/" + mp.blob + "_relief.png";
    i1.alt = "height relief"; i1.loading = "lazy";
    const i2 = el("img"); i2.src = "../maps/" + mp.blob + "_layer.png";
    i2.alt = "terrain layers"; i2.loading = "lazy";
    imgs.appendChild(i1); imgs.appendChild(i2); c.appendChild(imgs);
    const b = el("div","body");
    b.appendChild(el("h4",null,
      (mp.path || mp.map_name) + "  ·  " + mp.blob));
    const mm = el("div","m");
    mm.innerHTML =
      (mp.id !== null && mp.id !== undefined
        ? "tile <b>ix " + mp.ix + " · iy " + mp.iy + "</b> (id " + mp.id +
          ") · world name <b>" + mp.map_name + "</b><br>"
        : "") +
      "version <b>" + mp.version + "</b> · height <b>" + mp.height.width +
      "×" + mp.height.height + "</b> (" + (mp.patches.count||2) + " patches)<br>" +
      "terrain <b>" + mp.layer.width + "×" + mp.layer.height + "</b> · " +
      "<b>" + mp.layers.length + "</b> layers · <b>" + mp.decorations +
      "</b> deco · <b>" + mp.models + "</b> models<br>" +
      "<span style='opacity:.75'>" + (mp.layers.join(", ") || "") + "</span>";
    b.appendChild(mm); c.appendChild(b); g.appendChild(c);
  }
  m.appendChild(g);
  const n = el("div","note");
  n.innerHTML = "<b>Height</b> is 129×258 bytes over " +
    "<code>DEFWORLD_MINZ..MAXZ</code> (−50…450 world units), i.e. two stacked " +
    "129×129 patches. <b>Terrain</b> is 256×512 cells of 24-bit colour. The " +
    "mountain patch looks like noise by entropy — it is quantisation dithering.";
  m.appendChild(n);
}

function renderModels(){
  const m = $("#main"); m.textContent = "";
  m.appendChild(el("h2",null,"Models & textures"));
  m.appendChild(el("p","lede",
    M.stats.meshes.wf + " .53wf mesh files hold " + M.stats.meshes.submeshes +
    " submeshes and " + M.stats.meshes.materials + " materials; " +
    M.stats.meshes.cmz + " .cmz skeletons carry the rigs. Positions are quantised " +
    "3 bytes per vertex against a per-mesh base/step header; skins are 20-byte " +
    "records with quantised positions, unit normals and a single bone influence."));
  const g = el("div","gal");
  const cards = [
    ["../models/showcase.png","Showcase","15 textured meshes, three angles each"],
    ["../models/models.html","Mesh gallery","all decoded .53wf submeshes"],
    ["../models/textures.html","Texture gallery","GUID → filename, 5,672 pairs"],
    ["../models/skeletons.png","Skeletons","9 .cmz rigs with bind pose"],
    ["../models/skin_sheet.png","Skinned parts","14 character/equipment pieces"],
    ["../models/character.png","Character","humanoid rig, 1.754 m"],
  ];
  for (const [src, title, cap] of cards){
    const a = el("a"); a.href = src;
    const img = el("img"); img.src = src; img.loading = "lazy"; img.alt = title;
    a.appendChild(img);
    const c = el("div","cap"); c.textContent = title;
    c.appendChild(el("span",null,cap)); a.appendChild(c);
    g.appendChild(a);
  }
  m.appendChild(g);
}

function renderScripts(){
  const m = $("#main"); m.textContent = "";
  m.appendChild(el("h2",null,"Scripts"));
  const s = M.stats.scripts;
  m.appendChild(el("p","lede",
    "The client ships its entire UI as " + s.files + " uncompiled GBK scripts — " +
    s.lines.toLocaleString() + " lines of a C-like declarative DSL, " +
    s.classes.toLocaleString() + " classes, " + s.methods.toLocaleString() +
    " methods. The index is line-anchored and carries the runtime interface " +
    "graph, which is the real dependency structure (" +
    s.iface_edges.toLocaleString() + " edges, vs " + s.inc_edges.toLocaleString() +
    " for #include)."));
  const g = el("div","gal");
  for (const [href, title, cap] of [
      ["../gbc_index/index.html","Script index",
       "235 files, classes, interfaces, three dependency graphs"],
      ["../gbc_index/strings.html","Localisation inventory",
       "16,272 user-visible strings, 8,720 of them localised"]]) {
    const a = el("a"); a.href = href;
    const c = el("div","cap"); c.textContent = title;
    c.appendChild(el("span",null,cap)); a.appendChild(c);
    g.appendChild(a);
  }
  m.appendChild(g);
}

/* ---------- router ---------- */
function render(){
  if (view.table && !M.tables.some(t => t.id === view.table)) view = {kind:"overview"};
  buildNav();
  const q = $("#q");
  const searchable = view.kind === "tables" || view.kind === "assets";
  q.style.display = searchable ? "" : "none";
  if (!searchable) filter = "";
  if (view.table) renderTable(view.table);
  else if (view.kind === "overview") renderOverview();
  else if (view.kind === "assets") renderAssets();
  else if (view.kind === "maps") renderMaps();
  else if (view.kind === "models") renderModels();
  else if (view.kind === "scripts") renderScripts();
  window.scrollTo(0,0);
}

$("#q").addEventListener("input", e => { filter = e.target.value; page = 0; render(); });

/* theme */
const root = document.documentElement;
const saved = localStorage.getItem("ct-theme");
if (saved) root.dataset.theme = saved;
$("#theme").onclick = () => {
  const next = root.dataset.theme === "light" ? "dark" : "light";
  root.dataset.theme = next; localStorage.setItem("ct-theme", next);
};

$("#hdrsub").textContent =
  M.stats.tables + " tables · " + M.stats.table_rows.toLocaleString() +
  " rows · " + M.stats.assets.toLocaleString() + " assets";
render();
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
