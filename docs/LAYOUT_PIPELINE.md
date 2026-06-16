# Layout & Routing Logic — Trace

Trace of how `kicad-helper` turns components into a placed, wired schematic. Line
references are `kicad_skill/<file>.py:<line>` at the time of writing.

Goal of this doc: lay the logic out against the intended three-phase mental model —

1. **Logical** — connect components into correct nets.
2. **Visual (AABB)** — arrange bodies so nothing overlaps.
3. **Fine-tune** — clean up the remaining overlaps.

…and be explicit about where the current code matches that model and where it does not.

---

## TL;DR — current order vs the three-phase model

| Phase (intended) | What the code actually does | Where |
|---|---|---|
| 1. Logical connect | **Not a phase.** Connectivity exists only as the `connections` list the caller passes to step 2 below. It never informs placement. | — |
| 2. Visual / AABB | `place_symbols_and_resolve` — caller gives explicit `x,y`; grid-snap; AABB push-apart; text-field de-collision | `schematic.py:841` |
| 3. Fine-tune | folded into phase 2 (`adjust_symbol_properties_if_overlapping`) + the A* router avoiding obstacles at draw time | `schematic.py:651`, `235` |

**Real execution order is `place → resolve → connect` (geometry first, wires last).**
That is the reverse of "connect first", and it is *necessarily* so: a wire needs final
pin coordinates, so wire drawing can only happen after placement. See
[§ The ordering contradiction](#the-ordering-contradiction).

The genuine gap vs the intended model: **placement does not use connectivity at all.**
Coordinates are 100% caller-supplied; the netlist never pulls connected parts together.

---

## Phase A — Placement & overlap resolution

Entry: `place_symbols_and_resolve(schematic_path, table_path, new_placements, margin, resolve)`
— `schematic.py:841`.

1. **Dedup** — drop any existing instance whose reference is being re-placed (`:870`).
2. **Load symbol defs** — pull each `lib_id` from the local `.kicad_sym` or the global
   KiCad library, inject into the schematic's `lib_symbols` (`:896`, lookup `:82`).
3. **Create instances** — grid-snap the caller's `x,y` to 1.27 mm and build the symbol
   S-expression (`:913`, snap `:922`). Reference/Value field default positions come from
   the local bbox (`create_symbol_instance_sexpr` `:728`).
4. **Gather for overlap** — every root-level `symbol` instance becomes an overlap record.
   **Key rule: only newly-placed symbols are `movable`; existing layout is frozen**
   (`:993`). So re-running placement never disturbs prior work.
5. **Resolve** (`resolve` flag) → `resolve_overlaps(...)` then write positions + run the
   text de-collision pass (`:996`).

### AABB overlap resolution — `resolve_overlaps` `schematic.py:421`

Iterative force-style separation (`max_iterations=150`, `tolerance=0.01`):

- Each symbol's body bbox = local bbox **padded by `margin`** then transformed to global
  via `get_instance_aabb` (rotation/mirror aware) — `:432`, `:205`.
- For every pair, test AABB intersection (`:457`). On overlap, push along the axis of
  **smaller penetration** (`ox<oy ? x : y`) — minimal-displacement separation (`:466`).
- Movable/movable split the push 50/50; movable/fixed moves only the movable one; the
  per-symbol push is **averaged over its overlap count** to damp oscillation (`:478`,
  `:502`).
- Stop when no overlaps remain or max displacement < tolerance (`:494`, `:514`).
- **Grid-snap once at the very end** so intermediate pushes stay continuous but the final
  rest position is on-grid (`:517`).

### Text-field fine-tune — `adjust_symbol_properties_if_overlapping` `schematic.py:651`

Runs per symbol after bodies settle. Reference/Value labels must not sit on pins:

- Build pin "keep-out" boxes sized by pin length + name length and orientation
  (`compute_pin_collision_boxes` `:562`).
- Search candidate label positions: Reference above / Value below the body, trying
  perpendicular offsets `{0, 2.54, 5.08}` × lateral shifts out to ±15.24 mm, and take the
  first zero-overlap spot (else the least-overlapping) — `find_collision_free_position`
  `:603`. Rotation 90/270 swaps the search axis (`:620`).

This **is** the "fine-tune overlaps" phase of the model — but only for *text*, and it runs
inside Phase A, not as a separate final stage.

---

## Phase B — Wiring (the "connect" step)

Entry: `connect_symbols_in_schematic(schematic_path, table_path, connections, orthogonal)`
— `schematic.py:1103`. Runs **after** placement; needs final pin coordinates.

Per connection `{from: "Ref:Pin", to: "Ref:Pin"}`:

1. Resolve both pins' **global** coordinates via the instance transform
   (`transform_pin_coordinate` `:1055`, `:1242`).
2. **Existing wires are never deleted** — preserves multi-point nets (`:1245`).
3. Build routing constraints:
   - `blocked_pin_grids` — every pin except the two endpoints (`:1252`). Routing through a
     pin = short, so this is kept in *all* tiers.
   - `blocked_wire_directions` — **net-aware**: union-find over existing wires; skip wires
     already on the net being connected (`current_net_roots`), block the rest from
     collinear overlap. Lets a multi-pin net run along its own corridor; forbids overlapping
     a *different* net (`:1265`). (This is the fix for the VDD/GND rail-short bug.)
4. **Route** `find_orthogonal_path` (`:235`) — grid A* with a turn penalty, obstacle bboxes,
   pin/wire blocks, optional forced start direction (away from the pin's body) and
   `required_end_dir`.
   - Fallback ladder: full A* → relax only `start_dir` (pin + net-aware wire blocks **kept**)
     → blind L-shape last resort (`:1336`).
5. Emit one `wire` S-expr per path segment; append so later connections see them (`:1342`).

### A* router internals — `find_orthogonal_path` `schematic.py:235`

- Grid = 1.27 mm; search bounds = obstacle/endpoint extent ±20 cells (`:261`).
- `is_blocked` (`:267`): exempts start/end; rejects `blocked_pins`; rejects
  `blocked_wires[(gx,gy)]` only for the matching direction (so **perpendicular crossings are
  allowed, collinear overlaps are not**); rejects obstacle bboxes padded by 1.27 mm.
- State = `(cell, incoming_dir)`; no 180° reversals; turns cost extra → prefers straight
  runs, fewer bends.

---

## Phase C — Module extraction (hierarchical sheets)

`create_module_from_components` — `module.py` — is a higher-level operation that *reuses*
Phases A/B's primitives. Order inside it:

1. **Logical nets first** — union-find over pins + wires + labels to build the net graph,
   then classify boundary-crossing nets (inside↔outside) — `module.py` net build & classify.
2. Move inside components/wires into a sub-sheet; synthesize hierarchical labels + sheet
   pins for boundary nets.
3. **Re-route** the parent's outside nets to the new sheet pins with the same A* (now passing
   `blocked_pins` + net-aware `blocked_wires`).
4. **Prune** dangling wire stubs left by the cut (`prune_dangling_wires`).

Notably this is the one place that **does** build a logical net model before doing geometry —
closest thing in the codebase to the intended "logical first" ordering.

---

## The ordering contradiction

The requested order is "connect logically → AABB → fine-tune overlaps", with connect
**first**. Taken literally that is impossible: a drawn wire is a pair of coordinates, so it
can only exist once components have final positions. Hence the real pipeline must end with
wire drawing.

"Connect logically first" only makes sense if **logical** ≠ **drawn**:

- **Logical net model** (data: which pins share a net) — *can* come first.
- **Wire drawing** (geometry) — must come last.

So the intent-faithful pipeline is:

```
1. Build logical net model            (data only — no geometry)
2. Placement:
     a. connectivity-aware initial placement   ← MISSING today
        (pull net-connected parts together)
     b. AABB body de-overlap                    ← resolve_overlaps (exists)
3. Wiring:
     a. A* draw wires to final pins             ← connect (exists)
     b. fine-tune wire / text / field overlaps  ← partial (text only)
     c. evaluate_layout gate (shorts/dangling)  ← exists
```

### Gap vs today

| Intended step | Status |
|---|---|
| 1. Logical net model | exists only inside `create_module` (`module.py`); not a shared phase |
| 2a. Connectivity-aware placement | **missing** — `x,y` is fully caller-supplied |
| 2b. AABB de-overlap | ✅ `resolve_overlaps` |
| 3a. Draw wires | ✅ `connect_symbols_in_schematic` |
| 3b. Fine-tune overlaps | partial — text fields only (`adjust_symbol_properties_if_overlapping`); no post-route wire-overlap pass |
| 3c. Evaluate gate | ✅ `evaluate_layout.evaluate_schematic_layout` (shorts/dangling = FATAL) |

If/when connectivity should drive placement, the missing piece is **step 2a**: extract the
net model (reuse `module.py`'s union-find), then seed initial positions from it (e.g.
force-directed by net adjacency) *before* `resolve_overlaps` runs.
