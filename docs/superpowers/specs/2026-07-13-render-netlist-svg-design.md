# Render Netlist to SVG Design

## Overview

New `render-netlist` subcommand for `kicad-helper`. Flattens a (possibly hierarchical) `.kicad_sch` schematic into its actual connectivity — reusing the parsing already built for `check-netlist` — converts it into the Yosys-style netlist JSON that [netlistsvg](https://github.com/nturley/netlistsvg) consumes, and shells out to netlistsvg (via `npx`) to render an SVG.

Each component reference becomes a generic labeled box with one port per pin (numbered); each electrical net becomes a wire between ports. This is a connectivity diagram, not a real schematic — no R/C/U symbol art. Good enough for "does this net actually touch what I think it touches" debugging and doc figures.

## Command Interface

```
kicad-helper render-netlist --schematic X.kicad_sch --output X.svg [--table sym-lib-table]
```

| Arg | Default | Description |
|-----|---------|-------------|
| `--schematic` | required | Path to the root `.kicad_sch` file |
| `--output` | required | Destination `.svg` path |
| `--table` | same dir as schematic | Path to `sym-lib-table` (only needed if pin type lookup requires it — see Architecture) |

## Dependency

`netlistsvg` is NOT added as a project dependency. It's invoked on demand via `npx --yes netlistsvg`, using the already-present Node v22 (nvm). First run fetches/caches the npm package (needs network); subsequent runs use the npx cache. No new `pyproject.toml` entry.

## Architecture

**New file:** `kicad_skill/netlist_svg.py`
**Modified:** `kicad_skill/main.py` — add `render-netlist` subparser + handler

### netlist_svg.py responsibilities

1. **`build_yosys_netlist(schematic_path, table_path=None) -> dict`**
   - Reuse `netlist_eval._parse_sheet` + the same recursive sheet-walk `extract_actual_netlist` already performs, to collect, per flattened scope: every component ref and its full pin list (`{number, name}`) from `get_symbol_pins_global` (regardless of connection state).
   - Reuse `netlist_eval.extract_actual_netlist(schematic_path, table_path)` for the flattened net groups (sets of `"Ref:Num"`).
   - Emit one Yosys-JSON module containing:
     - one `cell` per component ref, `type: "generic"`, one port per pin keyed by pin number, port direction unspecified (`"direction": "inout"` — netlistsvg's generic skin doesn't care).
     - one `netname` entry per net group with >=2 pins, `bits` assigned sequential integer net ids; ports reference those same ids.
     - net groups with exactly 1 pin are skipped (nothing to draw — matches how unconnected pins already render as dangling stubs on the cell box).
   - Refs that repeat across sheets (same ref reused in two sub-sheets) collide by design — flattening assumes globally-unique refs, same assumption `extract_actual_netlist` already makes for shorts/opens comparison.

2. **`render_netlist_svg(schematic_path, output_path, table_path=None) -> None`**
   - Call `build_yosys_netlist`, write to a `tempfile.NamedTemporaryFile(suffix=".json")`.
   - `subprocess.run(["npx", "--yes", "netlistsvg", tmp_json_path, "-o", output_path], check=True)`.
   - No error wrapping — non-zero exit / missing `npx` propagates as `CalledProcessError` / `FileNotFoundError`, same bare-propagation style as `fetch_easyeda_component`.
   - Temp JSON file cleaned up in a `finally`.

### main.py: handle_render_netlist

New subparser `render-netlist` with `--schematic`, `--output`, `--table` (optional). Handler calls `render_netlist_svg(args.schematic, args.output, args.table)`, prints output path on success.

## Yosys JSON shape (netlistsvg input)

```json
{
  "modules": {
    "top": {
      "ports": {},
      "cells": {
        "U101": {
          "type": "generic",
          "port_directions": {"1": "inout", "2": "inout"},
          "connections": {"1": [10], "2": [11]}
        }
      },
      "netnames": {
        "net0": {"bits": [10]},
        "net1": {"bits": [11]}
      }
    }
  }
}
```

Cell ports are keyed by pin number (string). `port_directions` values are constant `"inout"` — direction isn't tracked by `extract_actual_netlist`, and netlistsvg's default/generic skin doesn't visually distinguish them.

## Scope Limits (documented, not hidden)

- Hierarchy is flattened into a single diagram — no per-sheet SVGs.
- Assumes globally-unique component refs across the whole hierarchy.
- Visual style is generic labeled boxes, not real schematic symbols (no custom skin work).
- First invocation needs network access for `npx` to fetch `netlistsvg`.

## Testing

`tests/test_netlist_svg.py`: build the Yosys JSON from a small fixture schematic (reuse an existing fixture under `tests/` if one already covers multi-pin components + one net + one dangling pin), assert:
- one cell per component ref, with all its pins present as ports (including the dangling one).
- one netname per net with >=2 connected pins; single-pin groups produce no netname.
- connection ids on both ends of a net match.

Does not shell out to `npx`/netlistsvg in the test — that path is integration-level and checked manually after implementation (run the CLI against a real schematic, confirm the SVG opens and shows the expected boxes/wires).

## Skill Doc Update

Add a numbered entry to `skills/kicad-helper/SKILL.md` (`### 8. Render Netlist to SVG (render-netlist)`), following the existing entries' format (command example + arguments table + one-line scope-limit note).
