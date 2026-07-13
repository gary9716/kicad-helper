# ELK Auto-Layout Design (`elk-layout`)

## Overview

Replace the current schematic layout pipeline (`place` collision-resolution + `resolve` AABB legalizer + `connect` A* per-wire routing) with an ELK (Eclipse Layout Kernel) based engine. ELK's `layered` algorithm (Sugiyama) computes globally-optimized component placement AND orthogonal wire routes in one pass, with `FIXED_POS` port constraints guaranteeing wires leave each pin from its true position and side.

Pipeline:

```
.kicad_sch  →  (parse: symbols/bboxes/pins + flattened nets)
            →  (classify: power & high-fanout nets → labels; rest → edges)
            →  [ELK JSON]  →  node tools/elk_runner.js (elkjs)  →  [ELK JSON + coords]
            →  (grid snap + orthogonality repair)
            →  (write-back: move symbols, delete old wires, emit new wires + labels + junctions)
            →  .kicad_sch
```

Runtime: elkjs (JS transpile of ELK, same engine netlistsvg/d3-hwschematic use) via a ~20-line node script. No JVM. Node v22 already present.

## Command Interface

```
kicad-helper elk-layout --schematic X.kicad_sch [--table T] [--output Y.kicad_sch] [--fanout-threshold 4] [--dry-run]
```

| Arg | Default | Description |
|-----|---------|-------------|
| `--schematic` | required | Input `.kicad_sch` |
| `--table` | same dir as schematic | `sym-lib-table` path (needed for symbol bbox/pin extraction) |
| `--output` | overwrite input | Output path |
| `--fanout-threshold` | 4 | Nets with ≥ this many pins become labels, not routed wires |
| `--dry-run` | false | Print placement/routing plan, write nothing |

Plus: ELK-backed regeneration via the library API `regenerate_schematic(..., routing="elk")` (there is no `regenerate` CLI subcommand) — it builds components/nets from ground-truth JSON, then delegates placement+routing to the same `elk_layout` core instead of its current place/A* path. Existing `routing="auto"|"labels"|"wires"` modes untouched.

Old `place` / `connect` / `resolve` subcommands stay untouched (legacy) until ELK is proven on real projects; deprecation is a later, separate decision.

## Dependency: elkjs via node runner

**New files:** `tools/elk_runner.js`, `tools/package.json` (pins `elkjs`).

