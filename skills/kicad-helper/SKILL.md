---
name: kicad-helper
description: Use when you need to programmatically build, layout, or route a KiCad schematic (.kicad_sch) or symbol library (.kicad_sym), resolve symbol overlaps, simplify wire runs, create sub-sheets (modules), or simulate schematic subcircuits with SPICE. Triggers on requests to place symbols, auto-route nets, generate symbols, create hierarchical sub-sheets, or run schematic evaluations.
---

# KiCad Helper Skill

This skill provides a Python package and CLI helper tool to automate symbol generation, collision-free placement, orthogonal wire routing, hierarchical module extraction, wire complexity reduction, and SPICE simulation for KiCad v6+ schematics (`.kicad_sch`) and symbol libraries (`.kicad_sym`).

## Location & Execution

The `kicad-helper` CLI wrapper is located at:
`/Users/ktchou/kicad-helper/kicad-helper`

You can run CLI commands directly using:
`/Users/ktchou/kicad-helper/kicad-helper <command> [args]`

## Subcommands Reference

### 1. Create a Symbol (`create-symbol`)
Generates a custom KiCad symbol, calculates appropriate dimensions based on pin count and length to avoid label overlap, and saves it to a `.kicad_sym` library.
```bash
/Users/ktchou/kicad-helper/kicad-helper create-symbol \
  --name "MY_CHIP" \
  --library "path/to/my_lib.kicad_sym" \
  --pins "left:1:VCC:power_in,right:2:GND:power_in" \
  --width 25.4 \
  --height 20.32
```
* **Arguments:**
  - `--name`: Name of the symbol.
  - `--library`: Absolute path to the output `.kicad_sym` file.
  - `--pins`: Pin shorthand string (`side:number:name:type`, comma-separated). Valid sides: `left`, `right`, `top`, `bottom`. Valid types: `input`, `output`, `bidirectional`, `tri_state`, `passive`, `free`, `unspecified`, `power_in`, `power_out`, `open_collector`, `open_emitter`.
  - `--pins-json`: JSON list of pins (alternative to `--pins`).
  - `--ref-prefix`: Reference designator prefix (default: `U`).
  - `--width` / `--height`: Symbol body dimensions in mm.
  - `--pin-length`: Length of pins in mm (default: `2.54`).

### 2. Place Symbols and Resolve Overlaps (`place`)
Places symbol instances in a schematic, resolves physical bounding-box overlaps, and snaps placement coordinates to the grid.
```bash
/Users/ktchou/kicad-helper/kicad-helper place \
  --schematic "path/to/schematic.kicad_sch" \
  --placements '[{"lib_id": "lib:MY_CHIP", "reference": "U101", "x": 100.0, "y": 100.0, "angle": 0.0}]' \
  --margin 5.08
```
* **Arguments:**
  - `--schematic`: Path to the `.kicad_sch` file.
  - `--placements`: JSON list (or path to a JSON file) of symbol placements. Each placement requires `lib_id`, `reference`, `x`, `y`, and `angle`.
  - `--table`: Path to the `sym-lib-table` file. Defaults to same directory as schematic.
  - `--margin`: Bounding box padding margin in mm for overlap detection (default: `2.54`).
  - `--no-resolve`: Disables overlap collision resolution.

### 3. Connect Pins with Orthogonal Wires (`connect`)
Routes orthogonal wires (using A* search algorithm) on a strict 1.27 mm grid. It automatically bypasses component bodies and prevents collinear wire overlaps.
```bash
/Users/ktchou/kicad-helper/kicad-helper connect \
  --schematic "path/to/schematic.kicad_sch" \
  --connections "U101:VCC to U102:GND"
```
* **Arguments:**
  - `--schematic`: Path to the `.kicad_sch` file.
  - `--connections`: Connection shorthand (e.g. `Ref1:Pin1 to Ref2:Pin2`, comma-separated). **Note:** this shorthand accepts pin NAMES, but ground-truth and evaluation work must use pin NUMBERS (e.g. `U1:14`, not `U1:VCC`) — pin names can collide across components. See the `plan-ground-truth-netlist` skill for evaluation-grade netlist work.
  - `--connections-json`: Path to JSON file/string of connections.
  - `--table`: Path to `sym-lib-table`.
  - `--diagonal`: Routes straight diagonal lines instead of L-shaped orthogonal lines (not recommended).

