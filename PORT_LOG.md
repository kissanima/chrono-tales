# PORT_LOG

Handoff log. Newest entry on top. Keep each entry under 20 lines.

---

## 2026-10-20 (later) — two agents: asset manifest + `.caf`

**Status:** DONE

**What was done:** Split into two parallel tracks.
*Manifest track:* `unknown/3602e346.bin` in both archives is a `.var` with **seed
100** (`0xFFABC257+0x64`) holding the packer's file list. `tools/asset_manifest.py`
→ names **327→433/484**, 0 real conflicts. Shows netpak ships **97 of 8,672**
listed paths (1.1%) — a stripped retail cut, which is why no name→GUID table
exists. Fixed a silent bug: `payloads()` globbed `unknown/*.zip` and lost 6
payloads (48→42) once names were applied; now discovers by content.
*Animation track:* `tools/caf_decode.py` + `caf_validate.py`, spec §4.9. Five
`.caf` are stock Cal3D, byte-exact. All bind rig `2682c5d5`.
Added `tools/check_all.py`: **14 invariants**, verified to fail on injection.

**Next Steps:**
1. `ziptabedit` (`af3407ec`) IS the animation binding: 12 rows × 6 cols,
   `系统名 / 原始动作包 / 附加动作包 / 查找索引 / 附加动作名 / 替换动作`, 111 distinct
   clip names. Row 1 = `jumpdown, jumping, jumpstart, walk, death, fly, fly_stand`
   and `stand01` is its `替换动作` fallback idle. **The open link is 系统名 →
   model GUID**, and `查找索引` only holds `模板|人类男` — a wildcard. Look in
   `npctabedit` / `peteditlist`, not in animation data.
2. Render an animated pose from `.caf` + a bound `.cmz` (no renderer does this yet).
3. `netpak/effect/*.txt` particle format — plaintext INI, 3 files, unread.

**Blockers:** None. Do not trust prose counts over `check_all.py`; two errors
this session came from quoting remembered figures (43 vs 406 action packs).
EOF
