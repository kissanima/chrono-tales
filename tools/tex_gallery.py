"""tex_gallery.py - document every material/mesh -> texture link."""
import json
import pathlib
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TMAP = json.load(open(ROOT / "extracted" / "models" / "texmap.json", encoding="utf-8"))

MESH_NAMES = {}
for p in (ROOT / "extracted" / "models").glob("*.json"):
    try:
        h = int(p.stem, 16)
    except ValueError:
        continue
    d = json.load(open(p, encoding="utf-8"))
    n = d.get("mesh_names")
    MESH_NAMES[h] = n[0] if n else "(no name)"


def thumb_path(entry):
    return ROOT / "extracted" / entry[0] / entry[1]


PAGE = """<!DOCTYPE html>
<meta charset="utf-8">
<title>Chrono Tales - texture map</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#2a2f3a;
    --fg:#e8eaf0; --dim:#98a0b3; --accent:#d6a860; --ok:#5fb98a; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
    font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",
    "Microsoft YaHei",sans-serif; }
  header { padding:34px 40px 26px; border-bottom:1px solid var(--line); }
  h1 { margin:0 0 6px; font-size:24px; }
  h1 span { color:var(--accent); }
  .sub { color:var(--dim); font-size:13px; max-width:920px; }
  .stats { display:flex; gap:28px; flex-wrap:wrap; margin-top:18px; }
  .stat b { display:block; font-size:18px; color:var(--accent); }
  .stat span { color:var(--dim); font-size:11px; text-transform:uppercase;
    letter-spacing:.09em; }
  main { padding:26px 40px 60px; }
  h2 { margin:38px 0 14px; font-size:16px; font-weight:600;
    border-left:3px solid var(--accent); padding-left:10px; }
  table { width:100%; border-collapse:collapse; font-size:12.5px;
    background:var(--panel); border:1px solid var(--line); border-radius:10px;
    overflow:hidden; }
  th, td { padding:9px 12px; text-align:left; vertical-align:middle; }
  th { background:#1a1e26; color:var(--dim); font-weight:500;
    font-size:11.5px; text-transform:uppercase; letter-spacing:.06em; }
  tr + tr td { border-top:1px solid var(--line); }
  td.thumb img { width:78px; height:78px; object-fit:cover;
    border-radius:6px; display:block; }
  td.name { font-weight:600; }
  td.mono { font-family:ui-monospace,Consolas,monospace; font-size:11.5px;
    color:var(--dim); }
  .conf { display:inline-block; padding:1px 7px; border-radius:10px;
    font-size:10.5px; letter-spacing:.04em; }
  .conf.hash   { background:#1b2b23; color:var(--ok); }
  .conf.visual { background:#2f2a1c; color:var(--accent); }
  .conf.tent   { background:#2b1f1c; color:#d68a60; }
  .conf.mesh   { background:#1c2530; color:#9ab8d6; }
  p.note { color:var(--dim); font-size:13px; max-width:880px; }
  .legend { display:flex; gap:14px; flex-wrap:wrap; margin:14px 0 0; }
  .legend .conf { font-size:11px; padding:2px 9px; }
</style>
<header>
  <h1>Texture map <span>- 15 material names linked to archive entries</span></h1>
  <p class="sub">
    The archive is indexed by the MPQ hash of a path whose filename is an
    8-hex-digit art-pipeline GUID, so the material names inside the meshes
    are not the keys used at load time.  This page is the recovered link
    table: which material name was resolved by which route, and what archive
    file the path <code>tex/&lt;GUID&gt;.&lt;ext&gt;</code> resolves to.
  </p>
  <div class="stats">
    <div class="stat"><b>__N_MAT__</b><span>material names</span></div>
    <div class="stat"><b>__N_MESH__</b><span>mesh-level fallbacks</span></div>
    <div class="stat"><b>__N_H__</b><span>hash-verified</span></div>
    <div class="stat"><b>__N_V__</b><span>visual match</span></div>
    <div class="stat"><b>__N_T__</b><span>tentative</span></div>
  </div>
  <div class="legend">
    <span class="conf hash">hash - resolved via the entity DB (varbin decoration table)</span>
    <span class="conf visual">visual - content match (alpha cutout extension, unambiguous art)</span>
    <span class="conf tent">tentative - plausible fit, not independently confirmed</span>
    <span class="conf mesh">mesh-level - applies to a mesh that ships with no material table</span>
  </div>
  <p class="sub" style="margin:12px 0 0">
    → <a href="showcase.png" style="color:var(--accent)">3-view textured showcase</a>
    · <a href="textured_sheet.png" style="color:var(--accent)">contact sheet</a>
  </p>
</header>
<main>
  <h2>By material name</h2>
  <table>
    <tr><th></th><th>material</th><th>archive file</th><th>GUID</th><th>confidence</th><th>via</th></tr>
    __ROWS_NAME__
  </table>
  <h2>By mesh (no material table)</h2>
  <table>
    <tr><th></th><th>mesh</th><th>archive file</th><th>confidence</th><th>via</th></tr>
    __ROWS_MESH__
  </table>
  <h2>How the links were recovered</h2>
  <p class="note">
    Hash-verified: the entity database (varbin) stores decoration records as
    <code>model/&lt;GUID&gt;.fw3</code> + <code>tex/&lt;GUID&gt;.&lt;ext&gt;</code> pairs.
    Those GUIDs hash to real archive entries, and the material's filename
    extension always matches the archive entry's extension (verified on
    4/4 pairs), so the table can be crossed against the decoded materials.
  </p>
  <p class="note">
    Visual: the material extension picks the format (PNG materials are
    alpha cutouts, JPG opaque), cutting candidates to the 10 model-texture
    PNGs; the remaining names (<code>\u65b0\u624b\u6751\u85e4</code>,
    <code>\u65b0\u624b\u6751\u6811\u53f6</code>,
    <code>\u65b0\u624b\u6751\u6811\u53f61</code>, plus the mesh misnamed
    <code>\u5c0f\u9547\u95e8</code> which is a drooping flower plant) line
    up with the art one-for-one.
  </p>
  <p class="note">
    Tentative: the architectural names (<code>\u672801</code>/<code>\u672802</code>,
    <code>\u987601</code>/<code>\u987602</code>, <code>\u589901</code>) fit
    by role (roof tiles / beams / brick) and the mushroom-house render
    confirms they read correctly.  The bark and altar assignments are
    best-guess.
  </p>
</main>
"""


