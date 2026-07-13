# Render Netlist v2: Ground-Truth JSON Input Design

## Overview

Rework `render-netlist` (see `2026-07-13-render-netlist-svg-design.md` for v1) to take a **ground-truth netlist JSON** as input instead of a `.kicad_sch` schematic. This makes the command a pure JSON→SVG transform, drops the schematic-parsing/`sym-lib-table` dependency entirely, and — because GT nets carry names — the diagram now shows real net names (`VDD`, `SPI_SCK`) instead of synthetic `net1`/`net2`.

Breaking change, user-approved: `--schematic`/`--table` flags removed. Users with only a schematic extract a GT JSON first (see the `plan-ground-truth-netlist` skill).

## Command Interface

```
kicad-helper render-netlist --netlist X.json --output X.svg
```

| Arg | Default | Description |
|-----|---------|-------------|
| `--netlist` | required | Ground-truth netlist JSON path |
| `--output` | required | Destination `.svg` path |

## Input Format

The existing ground-truth netlist JSON used by `check-netlist` / `regenerate` / `plan-ground-truth-netlist` (example: `tests/fixtures/can_node/can_node.groundtruth.json`):

```json
{
  "nets": [
    {"name": "VDD", "pins": ["U1:5", "U2:18", "C3:1"]},
    {"name": "SPI_CS", "pins": ["U1:1", "U2:16"]}
  ],
  "components": { "U1": {"lib_id": "...", ...} }
}
```

Only `nets[].name` and `nets[].pins` are consumed. `components` and all other keys ignored — cells are derived from the pins appearing in nets (a component with no pins in any net has nothing to draw). Loaded via `netlist_eval.load_ground_truth` if it validates the same shape, else plain `json.load` + minimal shape check (nets list with name/pins).

## Changes

**Modified:** `kicad_skill/netlist_svg.py`
- `build_yosys_netlist(gt_nets)` — new signature: takes the parsed nets list (`[{"name", "pins"}]`). Cells: every `Ref:Num` across all nets → port on cell `Ref`. Netnames: one per net with ≥2 pins, **keyed by the GT net name** (collision-safe: duplicate names get `_2`, `_3` suffixes); single-pin nets contribute ports but no netname (dangling stub, unchanged from v1). `port_directions` stays `"input"` (netlistsvg schema).
- `render_netlist_svg(netlist_path, output_path)` — new signature: loads the JSON, builds, same `npx --yes netlistsvg` invocation, same temp-file + bare-error-propagation behavior.

**Modified:** `kicad_skill/main.py` — `render-netlist` subparser: `--schematic`/`--table` removed, `--netlist` added; dispatch updated.

**Modified:** `tests/test_netlist_svg.py` — rework against `tests/fixtures/can_node/can_node.groundtruth.json`: cell/port coverage, named netnames, single-pin skip, bit-id consistency, subprocess-mock test for the new signature.

**Modified:** `skills/kicad-helper/SKILL.md` §8 — new args; pointer: have a schematic? extract GT JSON via `plan-ground-truth-netlist` first.

## Not Changing

- npx/netlistsvg invocation, temp-file handling, error style.
- Generic-box visual style, flatten-to-one-diagram scope.

## Testing

Unit only (no npx in tests, as v1): JSON → Yosys-JSON structure assertions on the can_node GT fixture. Manual e2e: run CLI on the fixture GT JSON, confirm SVG contains all refs and the net names as labels.
