#!/bin/bash
# KiCad design review pipeline using integrated skills
set -e

# Default project paths
DEFAULT_SCH="/Users/gary/hardwares/underwater-machine/schematic/underwater-glider.kicad_sch"
DEFAULT_PCB="/Users/gary/hardwares/underwater-machine/schematic/underwater-glider.kicad_pcb"
DEFAULT_BOM="/Users/gary/hardwares/underwater-machine/schematic/bom/bom.csv"
DEFAULT_OUT_DIR="/Users/gary/hardwares/underwater-machine/schematic/analysis"

SCHEMATIC="${1:-$DEFAULT_SCH}"
PCB="${2:-$DEFAULT_PCB}"
BOM="${3:-$DEFAULT_BOM}"
OUT_DIR="${4:-$DEFAULT_OUT_DIR}"

SKILLS_DIR="/Users/gary/.gemini/skills"
SCRIPTS_DIR="/Users/gary/kicad-helper/scratch"

echo "=========================================================="
echo "         RUNNING KICAD CONSOLIDATED DESIGN REVIEW         "
echo "=========================================================="
echo "Schematic: $SCHEMATIC"
echo "PCB:       $PCB"
echo "BOM:       $BOM"
echo "Output:    $OUT_DIR"
echo "----------------------------------------------------------"

mkdir -p "$OUT_DIR"
mkdir -p "$(dirname "$BOM")"

# 1. Schematic analysis
echo -e "\n[1/7] Analyzing Schematic..."
python3 "$SKILLS_DIR/kicad/scripts/analyze_schematic.py" "$SCHEMATIC" --output "$OUT_DIR/schematic_analysis.json"

# 2. PCB analysis
echo -e "\n[2/7] Analyzing PCB Layout..."
python3 "$SKILLS_DIR/kicad/scripts/analyze_pcb.py" "$PCB" --output "$OUT_DIR/pcb_analysis.json"

# 3. Auto-assign MPNs
echo -e "\n[3/7] Auto-assigning Manufacturer Part Numbers (MPNs)..."
python3 "$SCRIPTS_DIR/auto_assign_mpn.py"

# 4. Export BOM
echo -e "\n[4/7] Exporting BOM CSV..."
python3 "$SKILLS_DIR/bom/scripts/bom_manager.py" export "$SCHEMATIC" -o "$BOM" --recursive

# 5. SPICE Simulation
echo -e "\n[5/7] Running SPICE Simulations..."
python3 "$SKILLS_DIR/spice/scripts/simulate_subcircuits.py" "$OUT_DIR/schematic_analysis.json"

# 6. EMC Pre-compliance Analysis
echo -e "\n[6/7] Running EMC Analysis..."
python3 "$SKILLS_DIR/emc/scripts/analyze_emc.py" --pcb "$OUT_DIR/pcb_analysis.json" --output "$OUT_DIR/emc_analysis.json"

# 7. Thermal Analysis
echo -e "\n[7/7] Running Thermal Analysis..."
python3 "$SKILLS_DIR/kicad/scripts/analyze_thermal.py" --schematic "$OUT_DIR/schematic_analysis.json" --pcb "$OUT_DIR/pcb_analysis.json" --output "$OUT_DIR/thermal_analysis.json"

# 8. Consolidate and generate markdown report
echo -e "\nGenerating consolidated design review report..."
python3 "$SCRIPTS_DIR/generate_design_review_report.py"

echo "----------------------------------------------------------"
echo "✅ Design review completed successfully!"
echo "Report: $OUT_DIR/design_review_report.md"
echo "=========================================================="
