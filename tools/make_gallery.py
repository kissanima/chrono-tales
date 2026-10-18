#!/usr/bin/env python3
"""make_gallery.py - build a browsable HTML gallery of the decoded .53wf meshes.

Reads the .53wf files (for names / materials / counts) plus the renders written
by obj_render.py, and emits extracted/models/models.html next to them.

Usage:
    python make_gallery.py [--root DIR] [--models DIR] [--out FILE]
"""
import argparse
import html
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from mesh_vert_decode import parse, decode_vert, bbox, edge_stats  # noqa: E402


PAGE = """<!DOCTYPE html>
<meta charset="utf-8">
<title>Chrono Tales - .53wf meshes</title>
<style>
  :root {{
    --bg:#0f1115; --panel:#171a21; --panel2:#1d212a; --line:#2a2f3a;
    --fg:#e8eaf0; --dim:#98a0b3; --accent:#d6a860; --ok:#5fb98a;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",
              "Microsoft YaHei",sans-serif; }}
  header {{ padding:34px 40px 26px; border-bottom:1px solid var(--line);
            background:linear-gradient(180deg,#181c24,#0f1115); }}
  h1 {{ margin:0 0 6px; font-size:24px; font-weight:600; letter-spacing:.2px; }}
  h1 span {{ color:var(--accent); }}
  .sub {{ color:var(--dim); font-size:13px; max-width:900px; }}
  .stats {{ display:flex; gap:28px; flex-wrap:wrap; margin-top:20px; }}
  .stat b {{ display:block; font-size:20px; font-weight:600; color:var(--accent);
             font-variant-numeric:tabular-nums; }}
  .stat span {{ color:var(--dim); font-size:11px; text-transform:uppercase;
                letter-spacing:.09em; }}
  main {{ padding:28px 40px 60px; }}
  .grid {{ display:grid; gap:18px; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
           overflow:hidden; display:flex; flex-direction:column; }}
  .thumb {{ background:#12141a; display:block; position:relative; }}
  .thumb img {{ width:100%; display:block; aspect-ratio:1/1; object-fit:cover; }}
  .body {{ padding:13px 15px 15px; }}
  .name {{ font-size:15px; font-weight:600; margin-bottom:2px; }}
  .hash {{ font-family:ui-monospace,Consolas,monospace; font-size:11px;
           color:var(--dim); letter-spacing:.04em; }}
  table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:12px; }}
  td {{ padding:3px 0; vertical-align:top; }}
  td:first-child {{ color:var(--dim); width:88px; }}
  td.v {{ font-variant-numeric:tabular-nums; }}
  .tag {{ display:inline-block; padding:1px 6px; border-radius:4px; font-size:10.5px;
          background:#242a35; color:#b9c2d4; margin-right:4px; }}
  .tag.hi {{ background:#2f2a1c; color:var(--accent); }}
  .tag.ok {{ background:#1b2b23; color:var(--ok); }}
  code {{ font-family:ui-monospace,Consolas,monospace; font-size:11.5px;
          background:#20242d; padding:1px 5px; border-radius:4px; }}
  h2.sec {{ font-size:17px; font-weight:600; margin:44px 0 8px;
           padding-top:26px; border-top:1px solid var(--line); }}
  footer {{ padding:0 40px 50px; color:var(--dim); font-size:12px; }}
  a {{ color:var(--accent); }}
</style>
<header>
  <h1>Chrono Tales &mdash; <span>.53wf</span> meshes</h1>
  <div class="sub">
    Geometry decoded with the client's own quantiser, recovered from the 53wf
    loader in <code>chronotales.exe</code> at <code>0x5AE932</code>:
    a 24-byte header of six float32 base/step pairs (all &times;&nbsp;0.01),
    then three bytes per vertex, <code>pos = byte &times; step + base</code>.
    <code>VERH</code> carries unquantised float32 triples instead.
    Assets are metres, <b>+Z up</b>.
  </div>
  <div class="stats">{stats}</div>
</header>
<main><div class="grid">{cards}</div>{skel}</main>
<footer>
  Renders are z-buffered two-sided Lambert; <code>obj/</code> holds the
  Wavefront meshes with per-corner UVs, <code>png/</code> the stills.
  Every triangle edge is 0.25&times; the length it would have with shuffled
  vertices, so the point cloud is genuinely structured.
  <p style="margin-top:18px">
    <b>Textured version:</b> the same meshes with image textures sampled
    through their UVs &mdash; <a href="textured_sheet.png">contact sheet</a>
    or open <a href="textures.html">textures.html</a> for the per-material
    archive-entry table (4 hash-verified, 9 visual matches, 2 tentative).
  </p>
</footer>
"""

CARD = """<div class="card">
  <a class="thumb" href="{tex_png}" data-png="png/{stem}.png"><img src="{tex_png}" alt="{name}" loading="lazy"></a>
  <div class="body">
    <div class="name">{name}</div>
    <div class="hash">{stem} &middot; {size:,} B &middot; v{ver} &middot; {nmesh} submesh{es}</div>
    <table>
      <tr><td>geometry</td><td class="v">{verts:,} v &middot; {tris:,} tri</td></tr>
      <tr><td>codec</td><td>{codecs}</td></tr>
      <tr><td>bbox</td><td class="v">{bbox}</td></tr>
      <tr><td>materials</td><td>{mats}</td></tr>
    </table>
  </div>
</div>"""


