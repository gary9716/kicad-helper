# KiCad Helper

A Python library and CLI helper tool to automate symbol generation, collision-free placement, and orthogonal wire routing for KiCad v6+ schematics (`.kicad_sch`) and symbol libraries (`.kicad_sym`).

## Key Features

1. **Auto-expansion of Symbol Dimensions**: 
   Automatically calculates symbol body size to prevent opposite or adjacent pin names from overlapping.
2. **AABB Overlap Resolution**: 
   Iteratively resolves overlaps of placed symbol bodies in the schematic and snaps symbols to the grid (1.27 mm).
3. **Orthogonal Wire Routing (A*)**: 
   Routes wires orthogonally, snapping precisely to the standard 1.27 mm grid. It automatically bypasses component bodies, avoids routing directly over other terminals, and prevents collinear wire-on-wire overlaps while allowing perpendicular crossings.
4. **Collision-free Field Placements**: 
   Automatically detects if Reference (`Reference`) and Value (`Value`) text fields collide with pin names, numbers, connection terminals, or wires (especially when the component is rotated at 90°, 180°, or 270°) and shifts them to collision-free positions.
5. **Interactive Demos & Integrations**:
   Features schematic canvas clearing before running layout procedures.

---

## Directory Structure

```text
├── kicad-helper            # Executable CLI entrypoint wrapper
├── kicad_skill/            # Main package source
│   ├── parser.py           # KiCad S-expression parser & formatter
│   ├── symbol.py           # Symbol library generation & dimensions
│   ├── schematic.py        # Symbol placement, overlap resolution, & A* routing
│   └── main.py             # CLI parser mapping commands to package routines
├── test_project/           # Local demo and playground
│   ├── run_demo.py         # Python-based end-to-end placement & routing demo
│   ├── run_cli_demo.sh     # Shell script showcasing equivalent CLI usage
│   └── test_project.kicad_sch  # Target test schematic file
├── run_integration_test.py # Integration test script
└── README.md               # This documentation
```

---

## How to Run

### 1. Python Demo Script
To run the end-to-end Python demo, execute:
```bash
python3 test_project/run_demo.py
```
This script will:
* Clear all existing components and wires from `test_project/test_project.kicad_sch` to start fresh.
* Generate a custom symbol library `local_test.kicad_sym` with pin specifications.
* Place 3 instances (`U101`, `U102`, `U103` (rotated 90°)) and resolve overlaps.
* Route connection wires orthogonally between them.

### 2. CLI Demo Script
To see how the command-line helper works under the hood, run:
```bash
./test_project/run_cli_demo.sh
```

### 3. Integration Tests
To run integration tests against the communication schematic structure:
```bash
python3 run_integration_test.py
```

---

## CLI Usage

The helper can be run using the `./kicad-helper` CLI wrapper:

### 1. Create a Symbol
```bash
./kicad-helper create-symbol \
  --name "MY_CHIP" \
  --library "path/to/my_lib.kicad_sym" \
  --pins "left:1:VCC:power_in,right:2:GND:power_in" \
  --width 25.4 \
  --height 20.32
```

### 2. Place Symbols and Resolve Overlaps
```bash
./kicad-helper place \
  --schematic "path/to/schematic.kicad_sch" \
  --placements '[{"lib_id": "lib:MY_CHIP", "reference": "U101", "x": 100.0, "y": 100.0, "angle": 0.0}]' \
  --margin 5.08
```

### 3. Connect Pins with Orthogonal Wires
```bash
./kicad-helper connect \
  --schematic "path/to/schematic.kicad_sch" \
  --connections "U101:VCC to U102:GND"
```

---

## 🚀 Integrated Design Review Pipeline

This tool integrates automation skills from the `kicad-happy` framework to perform comprehensive, automated hardware design reviews against KiCad projects. It covers:

1. **Schematic & PCB Analysis**: Extracts component parameters, hierarchical sheet structures, physical trace geometries, and nets.
2. **BOM Preparation & Sourcing**: Automatically extracts and maps Manufacturer Part Numbers (MPNs) from component value patterns, exports tracking CSV files, and synchronizes datasheets.
3. **SPICE Simulation**: Runs ngspice simulations on subcircuits (voltage dividers, RC filters, decoupling networks, regulator feedback loops) to verify electronic characteristics.
4. **EMC Pre-compliance**: Evaluates layout geometries (ground plane continuity, loop areas, switching noise) and rates compliance (Target: CISPR 32).
5. **Thermal Audit**: Calculates power dissipation and junction temperatures to flag potential hotspots (e.g. shunt resistors).
6. **DFM Analysis**: Checks physical design constraints against JLCPCB and PCBWay manufacturing limits.

### Running a Design Review

To run the complete design review pipeline on the default project:
```bash
./run_design_review.sh
```

You can also pass custom schematic, PCB, and output paths:
```bash
./run_design_review.sh <path_to_schematic> <path_to_pcb> <path_to_bom_csv> <path_to_output_dir>
```

Upon completion, a detailed markdown report is generated at:
* [design_review_report.md](file:///Users/gary/hardwares/underwater-machine/schematic/analysis/design_review_report.md)

