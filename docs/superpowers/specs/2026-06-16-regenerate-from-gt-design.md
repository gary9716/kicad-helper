# Regenerate-from-Ground-Truth — Design Spec

**Date:** 2026-06-16
**Status:** Approved (user, 2026-06-16)
**Sub-project:** 3 of 3 (routing-correctness initiative). Depends on Sub-project 2
(`kicad_skill/netlist_eval.py`, committed `99985d5`).

## Problem

The existing pipeline (`place_symbols_and_resolve` + `connect_symbols_in_schematic`
A\* point-to-point router + `create_module_from_components`) produces a schematic
whose **actual electrical connectivity is wrong**: on the MCP2515 CAN-node demo the
A\* router emits collinear approach wires that stack on a shared trunk and merge
otherwise-distinct nets. `netlist_eval` confirms it: VDD↔GND short (plus SPI nets
swept into the same trunk), CANH↔CANL short. The geometry-only evaluator scores it
100/100 and cannot see the merge.

We need a generator that takes a **user-confirmed ground-truth netlist** and emits a
**clean flat schematic** that `netlist_eval` certifies as **0 short / 0 open**.

## Goal / Success Criteria

`regenerate_schematic(gt_path, table_path, out_sch)` produces `out_sch` such that:

- `netlist_eval.compare(extract_actual_netlist(out_sch, table), gt.nets)` reports
  **0 shorts, 0 opens** (fatal = False).
- The schematic is **flat** — no `sheet` nodes.
- Every GT net's pins are electrically joined; no two GT nets share an electrical net.
- Deterministic: same inputs → same output.

## Inputs

Extend the existing `groundtruth.json` with a `components` block (chosen over a
separate manifest: one file, parts-list lives beside the connectivity it wires):

```json
{
  "nets": [ ... ],                          // unchanged, user-confirmed per-net
  "components": {
    "U2": {"lib_id": "mcp_test:MCP2515", "value": "MCP2515",
           "x": 124.46, "y": 100.33, "angle": 0},
    "R2": {"lib_id": "Device:R", "value": "120"}
  }
}
```

- `lib_id`, `value` — **required** per ref.
- `x`, `y`, `angle` — **optional**. Absent → deterministic auto-row layout
  (components laid in a single row, fixed pitch, sorted by reference).
- Pin coordinates are resolved **by pin number** from the symbol library via the
  existing `find_symbol_definition` + `get_symbol_pins_global` loaders.
- **Assumption:** the project's `.kicad_sym` and `sym-lib-table` already exist and
  define every referenced `lib_id`. Symbols are *parts* and are reused as-is; this
  generator does not create or modify symbols. (Symbol authoring stays in the build
  harness / `symbol.py`.)
- Every ref appearing in any GT net pin MUST have a `components` entry, else raise
  (loud — `KeyError`-style with the missing ref named). Every GT pin number MUST
  resolve to a pin on its symbol, else raise.

## Algorithm

`regenerate_schematic(gt_path, table_path, out_sch)`:

1. **Load** GT (nets + components). Validate every net-ref has a component entry.
2. **Place** all components into a blank schematic via `place_symbols_and_resolve`
   (explicit x/y from components block, or auto-row coords).
3. **Classify** each net into `label_nets` vs `wire_nets`:
   - **label-route** when the net is power/global — name matches a power set
     (`VDD VCC VSS GND VBUS V+ V- 3V3 5V` + case-insensitive `GND*`/`V*` heuristics)
     **OR** has ≥3 pins **OR** its pins span more than one placement cluster.
   - **wire-route** only when the net has exactly 2 pins **and** both owning
     components are physically adjacent (center-to-center distance < threshold).
   - Everything not provably a safe local pair defaults to **label-route**.
4. **Generate connections:**
   - `label_nets`: for each pin, emit a short stub wire off the pin plus a **local
     label** whose text is the GT net name at the stub end. KiCad joins same-named
     local labels — no trunk wire, so no collinear stacking, so no accidental merge.
   - `wire_nets`: emit a short orthogonal A\* wire between the two adjacent pins
     (reuse `connect_symbols_in_schematic`, scoped to that pair).
5. **Verify:** run `netlist_eval` on the result.
6. **Fallback loop:** if `compare` reports any short or open, move every net implicated
   in a short (and any `wire_net` touching an open) from `wire_nets` to `label_nets`,
   regenerate from step 4, and re-verify. The all-labels configuration is structurally
   incapable of collinear merge, so the loop converges in ≤ (number of wire_nets + 1)
   iterations. If a fatal report survives the all-labels configuration, **raise** with
   the report attached (fail loud — indicates a GT/geometry contradiction, not a
   routing choice).

## Module / API

New file `kicad_skill/regenerate.py`:

- `regenerate_schematic(gt_path, table_path, out_sch) -> (out_path, report)`
- `classify_nets(gt, placements) -> (label_nets, wire_nets)` — pure, unit-testable.
- Internal helpers for stub+label emission and auto-row layout.

CLI: `regenerate-from-gt --ground-truth gt.json --table <tbl> --out clean.kicad_sch`.
Exit 0 when final report is clean, exit 1 (and print the report) when fatal.

## Testing (TDD)

- **Unit — classification:** power-named net → label; ≥3-pin net → label; 2-pin net
  with adjacent components → wire; 2-pin net with distant components → label.
- **Unit — label routing correctness:** a tiny 2-net design routed entirely by labels,
  fed back through `extract_actual_netlist` + `compare`, reports 0 short / 0 open.
- **Integration:** regenerate the MCP2515 demo from the extended GT; assert
  `netlist_eval` reports **0 short / 0 open** and the output contains **no `sheet`
  node** (flat). This is the schematic the old pipeline could not produce.
- **Regression:** full suite stays green.

## Out of Scope (YAGNI)

- Symbol creation/regeneration (reuse existing `.kicad_sym`).
- Hierarchy / sub-sheets (output is flat).
- Sophisticated auto-placement or routing aesthetics — `evaluate_layout.py` already
  scores look-and-feel and may be run as informational, but it does not gate this
  generator. Correctness (`netlist_eval`) is the only gate.
- Datasheet-PDF → auto-netlist extraction (separate later phase).
