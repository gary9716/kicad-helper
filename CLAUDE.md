# kicad-helper

Python CLI tool to automate KiCad schematic and symbol library work: symbol generation, collision-free placement, orthogonal wire routing, hierarchical module extraction, wire simplification, and SPICE simulation.

## Build

No separate build step — the project is a plain Python package managed with `uv`.

```bash
# Install dependencies (if any are added)
uv sync
```

## Test

```bash
uv run python -m unittest discover -s tests -v
```

## Binary

```
/Users/ktchou/kicad-helper/kicad-helper
```

Run commands as:
```bash
/Users/ktchou/kicad-helper/kicad-helper <subcommand> [args]
```

## Skills

- CLI usage and subcommand reference: `skills/kicad-helper/SKILL.md`
- Netlist ground-truth planning and evaluation: `.claude/skills/plan-ground-truth-netlist/SKILL.md`

## Convention: ceramic capacitors and generic resistors keep KiCad's stock footprint

Do NOT `fetch-easyeda` a dedicated symbol/footprint library for plain ceramic
capacitors (C0G/X7R/X5R, `Device:C` symbol) or generic resistors
(`Device:R` symbol). These always use KiCad's own stock symbol
(`Device:C` / `Device:R`) and stock footprint (`Capacitor_SMD:C_XXXX_YYYYMetric`
/ `Resistor_SMD:R_XXXX_YYYYMetric` matching the real package size) — only the
`LCSC` property is set to the sourced part number. The stock footprint is
correct for any manufacturer's part in that package size; there's no real
footprint variance worth chasing a dedicated EasyEDA import for.

This does NOT apply to: polarized/tantalum capacitors (`Device:C_Polarized`),
inductors, diodes, ICs, connectors, or anything with a non-generic package —
those still get the full `fetch-easyeda` treatment (real symbol + footprint +
3D model) per the rest of this file.

## Schematic Routing Rule (bypass capacitors)

Place bypass capacitors as close as possible to the power/ground pins they filter. Do not route the bypass cap across other signal paths.

## PCB Net Class Conventions (1 oz copper)

| Net Class | Clearance | Track Width | Via Size | Via Drill | Min Vias |
|---|---|---|---|---|---|
| Power_1A | 0.3 mm | 1.0 mm | 0.7 mm | 0.35 mm | 1 |
| Power_2A | 0.4 mm | 2.0 mm | 0.8 mm | 0.4 mm | 2 |
| Power_3A | 0.4 mm | 3.2 mm | 0.8 mm | 0.4 mm | 3 |

## PCB Net Class Conventions (2 oz copper)

| Net Class | Clearance | Track Width | Via Size | Via Drill | Min Vias |
|---|---|---|---|---|---|
| Power_1A | 0.35 mm | 0.5 mm | 0.7 mm | 0.35 mm | 1 |
| Power_2A | 0.4 mm | 1.0 mm | 0.8 mm | 0.4 mm | 2 |
| Power_3A | 0.45 mm | 1.6 mm | 0.8 mm | 0.4 mm | 3 |

## Trap: fixing LCSC/Value in the schematic alone does not fix the JLCPCB BOM

A component's `LCSC`/`Value`/`Footprint` properties exist in **three separate
places** that do NOT auto-sync:
1. `<sheet>.kicad_sch` — the schematic symbol properties.
2. `<project>.kicad_pcb` — each footprint carries its OWN copy of `Value`/
   `LCSC` properties, independent of the schematic. **This is the real
   source of truth for BOM generation** — editing only the schematic and
   leaving the PCB stale is the most common cause of "I fixed it but the
   BOM still shows the old part."
3. `<project>/jlcpcb/project.db` (sqlite, table `part_info`, columns
   `reference, value, footprint, lcsc, ...`) — the KiCad JLCPCB-Tools plugin
   (`bouni/kicad-jlcpcb-tools`) caches BOM/CPL data here. Its
   `update_from_board()` (store.py) reads footprints straight off the
   **PCB** (`fp.GetValue()`, LCSC field) — not the schematic — and
   overwrites this cache on every scan. So even closing/reopening KiCad
   does not fix a stale entry if the PCB itself was never updated.

Fix order, always PCB before cache:
1. Fix the schematic property (source of design intent).
2. If the footprint/package size is unchanged, you can safely hand-edit the
   matching `(property "Value" ...)` / `(property "LCSC" ...)` block for
   that reference directly in the `.kicad_pcb` text (s-expression, same
   syntax as `.kicad_sch`).
3. If the footprint/package size CHANGED (e.g. 0603→1210 for higher
   voltage/capacitance), do NOT hand-edit the PCB — the footprint block is
   real pad/courtyard/silkscreen geometry, not just a label. Have the user
   run KiCad's **Tools → Update PCB from Schematic** to swap it properly.
