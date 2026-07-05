---
name: kicad-design-workflow
description: Use to run the AI-collaborative KiCad schematic design SOP. AI extracts datasheet design rules, calculates passive values, and produces a net-label-centric text wiring guide; the human draws the schematic. Also reviews an exported netlist or schematic screenshot against the design checklist. Triggers on "design a new hardware module", "extract a design checklist from this datasheet", "generate a wiring guide", "review my schematic/netlist against the checklist", or "DRC review against checklist". Does NOT auto-draw schematics.
---

# KiCad Design Workflow (AI-collaborative SOP)

Orchestrates a 3-stage hardware schematic design flow. The division of labor is
the whole point:

- **AI = logic + memory + math + text bridge.** Read datasheets, compute passive
  values, produce a structured wiring guide, cross-check netlists.
- **Human = geometry + drawing + visual review.** Place symbols, route wires,
  judge readability in KiCad.

This skill is orchestration only — it adds no code. It **delegates** PDF
extraction to the `datasheets` skill and deep design-rule analysis to the
`kicad` skill, and it drives the `kicad-helper` CLI only when the human asks.

> **HARD RULE — never auto-draw.** This skill must NOT create or modify a
> `.kicad_sch`, place symbols, or auto-route. It produces text
> (`docs/checklist.md`, `docs/wiring_guide.md`) and the human draws. Symbol
> placement / routing via `kicad-helper` happens only on the human's explicit
> request, never as part of this workflow.

## When to use which mode

- **Mode A — New module design** → run Stage 1 then Stage 2.
- **Mode B — Review** → run Stage 3.

Pick by intent. "Design / start a module", "extract checklist", "generate wiring
guide" → Mode A. "Review / DRC against checklist" → Mode B.

---

## Mode A — New module design

### Stage 1 — Architecture & checklist  [🤖 AI]

1. Confirm the product goal and target rails with the user if not stated
   (e.g. "12V → 5V/3A, ESP32 host"). State assumptions explicitly; ask if
   ambiguous.
2. Locate the datasheet PDF(s) the user named under `docs/datasheets/`.
3. **Delegate extraction to the `datasheets` skill if installed; otherwise extract design rules from the datasheet PDF directly.** Do not parse PDFs here.
4. Write **`docs/checklist.md`**, one section per IC, covering:
   - layout constraints (e.g. DCDC feedback trace away from the inductor, USB
     D+/D- differential routing),
   - required decoupling (count + value per power pin),
   - pin traps (EN thresholds, bootstrap caps, strapping/boot pins, NC pins that
     must float),
   - voltage-level compatibility between ICs that talk to each other (flag if a
     level shifter is needed),
   - max current draw per IC, for the power-budget sanity check.
5. If `docs/checklist.md` already exists, update the relevant sections rather
   than overwriting the whole file.

### Stage 2 — Calc + wiring bridge  [🤖 AI]

1. From the target rails + the Stage 1 checklist, calculate passives using the
   datasheet formulas. For **each** computed component show: the formula, the
   computed value, the nearest E-series standard value, and a suggested LCSC part
   / footprint. Typical items: inductor value, feedback divider R1/R2, soft-start
   cap, input/output bulk caps.
2. Sanity-check the result if a SPICE model is cheap — delegate to the `spice` skill if installed; otherwise skip simulation and state values are hand-computed. Optional.
3. Write **`docs/wiring_guide.md`** as a **net-label-centric** table:

   | Net Name | Members (Ref:Pin …) | Route | Notes |
   | --- | --- | --- | --- |
   | `VIN_12V` | U1:VIN, C1:1, C2:1 | label | power, route first |
   | `GND` | U1:GND, C1:2, C2:2, R2:2 | label | ground, route first |
   | `SW` | U1:SW, L1:1 | wire | keep short, away from FB |
   | `FB_3V3` | U1:FB, R1:2, R2:1 | label | divider midpoint |

   - **One row per net.** `Members` lists every Ref:Pin on the net so the user
     places matching **net labels** in KiCad instead of drawing long wires.
     Pin NAMES are fine for the human-facing wiring guide; anything feeding
     ground-truth/evaluation must convert to pin NUMBERS per the
     plan-ground-truth-netlist skill.
   - `Route` hint: default `label`. Use `wire` only for tight local 2–3 pin nets
     where a physical wire reads cleaner (e.g. SW node, a feedback tap).
   - Flag power and GND nets **"route first"** (matches kicad-helper routing
     rule 3 — power/ground get parallel non-overlapping grid lines first).
4. State in your summary: **"Wiring guide is text only — draw the schematic in
   KiCad yourself; left-in / right-out, power up / ground down, modules in
   blocks."** Do not generate `.kicad_sch` geometry.
5. If `docs/wiring_guide.md` exists, update affected nets rather than overwrite.

---

## Mode B — Review  [🧠 AI-assisted, human decides]

1. Ask the user to provide either an exported KiCad netlist (`.net`) or a
   schematic screenshot (use multimodal reading for the screenshot).
2. **Delegate deep design-rule / DRC analysis to the `kicad` skill if installed; otherwise run DRC/review checks via the kicad-helper CLI and manual netlist inspection.**
3. This skill's specific job: cross-reference the connectivity against
   `docs/checklist.md` and report, keyed to checklist items:
   - missing or insufficient decoupling capacitors vs the checklist,
   - wrong or missing pull-up / pull-down resistors,
   - unconnected nets, or pins tied to the wrong power rail,
   - level-compatibility violations flagged in Stage 1.
4. Output a **pass/fail report per checklist item**, citing specific nets. Write
   it to a file only if the user asks.

---

## Related skills & tools

| Need | Use |
| --- | --- |
| Extract specs from a datasheet PDF | `datasheets` skill (optional — fallback inline if absent) |
| Deep schematic / netlist / DRC / power-tree analysis | `kicad` skill (optional — fallback inline if absent) |
| SPICE-validate a subcircuit (divider, filter) | `spice` skill (optional — fallback inline if absent) |
| Actually build/place/route geometry (human-driven) | `kicad-helper` CLI at `/Users/ktchou/kicad-helper/kicad-helper` |

## Outputs recap

- `docs/checklist.md` — Stage 1, design rules per IC.
- `docs/wiring_guide.md` — Stage 2, net-label-centric connection table.
- Review report — Stage 3, pass/fail per checklist item (file optional).