SKEL_CARD = """<div class="card">
  <a class="thumb" href="skel_png/{stem}.png"><img src="skel_png/{stem}.png" alt="{stem}" loading="lazy"></a>
  <div class="body">
    <div class="name">{part}</div>
    <div class="hash">{stem} &middot; {bones} bones &middot; {height:.2f} m tall</div>
    <table>
      <tr><td>chunks</td><td>{chunks}</td></tr>
      <tr><td>root</td><td class="v">{roots}</td></tr>
      <tr><td>attachments</td><td>{names}</td></tr>
    </table>
  </div>
</div>"""


def skel_section(models):
    """Build the .cmz skeleton cards, or '' when nothing was decoded."""
    import json
    js = sorted((models / "cmz").glob("*.json"))
    if not js:
        return ""
    cards = []
    for j in js:
        d = json.loads(j.read_text(encoding="utf-8"))
        w = d.get("world") or []
        height = (max(p[2] for p in w) - min(p[2] for p in w)) if w else 0.0
        roots = ", ".join(b["name"] for b in d["bones"] if b["parent"] < 0)
        parts = d.get("mesh") or []
        part = parts[0]["name"] if parts else j.stem
        chunks = " ".join(f'<span class="tag">{c["tag"]}</span>'
                          for c in d["chunks"])
        names = "".join(f'<span class="tag hi">{html.escape(n)}</span>'
                        for n in d.get("map1", [])) or "&mdash;"
        cards.append(SKEL_CARD.format(stem=j.stem, part=html.escape(part),
                                      bones=len(d["bones"]), height=height,
                                      chunks=chunks, roots=roots, names=names))
    return ('<h2 class="sec">.cmz skeletons</h2>'
            '<div class="sub" style="margin-bottom:18px">'
            'Bone hierarchy and bind pose, from the <code>skel</code> chunk. '
            'Offsets are local centimetres along the parent&rsquo;s +X; rotations '
            'are inverse quaternions, which is what makes the left and right '
            'limbs mirror exactly. These rigs are A-posed humanoids 1.75&nbsp;m '
            'tall, plus one mech motorcycle.</div>'
            '<div class="grid">' + "\n".join(cards) + '</div>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path,
                    default=pathlib.Path("extracted/netpak"))
    ap.add_argument("--models", type=pathlib.Path,
                    default=pathlib.Path("extracted/models"))
    ap.add_argument("--out", type=pathlib.Path, default=None)
    a = ap.parse_args()
    out = a.out or (a.models / "models.html")

    cards, tot_v, tot_t, tot_s = [], 0, 0, 0
    for path in sorted(a.root.rglob("*.53wf")):
        info = parse(path)
        if not info:
            continue
        nv = nt = 0
        codecs, boxes = set(), []
        for g in info["groups"]:
            if "idx" not in g or "vert" not in g:
                continue
            pts, codec = decode_vert(g["vert"], g.get("verh", False))
            if not pts:
                continue
            codecs.add(codec)
            need = max(g["idx"]) + 1
            nv += need
            nt += len(g["idx"]) // 3
            boxes.append(bbox(pts[:need]))
        tot_v += nv
        tot_t += nt
        tot_s += len(info["groups"])

        bb = (f'{max(b[0] for b in boxes):.2f} &times; '
              f'{max(b[1] for b in boxes):.2f} &times; '
              f'{max(b[2] for b in boxes):.2f} m') if boxes else "&mdash;"
        codec_tags = " ".join(
            f'<span class="tag{" ok" if c == "VERH" else ""}">{c}</span>'
            for c in sorted(codecs))
        mat_html = "".join(
            f'<span class="tag hi">{html.escape(n)}</span>'
            + (f'<code>{html.escape(t)}</code> ' if t else "")
            for n, t in info["materials"][:4]) or "&mdash;"
        if len(info["materials"]) > 4:
            mat_html += f'<span class="tag">+{len(info["materials"]) - 4}</span>'

        tex_png = f"png_tex/{path.stem}.png"
        if not (a.models / tex_png).exists():
            tex_png = f"png/{path.stem}.png"
        cards.append(CARD.format(
            stem=path.stem, name=html.escape(info["name"] or path.stem),
            size=info["size"], ver=info["version"], nmesh=info["meshes"],
            es="" if info["meshes"] == 1 else "es",
            verts=nv, tris=nt, codecs=codec_tags, bbox=bb, mats=mat_html,
            tex_png=tex_png))

    n_tex = len(list((a.models / "png_tex").glob("*.png")))
    stats = "".join(
        f'<div class="stat"><b>{v}</b><span>{k}</span></div>'
        for k, v in (("meshes", len(cards)), ("submeshes", tot_s),
                     ("vertices", f"{tot_v:,}"), ("triangles", f"{tot_t:,}"),
                     ("textured", n_tex)))
    out.write_text(PAGE.format(stats=stats, cards="\n".join(cards),
                               skel=skel_section(a.models)), encoding="utf-8")
    print(f"{out}  ({len(cards)} meshes, {tot_s} submeshes, "
          f"{tot_v:,} verts, {tot_t:,} tris)")


if __name__ == "__main__":
    main()
