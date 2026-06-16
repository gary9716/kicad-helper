# Wire Complexity & Label Simplification — Design

Date: 2026-06-16
Status: Approved (pending spec review)

## Problem

Auto-routed schematics accumulate visually noisy wires: long runs, many bends, and
wires that cross many *other* nets. Past a point, drawing every connection as a physical
wire hurts readability more than it helps. We want to **measure** per-connection routing
complexity and, when the schematic's total complexity is too high, **convert the worst
offenders from drawn wires into local-label connections** — removing the wire path and
tagging both endpoints with a matching net label.

This must never change the netlist: a conversion that would short two nets or disconnect
a pin is rejected.

## Scope

In scope:
- A complexity score per pin-to-pin connection (crossings, bends, length).
- A total-complexity metric for the schematic.
- An iterative simplifier that converts highest-complexity connections to local labels
  until total complexity drops below a threshold, guarded by a net-preservation gate.
- CLI subcommand `simplify-wires`.
- A read-only `wire_complexity_total` metric surfaced by `evaluate_layout`.

Out of scope:
- Connectivity-aware *placement* (separate future work; see `docs/LAYOUT_PIPELINE.md`).
- Global labels / hierarchical labels (local label only).
- Re-routing wires (we delete + label, we do not re-route).

## Module layout

New file: `kicad_skill/wire_complexity.py`. Self-contained; reuses primitives:
- `module.get_symbol_pins_global`, `module.grid_key`
- `schematic.load_sym_lib_table`, `find_symbol_definition`, `make_wire_sexpr`
- `parser.parse_sexpr`, `format_sexpr`

`evaluate_layout.py` gains one read-only call to `score_wire_complexity` for a metric;
it does not mutate. (Keeps the evaluator pure.)

## Definitions

### Net model
Union-find over explicit connections only: each wire segment unions its two grid
endpoints; a pin at a grid coordinate joins whatever wire endpoint sits there. Collinear
overlap and pass-through are NOT unioned (consistent with `evaluate_layout`), so accidental
merges remain visible rather than hidden. Result: `net_root(grid_key) -> root`.

### Connection (the unit of conversion)
Per the user's decision, the unit is a **pin-to-pin connection**: the wire path between two
terminal pins on the same net.

Reconstruction: build an adjacency graph of wire segments (nodes = grid keys, edges =
segments). For each net, take its terminal pins (grid keys that host a symbol pin). A
"connection" is the simple wire path between a pair of terminal pins that contains no third
terminal pin in its interior. For a 2-pin net this is the entire wire; for a multi-pin net
it is each leaf-to-branch or pin-to-pin segment chain.

A connection record holds: `pin_a`, `pin_b` (ref/pin/coord), ordered `segments`, and the
set of wire-node ids that form it.

### Complexity score
For a connection:
- `crossings` — count of points where any of its segments geometrically crosses a segment
  belonging to a **different** net (perpendicular or T crossing; not a shared endpoint; no
  junction present). This is the primary clutter signal.
- `bends` — number of direction changes along the ordered path.
- `length` — total Manhattan length in grid units (1.27 mm = 1 unit).

```
score = w_c * crossings + w_b * bends + w_l * length
```
Default weights: `w_c = 10.0`, `w_b = 2.0`, `w_l = 0.5` (crossings dominate; all tunable).

### Total complexity
`total = sum(score for each connection)`. The simplifier triggers while `total > threshold`.
Default `threshold = 50.0` (absolute, tunable via CLI/param).

## Conversion: connection → local labels

To convert the chosen connection:
1. **Net name** — first available of:
   1. an existing `label` already on this net (reuse its text),
   2. pin-derived `f"{ref}_{pin_name or pin_number}"` sanitized (`/`,space → `_`),
      matching `module.py` naming,
   3. `f"NET_{n}"` counter fallback.
