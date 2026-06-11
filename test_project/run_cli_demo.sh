#!/bin/bash
# Exit on error
set -e

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( dirname "$SCRIPT_DIR" )"

# Make sure we run from project root
cd "$PROJECT_ROOT"

echo "=== Running CLI Demo to create/modify test_project ==="

# 1. Reset the schematic to a clean slate
echo "Resetting test_project.kicad_sch..."
cat <<EOF > test_project/test_project.kicad_sch
(kicad_sch
	(version 20260306)
	(generator "eeschema")
	(generator_version "10.0")
	(uuid "1d1b3240-5a3b-419b-a010-090f23058866")
	(paper "A4" portrait)
	(lib_symbols
	)
)
EOF

# 2. Create the custom symbol using CLI
echo "1. Creating symbol 'STM32_DEMO'..."
./kicad-helper create-symbol \
  --name "STM32_DEMO" \
  --library "test_project/local_test.kicad_sym" \
  --pins "left:1:VCC:power_in,left:2:PA0:bidirectional,left:3:PA1:bidirectional,right:4:GND:power_in,right:5:PB0:bidirectional,right:6:PB1:bidirectional,top:7:RST:input,bottom:8:TEST:input" \
  --width 25.4 \
  --height 20.32

# 3. Place symbols using CLI (resolving overlapping coordinates)
echo -e "\n2. Placing symbol instances (U101, U102, U103)..."
./kicad-helper place \
  --schematic "test_project/test_project.kicad_sch" \
  --margin 7.62 \
  --placements '[{"lib_id": "local_test:STM32_DEMO", "reference": "U101", "value": "STM32_DEMO", "x": 100.0, "y": 100.0, "angle": 0.0}, {"lib_id": "local_test:STM32_DEMO", "reference": "U102", "value": "STM32_DEMO", "x": 100.0, "y": 100.0, "angle": 0.0}, {"lib_id": "local_test:STM32_DEMO", "reference": "U103", "value": "STM32_DEMO", "x": 102.0, "y": 102.0, "angle": 90.0}]'

# 4. Connect symbol pins using CLI
echo -e "\n3. Connecting symbol pins with wires..."
./kicad-helper connect \
  --schematic "test_project/test_project.kicad_sch" \
  --connections "U101:PA0 to U102:PB0, U101:VCC to U103:RST"

echo -e "\n=== CLI Demo completed successfully! ==="
