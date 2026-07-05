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
