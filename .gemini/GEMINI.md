# Gemini AI Coding Guidelines for kicad-helper

When working on the `kicad-helper` project, always adhere to the following rules and design patterns to prevent regression of fixed issues:

## 1. Schematic Routing Guidelines
* **Do Not Recursively Delete Connected Wires**: In `connect_symbols_in_schematic`, do not perform recursive deletion (BFS) of wires connected to the terminals of the current connection. Doing so destroys multi-point shared nets like `VDD/VCC` and `GND/VSS` when routing bypass capacitors or daisy-chained pins.
* **Route Power and Ground First**: Always place power (`VDD/VCC`) and ground (`GND/VSS`) connections at the beginning of the `connections` routing list. This allows the A* router to route them on parallel non-overlapping grid lines before the signal/SPI wires block the channels.
* **Component Placement for Bypass Capacitors**: Place bypass capacitors close to the power/ground pins they filter (e.g., place `C3` for `U2` on the left side at `X = 115.0` instead of the right side `X = 145.0` to avoid long routing paths crossing other signals).

## 2. Grid Snapping & Quoting Rules
* **Strict 1.27 mm (50 mil) Snapping**: All placements (symbols, pins, wires, junctions, labels) must snap to multiples of `1.27`.
* **String Quoting in S-Expressions**: Ensure string parameters under `path`, `page`, `pin`, and `lib_id` are explicitly quoted when formatted to S-expressions to avoid KiCad parsing errors (`Expecting 'symbol'`).