4. Only after the PCB is correct, sync the cache:
   ```bash
   sqlite3 <project>/jlcpcb/project.db \
     "UPDATE part_info SET value='<value>', lcsc='<lcsc>' WHERE reference='<ref>';"
   ```
Skipping step 2/3 (PCB) means the plugin will silently re-derive the old
value from the board on its next scan and stomp the cache fix again.

## Convention: `fetch-easyeda` registers to the GLOBAL library table, not project

Always run `fetch-easyeda` / `import-lib` **without** `--project` (i.e. let it
default to the global `sym-lib-table`/`fp-lib-table` at
`~/Library/Preferences/kicad/<version>/`), even when the work is scoped to
one project. This project's whole library history (`TPS54360DDAR`,
`SLD22U_017_B`, `DMP6050SFG`, `ul_TMUX1112PWR`, etc.) lives in the global
table — none of it is in any project-local `fp-lib-table`.

Using `--project` instead writes to `<project>/fp-lib-table`, and KiCad
(observed on 10.0) does not reliably pick that up even after fully quitting
and relaunching — footprint lookups from tools like "Update PCB from
Schematic" fail with `footprint 'X' not found` even though the file and
table entry are both present and well-formed. Symptom looks like a stale
cache, but it's actually the wrong table. Re-registering the same library to
the global table (`fetch-easyeda ... --force`, no `--project`) fixes it.

## Part sourcing: LCSC storefront stock ≠ JLCPCB assembly stock

`lcsc.com/product-detail/C#####.html` shows LCSC's general **retail/distributor**
stock. JLCPCB's PCBA line pulls from a **separate** internal parts inventory
that can show a part as available even when the LCSC storefront says
"Out of Stock" (and vice versa) — they are not the same number. Checking only
the LCSC product page before writing a part into a BOM meant for JLCPCB
assembly can give a false "no stock" and waste time hunting for a substitute
that wasn't needed.

Use the **`sourcing-smt-parts` skill** (`skills/sourcing-smt-parts/SKILL.md`)
for any LCSC-part-number/stock lookup — it queries `jlcsearch.tscircuit.com`
(JSON, no key, JLCPCB-assembly-scoped) first, e.g.:
```
https://jlcsearch.tscircuit.com/resistors/list.json?resistance=200&package=1206
```
This returns the JLCPCB-assembly stock directly, already filtered to parts
JLCPCB can place — skip guessing candidate LCSC numbers one at a time and
checking each on lcsc.com. Also has `scripts/check_jlc_stock.py` in this repo
for the same purpose. Fall back to LCSC's site/OpenAPI only per the skill's
layer order (jlcsearch → yaqwsx/jlcparts → LCSC OpenAPI → EasyEDA endpoint).

## Bug: `fetch-easyeda` writes a dead temp-folder path into the 3D model reference

Every footprint `fetch-easyeda` has ever pulled (verified across 18 separate
libraries under `~/hardwares/Libraries/*/KiCADv6/footprints.pretty/*.kicad_mod`
in the sonar-device project — i.e. this isn't a one-off, it's every single
fetch) ships a `.kicad_mod` whose `(model "...")` line still points at the
**temporary staging directory** used during the fetch
(`/var/folders/.../tmpXXXXXXXX/raw.3dshapes/<name>.wrl`), not the permanent
install location (`~/hardwares/Libraries/<name>/KiCADv6/3dshapes/<name>.wrl`).
The temp dir is deleted once the command exits, so the 3D model silently
fails to load in the PCB 3D viewer / export — no error, the footprint just
renders flat with no model. Symbol and 2D footprint are unaffected, only the
3D model reference is wrong.

**Check after every fetch:**
```bash
grep -n '(model "' ~/hardwares/Libraries/<name>/KiCADv6/footprints.pretty/*.kicad_mod
```
If the path contains `/var/folders/`, it's broken — rewrite it to
`~/hardwares/Libraries/<name>/KiCADv6/3dshapes/<same filename>` (the real
file is already there under that path; only the reference in the
`.kicad_mod` text is wrong). One-liner to fix all libraries at once:
```python
import re, glob, os
for fp in glob.glob(os.path.expanduser("~/hardwares/Libraries/*/KiCADv6/footprints.pretty/*.kicad_mod")):
    text = open(fp).read()
    m = re.search(r'\(model "(/var/folders/[^"]*/([^/"]+))"', text)
    if not m: continue
    real = os.path.join(fp.split("/KiCADv6/footprints.pretty/")[0], "KiCADv6", "3dshapes", m.group(2))
    if os.path.isfile(real):
        open(fp, "w").write(text.replace(m.group(1), real))
```
This is a `kicad-helper` tool bug (in `fetch-easyeda`'s install step, likely
in `easyeda2kicad.py` or the wrapper's post-processing) — worth fixing at
the source so future fetches don't need this patched every time.
