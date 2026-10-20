# PORT_LOG

Handoff log. Newest entry on top. Keep each entry under 20 lines.

---

## 2026-10-20 — project scan + frontier survey

**Status:** DONE

**What was done:** Read README, GPAK_FORMAT.md, memory. Verified state by running
tools: `gpak_repack.py roundtrip` (0 differing bytes / 7,749,632) and
`fresh` on sysnet (336/336 payloads, 2704/2704 CRCs). Both writer paths correct.
Surveyed 4 unexploited asset classes: Cal3D animation (`.caf`), the 6
`netpak/intro/*.zmap` scenes, 7 named-table `.bin`s behind the `BE119407` wrapper,
and the plaintext `effect/*.txt` particle format. Details in
`.workbuddy-ai/memory/2026-10-20.md`.

**Next Steps:** Highest value first —
1. `tools/caf_decode.py` — `.caf` is Cal3D (QTrack vector+rot+trans, 1000 ticks/s);
   ground truth available from libcal3d source. 5 clips, 0 documented.
2. `tools/intro_map_decode.py` — reuse `varfile.Reader(plain=True)`; the 6 intro
   scenes already parse byte-exactly, only the 426,779 B blob is uninterpreted.
3. Extend `map_decode.py` / `varfile` to the `BE119407`-wrapped `.bin` tables.
4. `tools/regen_all.py` + a single `make check` — 64 scripts, 1 validator, no CI.

**Blockers:** None. `.cmz`-side animation binding is unclear: only 5 `.caf` files
ship and `netpak/action/` is a single hash-named ZIP, so the model->clip mapping
is not in the archives — check `SetAniKind`/`OnPlayAction` in the exe before
assuming clip names resolve by filename.
