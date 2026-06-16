# KiCad Schematic Generation — Domain Knowledge

Hard-won facts about generating KiCad 6+ `.kicad_sch` files programmatically, verified
against `kicad-cli` ERC and KiCad source. Read this before touching symbol generation,
placement, wiring, or connectivity code. Most of these were expensive to discover.

> **Golden rule:** KiCad's own ERC (`kicad-cli sch erc`) is the only authoritative
> connectivity check. A hand-rolled netlist model can be self-consistent yet wrong — it
> validates its own mistakes. Gate every generated schematic on ERC.

---

## 1. Coordinate system & the pin Y-flip

- The schematic canvas is **Y-down**. Symbol-library pin coordinates are **Y-up**.
- KiCad applies an **inherent vertical flip** when instantiating a symbol: a pin at local
  `+Y` (above the symbol origin in the library) lands **below** the instance origin.
- A pin's global position is therefore `gx = tx + px`, **`gy = ty − py`** (for angle 0,
  no mirror). Forgetting the `−py` mirrors every pin about its symbol origin.
- This bug is invisible to internal tooling because pin-reading *and* wire-emission share
  the same transform — everything stays self-consistent but mismatches KiCad. Only ERC
  catches it. On the MCP2515 demo the missing flip produced 18 `pin_not_connected` /
  `label_dangling` ERC errors.
- Code: `transform_pin_coordinate` in `kicad_skill/schematic.py` (negates `py` first, then
  applies mirror/rotation). `get_symbol_pins_global` (`kicad_skill/module.py`) delegates to
  it. Regression test: `tests/test_pin_transform.py`.

**Verification:** `get_symbol_pins_global` matches KiCad's ERC-reported pin positions
**exactly** (dx = dy = 0) for every pin once the flip is correct. KiCad reports an
unconnected pin's coordinate in the ERC output — that is ground truth for the connection
point.

---

## 2. How KiCad decides connection / dangling (from source)

