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
│   ├── run_flashlight_demo.py  # Python-based flashlight schematic layout demo
│   ├── run_flashlight_e2e.py   # Python-based flashlight end-to-end schematic + PCB autorouting pipeline
│   └── test_project.kicad_sch  # Target test schematic file
├── run_integration_test.py # Integration test script
└── README.md               # This documentation
```

---

## How to Run

### 1. Python Demo Script
To run the end-to-end Python demo, execute:
```bash
uv run test_project/run_demo.py
```
This script will:
* Clear all existing components and wires from `test_project/test_project.kicad_sch` to start fresh.
* Generate a custom symbol library `local_test.kicad_sym` with pin specifications.
* Place 3 instances (`U101`, `U102`, `U103` (rotated 90°)) and resolve overlaps.
* Route connection wires orthogonally between them.

### 2. Flashlight E2E Design Pipeline
To run the complete flashlight E2E schematic and PCB design pipeline (using **THT footprints with verified 3D models**):
```bash
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 test_project/run_flashlight_e2e.py
```
This script automates:
1. Creating a custom schematic symbol library containing the THT components:
   * **Battery Holder**: `KEYSTONE_2466` (AAA Battery Holder)
   * **Slide Switch**: `OS102011MS2Q` (SPDT Slide Switch)
   * **Resistor**: `R_Axial_DIN0207` (Axial Resistor)
   * **LED**: `LED_D5.0mm` (5mm Radial LED)
2. Placing and snapping symbols to the grid, and routing orthogonal connection wires.
3. Automatically centering the schematic symbols and wires on the paper sheet.
4. Performing a Schematic Electrical Rules Check (ERC).
5. Initializing a PCB layout, drawing a centered board outline, placing THT footprints, and assigning nets.
6. Exporting a Specctra DSN design, running FreeRouting CLI in headless mode to route THT tracks, and importing the Specctra SES session back into the PCB.
7. Executing a PCB Design Rules Check (DRC) to verify there are 0 violations and 0 unconnected pins.
8. Exporting the Bill of Materials (BOM) and running a DFM compliance audit.

### 3. CLI Demo Script
To see how the command-line helper works under the hood, run:
```bash
./test_project/run_cli_demo.sh
```

### 4. Integration Tests
To run integration tests against the communication schematic structure:
```bash
uv run run_integration_test.py
```

### 5. Unit Tests
To run the mathematical layout, geometry bounding box, and A* grid routing unit tests:
```bash
uv run python -m unittest discover -s tests
```

### 6. CI/CD Pipeline
A GitHub Actions workflow is configured in [.github/workflows/ci.yml](file:///Users/gary/kicad-helper/.github/workflows/ci.yml). It automatically runs the full suite of unit and integration tests across Python versions 3.10, 3.11, and 3.12 on every push and pull request to the `main` branch.

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

---

## 📐 Programmatic Schematic & Library Generation Guidelines

When generating KiCad schematics (`.kicad_sch`) or library files (`.kicad_sym`) programmatically, adhere to the following rules to prevent connectivity issues, syntax errors, and ERC/DRC failures:

### 1. Grid Snapping
KiCad schematics use a strict **50-mil (1.27 mm) grid** for pins, wires, junctions, and labels. Programmatically generated positions must be snapped to multiples of `1.27 mm` to ensure KiCad recognizes connections.
* **Math Snapping Function**:
  ```python
  def snap_to_grid(val):
      return round(val / 1.27) * 1.27
  ```
* **Wire/Label Snap**: A wire must connect *exactly* to a pin's absolute coordinate and extend horizontally or vertically, snapping its other end (and any label placed there) to the 50-mil grid.

### 2. Coordinate Systems & Y-Axis Inversion
* **Symbol Library (`.kicad_sym`)**: Uses math convention (**Y-Up**, positive Y is upwards).
* **Schematic File (`.kicad_sch`)**: Uses screen convention (**Y-Down**, positive Y is downwards).
* **Placement Formulas**:
  * For relative pin offsets `(pin_x, pin_y)` inside a library symbol placed at `(symbol_x, symbol_y)` in the schematic:
    * `absolute_pin_x = symbol_x + pin_x`
    * `absolute_pin_y = symbol_y - pin_y` (Note the subtraction to invert Y-axis coordinate convention).
  * For reference designators and values:
    * `ref_y = symbol_y - bbox_max_y - 2.54`
    * `val_y = symbol_y - bbox_min_y + 2.54`

### 3. Symbol Library Setup & Naming
* **Library Name Registration**: When using custom symbols like `MyLibrary:MySymbol`, the library name `MyLibrary` must be registered in the project's local `sym-lib-table` pointing to the physical `.kicad_sym` file (e.g. `(uri "${KIPRJMOD}/MyLibrary.kicad_sym")`).
* **S-Expression Formats**:
  * **In `sym-lib-table`**: Register the library with its name and type.
  * **In `.kicad_sym`**: The symbol header must omit the library name prefix: `(symbol "MySymbol" ...)`.
  * **In `lib_symbols` section of `.kicad_sch`**: The symbol header must include the library prefix: `(symbol "MyLibrary:MySymbol" ...)`.
* **Sub-Symbol Unit Naming**: Standard sub-symbols (units) inside a parent symbol must follow strict suffix conventions:
  * Graphical/Background units: `"{symbol_name}_0_1"` (often contains background shape).
  * Pin/Functional units: `"{symbol_name}_1_1"` (contains functional pins for Unit 1, Style 1).
  * Do NOT use custom prefixes/names for these units that violate the `SymbolName_Unit_Style` template, or KiCad will fail to load the schematic with "Invalid symbol unit name prefix" errors.

### 4. Electrical Pin Types for Buses
* **Shared Bus Outputs**: Pin electrical types for shared bus drivers (e.g., MISO pins on multiple SPI slave modules connected to the same MCU MISO line) must be defined as `tri_state` (or `passive` if necessary), but **never** as `output`. Defining them as `output` will cause KiCad's Electrical Rules Checker (ERC) to report conflicting push-pull output connections.
* **Electrical Type Syntax**: Valid electrical types for pins are `input`, `output`, `bidirectional`, `tri_state`, `passive`, `free`, `unspecified`, `power_in`, `power_out`, `open_collector`, `open_emitter`. Do NOT wrap these strings in double quotes inside the S-expression pin statement (e.g., use `(pin input line ...)` instead of `(pin "input" line ...)`).
