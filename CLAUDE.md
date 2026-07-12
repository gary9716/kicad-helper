# kicad-helper

Python CLI tool to automate KiCad schematic and symbol library work: symbol generation, collision-free placement, orthogonal wire routing, hierarchical module extraction, wire simplification, and SPICE simulation.

## Build

No separate build step — the project is a plain Python package managed with `uv`.

```bash
# Install dependencies (if any are added)
uv sync
```

## Test

```bash
uv run python -m unittest discover -s tests -v
```

## Binary

```
/Users/ktchou/kicad-helper/kicad-helper
```

Run commands as:
```bash
/Users/ktchou/kicad-helper/kicad-helper <subcommand> [args]
```

## Skills

- CLI usage and subcommand reference: `skills/kicad-helper/SKILL.md`
- Netlist ground-truth planning and evaluation: `.claude/skills/plan-ground-truth-netlist/SKILL.md`

## Schematic Routing Rule (bypass capacitors)

Place bypass capacitors as close as possible to the power/ground pins they filter. Do not route the bypass cap across other signal paths.

## PCB Net Class Conventions (1 oz copper)

| Net Class | Clearance | Track Width | Via Size | Via Drill | Min Vias |
|---|---|---|---|---|---|
| Power_1A | 0.3 mm | 1.0 mm | 0.7 mm | 0.35 mm | 1 |
| Power_2A | 0.4 mm | 2.0 mm | 0.8 mm | 0.4 mm | 2 |
| Power_3A | 0.4 mm | 3.2 mm | 0.8 mm | 0.4 mm | 3 |

## PCB Net Class Conventions (2 oz copper)

| Net Class | Clearance | Track Width | Via Size | Via Drill | Min Vias |
|---|---|---|---|---|---|
| Power_1A | 0.35 mm | 0.5 mm | 0.7 mm | 0.35 mm | 1 |
| Power_2A | 0.4 mm | 1.0 mm | 0.8 mm | 0.4 mm | 2 |
| Power_3A | 0.45 mm | 1.6 mm | 0.8 mm | 0.4 mm | 3 |