def make_thumb(entry, w=78):
    p = thumb_path(entry)
    try:
        im = Image.open(p).convert("RGBA")
        bg = Image.new("RGBA", im.size, (40, 44, 52, 255))
        bg.alpha_composite(im)
        im = bg.convert("RGB")
        im.thumbnail((w, w))
        out = ROOT / "extracted" / "models" / "_thumbs" / (p.stem + ".png")
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out)
        return "../_thumbs/" + p.stem + ".png"
    except Exception:
        return ""


def row_name(name, v):
    t = make_thumb(v["tex_entry"])
    conf = v.get("confidence", "hash" if v.get("verified") else "tent")
    h = f"{v['tex_hash']:08x}"
    p = "/".join(v["tex_entry"])
    via = v.get("via", "")
    return (f"    <tr><td class=thumb><img src='{t}'></td>"
            f"<td class=name>{name}</td>"
            f"<td class=mono>{p}</td>"
            f"<td class=mono>{h}</td>"
            f"<td><span class='conf {conf}'>{conf}</span></td>"
            f"<td class=mono>{via}</td></tr>")


def row_mesh(h, v):
    n = MESH_NAMES.get(int(h, 16), "?")
    t = make_thumb(v["tex_entry"])
    conf = "hash" if v.get("verified") else "mesh"
    p = "/".join(v["tex_entry"])
    return (f"    <tr><td class=thumb><img src='{t}'></td>"
            f"<td class=name>{n} <span class=mono>({h})</span></td>"
            f"<td class=mono>{p}</td>"
            f"<td><span class='conf {conf}'>{conf}</span></td>"
            f"<td class=mono>{v.get('via','')}</td></tr>")


def main():
    byname = TMAP["by_name"]
    bymesh = TMAP["by_mesh"]
    rows_n = "\n".join(row_name(k, v) for k, v in sorted(byname.items()))
    rows_m = "\n".join(row_mesh(k, v) for k, v in sorted(bymesh.items()))
    n_h = sum(1 for v in byname.values() if v.get("verified"))
    n_v = sum(1 for v in byname.values()
              if not v.get("verified") and v.get("confidence") == "visual")
    n_t = sum(1 for v in byname.values()
              if not v.get("verified") and v.get("confidence") == "tentative")
    out = (PAGE
           .replace("__N_MAT__", str(len(byname)))
           .replace("__N_MESH__", str(len(bymesh)))
           .replace("__N_H__", str(n_h))
           .replace("__N_V__", str(n_v))
           .replace("__N_T__", str(n_t))
           .replace("__ROWS_NAME__", rows_n)
           .replace("__ROWS_MESH__", rows_m))
    dst = ROOT / "extracted" / "models" / "textures.html"
    dst.write_text(out, encoding="utf-8")
    print(f"-> {dst}  ({len(byname)} materials, {len(bymesh)} mesh-level)")


if __name__ == "__main__":
    main()