- Runner: reads ELK JSON graph on stdin, `new ELK().layout(graph)`, writes layouted JSON to stdout. Errors to stderr, non-zero exit.
- Setup: `npm install --prefix tools/` — one-time, needs network. Documented in SKILL.md. `tools/node_modules/` gitignored.
- Python side invokes `subprocess.run(["node", "tools/elk_runner.js"], input=json, capture_output=True, check=True)`. Missing node / npm-install-not-run errors propagate bare (FileNotFoundError / CalledProcessError with elkjs's stderr), same style as `fetch-easyeda` / `render-netlist`.

## Architecture

**New file:** `kicad_skill/elk_layout.py`
**Modified:** `kicad_skill/main.py` (subparser + dispatch), `kicad_skill/regenerate.py` (`--routing elk` delegation)

### Stage 1 — Parse (reuse, no new parsing code)

- Symbols, rotated bboxes (incl. property text), absolute pin positions: reuse `resolve_layout._extract_symbols` + `_prop_text_bboxes`.
- Flattened electrical nets (sets of `"Ref:Num"`): reuse `netlist_eval.extract_actual_netlist`.
- Pin metadata (orientation → ELK port `side`): from `module.get_symbol_pins_global` output (pin angle: 0→WEST-facing connection… derive side from pin orientation + symbol rotation).

Hierarchy: v1 lays out ONE sheet at a time (root sheet only; sub-sheets untouched). Flattened-net extraction still used for connectivity, but only root-sheet symbols move. Cross-sheet nets appearing on the root sheet keep their hierarchical/global labels.

### Stage 2 — Net classification (reuse `regenerate.classify_nets` rules)

- Power-named nets (`GND`, `VCC`, `+5V`, … — the existing `_is_power` list): → labels at every pin, never ELK edges. Kills the power-rail wire waterfall.
- Fanout ≥ `--fanout-threshold` (default 4): → labels at every pin. Matches `simplify-wires` philosophy; defers bus-trunk synthesis to v2.
- Remaining 2-3 pin nets: → ELK edges (1 source, 1-2 targets).

### Stage 3 — ELK JSON build

Mapping (mm units throughout — ELK is unitless):

| KiCad | ELK |
|---|---|
| Symbol instance `U1` | `children[]` node: `id: "U1"`, `width`/`height` from rotated bbox, `layoutOptions: {portConstraints: "FIXED_POS"}` |
| Pin | `ports[]`: `id: "U1:5"`, `x`/`y` relative to node origin (post-rotation), `layoutOptions: {"port.side": "WEST"\|"EAST"\|"NORTH"\|"SOUTH"}` |
| 2-3 pin net | `edges[]` — star-split into simple 2-pin edges from one hub pin (`sources: ["U1:5"]`, `targets: ["R1:1"]`), because ELK layered rejects multi-target hyperedges ("Passed edge is not 'simple'"). Duplicate wire segments from star edges sharing the hub run are deduped at write-back. |

Root `layoutOptions`:
- `algorithm: "layered"`
- `elk.direction: "RIGHT"`
- `edgeRouting: "ORTHOGONAL"`
- `spacing.nodeNode`, `spacing.edgeNode`, `layered.spacing.nodeNodeBetweenLayers`: mm-scale values, tuned during implementation against the can_node fixture (start ~5.08 / 2.54 / 10.16).

ELK never rotates nodes; each symbol keeps its existing `angle`, bbox and pin offsets computed post-rotation before graph build.

### Stage 4 — Grid snap + orthogonality repair (the hard part)

- ELK returns float node origins + edge sections (start/end + bendPoints).
- **Node snap:** snap each symbol's ANCHOR so its pins land on the 1.27 mm grid. Pin offsets within a KiCad symbol are already grid-aligned relative to the anchor, so snapping the anchor to 1.27 grid suffices; use 2.54 for the anchor if all pin offsets are 2.54-aligned (match existing `place` snap behavior).
- **Edge re-derivation:** wire endpoints = snapped absolute pin positions (authoritative — never trust ELK's float port coords after snapping). Bend points snapped to 1.27 grid. If snapping breaks a segment's orthogonality (dx≠0 and dy≠0), insert one L-jog at the snapped bend.
- **Junctions:** where ≥3 wire segments meet at a grid point, emit KiCad `junction`. Reuse/factor existing junction-emission from `schematic.py` if cleanly extractable, else small local helper.

### Stage 5 — Write-back

- Update each moved symbol's `(at x y angle)`.
- Delete ALL existing `wire`, `junction`, and local `label` elements on the sheet (full re-route; `--dry-run` for safety preview). Labels are then re-emitted via `regenerate._make_label` / `_label_orientation`: label-nets get a label at EVERY pin; edge-nets whose name came from a user label (non-synthesized, i.e. not `NET_*` invented by `name_nets`) get one label at their first (sorted) pin so the user's net name survives the re-route.
- Emit new wires, labels, junctions. S-expression editing follows existing `schematic.py` patterns.
- Output is written to a temp file in the same directory (`<out>.elk_tmp`) and the connectivity gate runs against it; only on gate pass is it renamed onto the output path. On gate failure the input is left untouched and the rejected layout is kept at `report["rejected_file"]` for inspection.

## Best-practice rules mapping (review of old rules)

| Old rule / heuristic | Source | ELK-era disposition |
|---|---|---|
| Minimize wire crossings (weight 10) | `simplify-wires` complexity | ELK `layered` minimizes crossings natively — retire manual weight |
| Minimize bends (weight 2) | `simplify-wires` | ELK orthogonal routing minimizes bends natively |
| Minimize length (weight 0.5) | `simplify-wires` | ELK layer assignment handles; spacing options tune it |
| High-complexity wires → labels | `simplify-wires` | Absorbed into Stage 2 fanout-threshold classification (pre-emptive, not post-hoc) |
| No symbol overlaps, margin 2.54 | `place`/`resolve` | ELK `spacing.nodeNode` guarantees by construction — retire MTV legalizer for ELK path |
| Power pins → labels not wires | `regenerate.classify_nets` | Reused directly (Stage 2) |
| Signal flow left→right | implicit in evaluate | `elk.direction: RIGHT` explicit |
| Bypass caps adjacent to power pins, never across signal paths | project CLAUDE.md | **v1 limitation** (bypass caps' power nets become labels, so caps float freely). v2: compound-node grouping pins cap next to its IC |
| Shorts/dangling = FATAL | `evaluate_layout` / memory | Unchanged — hard gate below |

## Verification gate (hard, in tests)

An ELK-laid schematic must pass ALL THREE:

1. **Connectivity:** `netlist_eval.check_netlist` — pre-layout extracted netlist (as ground truth) vs post-layout: zero shorts, zero opens.
2. **ERC:** `kicad-cli sch erc` exits clean (same invocation `regenerate` already uses).
3. **Quality:** `evaluate_layout.py` score on the ELK-laid `can_node` fixture ≥ score of the current pipeline's output for the same input.

## Testing

- **Unit (no node needed):** ELK JSON builder — fixture schematic → assert node/port/edge structure, FIXED_POS options, power/fanout nets excluded from edges, port sides correct for rotated symbols. Snap logic — synthetic float coords → grid-aligned, orthogonality preserved, jog insertion. Write-back — wires terminate exactly on pin coords.
- **Integration (needs node + `npm install --prefix tools/`):** full `elk-layout` on `can_node` fixture; asserts the 3-part verification gate. Skipped (`unittest.skipUnless`) when `node` or `tools/node_modules/elkjs` absent.

## Scope limits (v1, documented)

- Single sheet per run; no hierarchical multi-sheet layout.
- No symmetry/grouping constraints (differential pairs, cap arrays) — v2 via ELK compound nodes.
- No bypass-cap proximity enforcement — v2.
- No bus trunk synthesis for high-fanout nets (labels instead) — v2.
- ELK never rotates symbols; input orientations preserved.
- Needs `node` + one-time `npm install --prefix tools/` (network).

## Skill Doc Update

New numbered SKILL.md entry for `elk-layout` (command, args, setup step, scope limits) + note under `regenerate` for `--routing elk`.