From `SCH_LINE::UpdateDanglingState` (KiCad source, see
https://docs.kicad.org/doxygen/sch__line_8cpp_source.html):

- A wire endpoint is **connected** iff another connectable item sits at the **EXACT same
  coordinate** — `item.GetPosition() == endpoint`, **no tolerance**, must be precisely
  coincident.
- Connectable items for a wire: other **wire ends**, **junctions**, **labels** (all
  kinds), **symbol pins** (`PIN_END`), **sheet pins**.
- There is **no collinearity or direction check** — only coordinate coincidence.
- **The grid trap:** any sub-unit offset (e.g. an off-grid placement, a `%.3f` rounding
  mismatch, a symbol not snapped to the 1.27 mm grid) means the endpoint does *not*
  coincide → silent disconnection. Keep symbols, pins, wires, junctions, and labels all on
  the 1.27 mm grid. KiCad works internally in integer nanometres, so exact decimal strings
  that map to the same nm value do match.

**Practical consequence:** to connect a wire to a pin, the wire endpoint must land on the
pin's connection point to the nanometre. To connect two nets, place same-named labels at
the exact pin connection points.

**Caveat (see §4):** geometric coincidence as described above is necessary but, for
*programmatically emitted* wires, observed not to be sufficient — a wire between two pins is
still flagged `wire_dangling` unless the net also carries a label. Treat a label as required
on every wired net.

---

## 3. Labels vs wires

- A **local label** anchored exactly on a pin's connection point connects that pin to its
  net. Same-named local labels join across the sheet **by name** (no wire needed).
- Labels carry their own net name, so label-based connection is robust: KiCad attaches the
  net name wherever the label anchor coincides with a pin.
- **The label anchor must stay on the pin coordinate** to connect. You may freely change
  the label `angle` and `justify` (text direction) without breaking the connection — only
  the anchor position matters. Use this to orient label text **outward** from the body so
  it doesn't overlap the symbol or neighbouring pin text.
  - left-side pin → `(angle 0, justify right)` (text grows left)
  - right-side pin → `(angle 0, justify left)`
  - top-side pin → `(angle 90, justify left)` (text grows up)
  - bottom-side pin → `(angle 90, justify right)`
  - Side is the dominant axis of `(pin − symbol_center)`.
  - Code: `_label_orientation`, `_make_label`, `_emit_labels` in `kicad_skill/regenerate.py`.

---

## 4. Routing strategy (golden rule + the wire-to-IC-pin caveat)

Professional schematic convention, confirmed in practice:

- **IC ↔ IC** (normal symbol to normal symbol): use a **local label** on each pin. Avoids
  spider-web wiring across the sheet.
- **Symbol ↔ passive / power symbol / connector**: a short **wire** reads best.

**Label-less wire connections are fully supported:** A programmatically emitted wire between two pins is **not** reported as `wire_dangling` by KiCad ERC as long as the wire endpoints coincide exactly with the pin connection coordinates (to the nanometre) and the symbol definitions (`lib_symbols` in the `.kicad_sch` file) are correctly loaded.

**How the "wired nets need a label" misconception occurred:**
In earlier prototypes, certain generated schematics produced `wire_dangling` errors on label-less wires. This was initially misdiagnosed as an inherent KiCad requirement. However, further testing revealed:
- The true cause was that the symbol definitions in `lib_symbols` were either missing, misnamed, or failed to resolve via `sym-lib-table` when running `kicad-cli sch erc`.
- When KiCad cannot resolve a symbol's definition, it doesn't recognize that symbol's pins. Consequently, the wire endpoint lands on "empty space" from an electrical standpoint and is flagged as dangling.
- Adding a label acted as a workaround because the wire end connected to the label (which is a recognized connection item), clearing the `wire_dangling` flag, but masking the fact that the connection to the pin itself was unresolved.

**Verification:** When the symbol library and coordinate transforms are correct, a bare wire connects two pins with absolutely zero ERC errors.
Regression test: `tests/test_wiring_needs_label.py` confirms that a label-less wire between two resistors passes ERC cleanly with `0` dangling errors.

---

## 5. `lib_symbols` sub-symbol naming

A `.kicad_sch` embeds a `lib_symbols` cache. Each symbol definition nests child units:

```
(symbol "nickname:Name"
  (symbol "Name_0_1" ...)   ; body / graphics
  (symbol "Name_1_1" ...))  ; pins
```

- Children are prefixed with the **bare symbol name** (the part **after** the colon), NOT
  the library nickname. KiCad's own `Device:R` uses `R_0_1` / `R_1_1`, not `Device:R_0_1`.
- A generated symbol `nickname:MCP2515` correctly uses `MCP2515_0_1` / `MCP2515_1_1`.
- Getting this wrong makes KiCad ignore the cached pin definitions (pins appear to exist
  visually but ERC treats them as absent). It was *suspected* as the wire-dangling cause
  here but verified correct — do check it when pins seem invisible to ERC.

---

## 6. Generated symbol pin geometry

`generate_symbol_sexpr` (`kicad_skill/symbol.py`) emits pins as
`(pin TYPE line (at X Y ANGLE) (length L) (name ...) (number ...))`:

- `(at X Y)` is the pin's **connection point** (the free/outer end). The pin graphic
  extends `length` from there toward the body.
- ANGLE: `0` left side, `180` right side, `90` top side, `270` bottom side.
- Body and pin positions are kept on the 2.54 mm (and thus 1.27 mm) grid; off-grid pins
  silently fail to connect (§2).
- Pin types matter for ERC electrical checks (not topology): `power_in` pins must be driven
  by a `power_out` pin or a PWR_FLAG, else `power_pin_not_driven`. SPI MISO on a shared bus
  must be `tri_state`/`passive`, not `output`, to avoid driver conflicts.

---

## 7. Placement & label-aware spacing

- `place_symbols_and_resolve` snaps positions to grid and resolves symbol-vs-symbol
  overlaps using each symbol's body bounding box, pushing only the newly-placed symbols.
- Body boxes alone ignore label text, so long net-label text collides with neighbours.
  Pass `bbox_overrides` (a `{ref: BoundingBox}` map) padded by label text extents so
  resolution spreads symbols enough for labels. Pad in x for horizontal-side pins, in y for
  vertical-side pins; estimate text width ≈ `len(name) * 1.0 mm` at 1.27 mm font.
  - Code: `_label_padded_bboxes` in `kicad_skill/regenerate.py`,
    `place_symbols_and_resolve(..., bbox_overrides=...)` in `kicad_skill/schematic.py`.

---

## 8. Hierarchical sheets (flattening for connectivity)

- A parent sheet exposes `(pin "Name" ...)` on each `(sheet ...)` instance; the sub-sheet
  exposes matching `(hierarchical_label "Name" ...)`. They tie **by name**.
- `global_label`s join across all sheets **by name**.
- To extract the true flattened netlist, union: wire endpoints, same-named local labels
  within a sheet, parent sheet-pin ↔ child hierarchical-label by name, global labels by
  name across sheets.
- The hierarchical create-module pipeline shares the §4 router artifact (wires dangle on IC
  pins) and adds sheet-pin connectivity gaps. The flat regenerate path is preferred.

---

## 9. ERC as the gate (tooling)

- `kicad-cli sch erc --format json --output X.json --severity-error IN.kicad_sch` gives a
  machine-readable report: `sheets[].violations[]` with `type`, `severity`, `items[].pos`.
- Wrapper: `run_erc` / `find_kicad_cli` in `kicad_skill/erc.py`. Locates the CLI on PATH or
  common install dirs (e.g. `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`).
- Violation types seen: `pin_not_connected`, `wire_dangling`, `label_dangling`,
  `power_pin_not_driven`, `pin_not_driven`.
- `kicad-cli` can **only** run ERC/DRC and export — it **cannot edit** schematics.
- `kipy` (kicad-python IPC API) *can* edit, with KiCad's native connectivity, but it is
  PCB-centric, needs a running KiCad with IPC enabled, and has limited schematic support.
  For headless generation, hand-rolled S-expression writing + ERC verification is the
  pragmatic path — but **only trust full-file ERC**, never an isolated strip/append harness
  (round-tripping a partial schematic corrupts connectivity and gives false results).

---

## 10. Generate-from-ground-truth pipeline (current design)

`regenerate_schematic` (`kicad_skill/regenerate.py`):

1. Load ground-truth netlist (nets + a `components` block: `ref → {lib_id, value, x, y}`).
2. Compute label-padded bounding boxes (§7); place symbols (grid-snapped, overlap-resolved).
3. Classify nets (§4): IC↔IC → label; touching passive/connector/power → wire; power rails
   always labels. Optimistic — the ERC gate corrects mistakes.
4. Emit outward-oriented labels (§3) and short wires.
5. **Gate on `run_erc`** (backed by ground-truth comparison for logical correctness). Any
   ERC error (short / open / `wire_dangling` / `label_dangling`) demotes that pass's wires
   to labels and rebuilds. The all-labels configuration is electrically clean, so the loop
   converges. Falls back to ground-truth comparison when `kicad-cli` is absent.

Result on the MCP2515 CAN-node demo: flat schematic, **KiCad ERC 0 errors**.
Demo: `scratch/run_mcp_verification.py`.