### 4. Create Hierarchical Module (`create-module`)
Groups a set of components into a hierarchical sub-sheet. Intra-module connectivity is rebuilt using local labels, while boundary nets crossing sheet borders collapse to hierarchical sheet pins.
```bash
/Users/ktchou/kicad-helper/kicad-helper create-module \
  --schematic "path/to/schematic.kicad_sch" \
  --components "U101,U102,R101" \
  --name "MyModule" \
  --sheet-file "mymodule.kicad_sch"
```
* **Arguments:**
  - `--schematic`: Path to the parent `.kicad_sch` file.
  - `--components`: Comma-separated list of component references to move.
  - `--name`: Name of the sub-sheet block.
  - `--sheet-file`: Sub-sheet schematic filename.

### 5. Simplify Wires (`simplify-wires`)
Converts high-complexity wires (crossings/bends/length) to clean local net labels to reduce schematic clutter.
```bash
/Users/ktchou/kicad-helper/kicad-helper simplify-wires \
  --schematic "path/to/schematic.kicad_sch" \
  --threshold 50.0
```

### 6. Run SPICE Simulation (`simulate`)
Runs ngspice, LTspice, or Xyce simulation on detected schematic subcircuits (voltage dividers, RC/LC filters, op-amp circuits, decoupling networks).
```bash
/Users/ktchou/kicad-helper/kicad-helper simulate \
  --schematic "path/to/schematic.kicad_sch" \
  --output "report.json"
```

### 7. Fetch & Import an EasyEDA/LCSC Component (`fetch-easyeda`)
Fetches a component (symbol, footprint, and 3D model) from EasyEDA/LCSC via the `easyeda2kicad` CLI, then imports it into the local library and registers it in KiCad's sym-lib-table/fp-lib-table — the same registration flow used by `import-lib`.
```bash
/Users/ktchou/kicad-helper/kicad-helper fetch-easyeda C2040 \
  --lib-root ~/hardwares/Libraries
```
* **Arguments:**
  - `lcsc_id` (positional): LCSC part number, e.g. `C2040`.
  - `--name`: Component/library name override (default: the LCSC id).
  - `--lib-root`: Root directory for installed libraries (default: `~/hardwares/Libraries`).
  - `--project`: Path to `.kicad_pro` for project-level table registration (default: global).
  - `--force`: Overwrite if the component already exists in `lib-root`.
  - `--fix-namespace`: Auto-prepend the registered library name to bare (unnamespaced) Footprint properties.

## Critical Schematic Routing & Placement Rules

When performing routing or placement, you **MUST** follow these rules to avoid breaking connectivity, causing ERC errors, or failing validation:

1. **Strict 1.27 mm (50 mil) Snapping**:
   All symbols, pins, wires, junctions, and labels must snap to integer multiples of `1.27` to align correctly in KiCad.
2. **String Quoting in S-Expressions**:
   Ensure string parameters under `path`, `page`, `pin`, and `lib_id` are explicitly double-quoted when editing or formatting to S-expressions to avoid parsing errors.
3. **Route Power & Ground First**:
   Always place power (`VDD/VCC`) and ground (`GND/VSS`) connections at the beginning of the connections routing list. This allows the A* router to route them on parallel non-overlapping grid lines first.
4. **Do Not Recursively Delete Connected Wires**:
   When routing, do not perform recursive deletion (BFS) of wires connected to terminals of the current connection. Doing so destroys multi-point shared nets like `VDD/VCC` and `GND/VSS` when routing bypass capacitors or daisy-chained pins.
5. **Bypass Capacitor Placement**:
   Place bypass capacitors as close as possible to the power/ground pins they filter to avoid long routing paths crossing other signals.
6. **Y-Axis Inversion**:
   - Symbols in library files (`.kicad_sym`) use **Y-Up** (positive Y is upwards).
   - Schematic files (`.kicad_sch`) use **Y-Down** (positive Y is downwards).
   - Relative pin offsets: `absolute_pin_y = symbol_y - pin_y`.