2. **Remove** the connection's wire segments from the schematic.
3. At each terminal pin, add a short stub wire (one grid step away from the body) ending in
   a `label` with the net name, oriented away from the body. Reuse the pin-orientation →
   label-placement logic from `module.py`'s hierarchical-label code, emitting a local
   `label` instead.

### Net-preservation gate (safety)
Before converting, snapshot the **pin-partition**: the equivalence classes of all pins
under `net_root` (which pins are mutually connected — via wires AND matching label names).
Apply the conversion, recompute the partition (labels of equal text now union their hosts),
then accept only if:
- the pin-partition is **identical** to the snapshot (no pin pair gained/lost connectivity),
  AND
- `evaluate_layout` reports no new shorts and no new dangling wires.

Otherwise **roll back** (restore the pre-conversion AST) and try the next-highest
connection. Multi-pin power nets (VDD/GND) whose pin-to-pin removal would disconnect a pin
fail the gate and are naturally left alone.

### Iteration
```
while total > threshold and conversions_remaining:
    cand = highest-score connection not yet tried this round
    try convert(cand) with gate
    if accepted: recompute scores+total; reset tried set
    else: mark cand tried
    stop if no untried candidate, or max_conversions reached
```

## Public API

```python
score_wire_complexity(sch_path, table_path, weights=None) -> {
    "total": float,
    "connections": [
        {"pin_a": "U1:CS", "pin_b": "U2:CS", "score": float,
         "crossings": int, "bends": int, "length": float}
        # sorted by score desc
    ],
}

simplify_wires(sch_path, table_path, threshold=50.0, weights=None,
               max_conversions=None, dry_run=False) -> {
    "total_before": float,
    "total_after": float,
    "converted": [ {"net_name": str, "pin_a": str, "pin_b": str, "score": float} ],
    "skipped_unsafe": [ {"pin_a": str, "pin_b": str, "reason": str} ],
}
```
`dry_run=True` scores and reports the plan without writing the file.

## CLI

New subcommand in `main.py`:
```
kicad-helper simplify-wires --schematic PATH [--table PATH]
    [--threshold 50] [--max N] [--wc 10] [--wb 2] [--wl 0.5] [--dry-run]
```
`--table` defaults to `sym-lib-table` beside the schematic (matches other subcommands).
Prints total before/after and each converted/skipped connection.

## evaluate_layout integration
Add `wire_complexity_total` to the result dict via `score_wire_complexity`. Informational
only — **not** FATAL, does not affect the pass/fail score band. Printed in the report.

## Error handling
- Missing schematic / unparseable → raise `ValueError` (consistent with siblings).
- Symbol defs unresolved → that instance contributes no pins (same as existing code).
- No connections / already below threshold → no-op, `total_after == total_before`.
- A connection with `crossings == 0 and bends <= 1 and length` small → low score, never
  selected. No special-casing needed.

## Testing

`tests/test_wire_complexity.py`:
1. **Score monotonicity** — synthetic connections: more bends, more length, more crossings
   each strictly raise the score (one factor varied at a time).
2. **Crossing detection** — two different-net wires that cross count 1 crossing; same-net or
   shared-endpoint do not.
3. **Conversion preserves nets** — after `simplify_wires`, the pin-partition equals the
   pre-run partition (gate invariant); `evaluate_layout` shorts == 0, dangling == 0.
4. **Total decreases** — on a deliberately noisy schematic, `total_after < total_before`.
5. **Rollback on unsafe** — a multi-pin net whose pin-to-pin conversion would disconnect a
   pin is reported in `skipped_unsafe` and its wires remain.
6. **dry_run** — file unchanged; plan returned.

## Open defaults (chosen)
- Conversion unit: pin-to-pin connection.
- Label type: local `label`.
- Threshold: absolute, default 50, tunable.
- Naming: existing label → pin-derived → `NET_n`.
- Weights: `w_c=10, w_b=2, w_l=0.5`, tunable.
- Trigger: iterate until `total <= threshold`.
