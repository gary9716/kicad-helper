# Design: kicad-design-workflow skill

Date: 2026-06-26
Status: Approved (design phase)

## Problem

Turn the "AI-collaborative KiCad schematic design SOP" into something the agent
follows reliably. The SOP splits work by strength: AI handles datasheet reading,
math, and a text wiring bridge; the human handles spatial layout and drawing.
Today these capabilities are scattered across separate skills (`datasheets`,
`kicad`, `spice`) with no orchestrating workflow that enforces the role split or
produces the two bridge documents.

## Goal

A single skill, `kicad-design-workflow`, that defines the 3-stage SOP as an
orchestration document. It produces `docs/checklist.md` and
`docs/wiring_guide.md`, delegates extraction/review to existing skills, and never
auto-draws the schematic.

## Non-goals

- No new Python. No PDF parser, no calc engine, no CLI subcommands.
- No visual schematic generation by the AI (explicit prohibition in the skill).
- Not replacing `datasheets`, `kicad`, or `spice` skills — orchestrating them.

## Deliverable

One file: `skills/kicad-design-workflow/SKILL.md` (sibling of
`skills/kicad-helper/`). No code. Installed/discovered the same way the existing
`kicad-helper` skill is.

## Core principle

AI = logic + memory + math + text bridge. Human = geometry + drawing + visual
review. The skill encodes this split and forbids the AI from generating the
visual schematic.

## Triggers

`description` frontmatter fires on: "design a (new) hardware module", "start a
module design", "extract a design checklist from this datasheet", "generate a
wiring guide", "review my schematic against the checklist", "DRC review against
checklist".

## Two invocation modes

### Mode A — New module design (Stage 1 → Stage 2)

**Stage 1 — Architecture & checklist  [AI]**
- Input: datasheet PDF(s) under `docs/datasheets/` named by the user.
- Delegate spec extraction to the existing `datasheets` skill. Do not parse PDFs
  directly in this skill.
- Output: `docs/checklist.md`, one section per IC, covering layout constraints,
  decoupling requirements, pin traps (e.g. EN thresholds, bootstrap caps),
  voltage-level compatibility between connected ICs, and max current draw for the
  power-budget sanity check.

**Stage 2 — Calc + wiring bridge  [AI]**
- Input: target rails (e.g. "12V → 5V/3A") + the Stage 1 checklist.
- Passive calculation: derive inductor value, feedback divider R1/R2, etc. from
  the datasheet formulas. For each: show the formula, the computed value, the
  nearest E-series standard value, and a suggested LCSC part / footprint.
- Output: `docs/wiring_guide.md` — **net-label centric** table:

  | Net Name | Members (Ref:Pin …) | Route | Notes |
  | --- | --- | --- | --- |
  | `VIN_12V` | U1:VIN, C1:1, C2:1 | label | power, route first |
  | `FB_3V3` | U1:FB, R1:2, R2:1 | label | divider midpoint |

  - One row per net. `Members` lists every Ref:Pin on the net so the user drops
    matching net labels in KiCad rather than drawing long wires.
  - `Route` column hint: `label` for power/bus/multi-pin nets, may suggest `wire`
    for tight local nets (e.g. a 2-pin feedback tap) — but default is label.
  - Power and GND nets flagged "route first" to match kicad-helper routing rule 3.
- Hard rule stated in the skill: **DO NOT generate a visual schematic** (no
  `.kicad_sch` geometry, no placement, no auto-routing). The human draws.

### Mode B — Review (Stage 3)

**Stage 3 — Review  [AI-assisted, human decides]**
- Input: exported KiCad netlist (`.net`) OR a schematic screenshot (multimodal).
- Delegate deep design-rule analysis to the existing `kicad` skill.
- This skill's specific job: cross-reference the netlist against
  `docs/checklist.md` and report:
  - missing/insufficient decoupling capacitors vs checklist,
  - wrong or missing pull-up/pull-down resistors,
  - unconnected nets, or pins tied to the wrong power rail.
- Output: a pass/fail report keyed to checklist items (not a file unless asked).

## Relationship to existing skills

| Need | Owner |
| --- | --- |
| PDF datasheet spec extraction | `datasheets` skill (delegate) |
| Deep schematic/netlist/DRC analysis | `kicad` skill (delegate) |
| Subcircuit SPICE validation | `spice` skill (optional, Stage 2/3) |
| Symbol/place/route/module geometry | `kicad-helper` CLI (human-driven only) |
| SOP orchestration + the two bridge docs | **this skill** |

## File outputs

- `docs/checklist.md` — created/updated in Stage 1.
- `docs/wiring_guide.md` — created/updated in Stage 2.
- Both are Markdown, human-editable, version-controlled. The skill updates rather
  than blindly overwriting when a file already exists.

## Success criteria

1. Asking "design a 12V→5V/3A module from docs/datasheets/X.pdf" produces a
   `checklist.md` and a net-label-centric `wiring_guide.md`, and the agent does
   NOT create or modify any `.kicad_sch`.
2. Asking "review my netlist against the checklist" produces a checklist-keyed
   pass/fail report citing specific nets.
3. The skill adds zero Python and introduces no new dependency.

## Deferred (YAGNI)

- A `calc-passives` CLI helper with an assert-based self-check — add only if
  hand math turns out non-reproducible across runs.
- Auto-export of the KiCad netlist before review — add if the manual export step
  becomes friction.
