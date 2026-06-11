import unittest
import sys
import os
import shutil

# Ensure package path is visible
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.symbol import generate_symbol_sexpr, save_symbol_to_library
from kicad_skill.schematic import place_symbols_and_resolve, connect_symbols_in_schematic

class TestFlashlightE2E(unittest.TestCase):
    def setUp(self):
        # Create a temp directory for E2E files
        self.test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flashlight_temp")
        os.makedirs(self.test_dir, exist_ok=True)
        
        self.sch_path = os.path.join(self.test_dir, "flashlight.kicad_sch")
        self.lib_path = os.path.join(self.test_dir, "flashlight.kicad_sym")
        self.table_path = os.path.join(self.test_dir, "sym-lib-table")
        
        # 1. Initialize a blank schematic file
        with open(self.sch_path, 'w', encoding='utf-8') as f:
            f.write("""(kicad_sch
	(version 20260306)
	(generator "eeschema")
	(generator_version "10.0")
	(uuid "2d1b3240-5a3b-419b-a010-090f23058899")
	(paper "A4" portrait)
	(lib_symbols
	)
)
""")
            
        # 2. Initialize a blank sym-lib-table file
        with open(self.table_path, 'w', encoding='utf-8') as f:
            f.write(f"""(sym_lib_table
	(lib (name "flashlight") (type "KiCad") (uri "${{KIPRJMOD}}/flashlight.kicad_sym") (options "") (descr ""))
)
""")

    def tearDown(self):
        # Clean up temp directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_flashlight_design_flow(self):
        # Step 1: Create custom symbols for the flashlight components
        print("\n--- Creating custom symbols ---")
        
        # Battery Holder (Keystone 3003)
        bat_pins = [
            {"side": "left", "number": "1", "name": "VCC", "type": "power_out"},
            {"side": "right", "number": "2", "name": "GND", "type": "power_out"}
        ]
        bat_sym = generate_symbol_sexpr("KEYSTONE_3003", bat_pins, ref_prefix="BT", width=12.7, height=12.7)
        save_symbol_to_library(self.lib_path, bat_sym)
        
        # Slide Switch (CL-SB-12B-02T)
        sw_pins = [
            {"side": "left", "number": "1", "name": "COM", "type": "passive"},
            {"side": "right", "number": "2", "name": "NO", "type": "passive"},
            {"side": "bottom", "number": "3", "name": "NC", "type": "passive"}
        ]
        sw_sym = generate_symbol_sexpr("CL_SB_12B_02T", sw_pins, ref_prefix="SW", width=10.16, height=10.16)
        save_symbol_to_library(self.lib_path, sw_sym)
        
        # Resistor (RC0805FR)
        res_pins = [
            {"side": "left", "number": "1", "name": "1", "type": "passive"},
            {"side": "right", "number": "2", "name": "2", "type": "passive"}
        ]
        res_sym = generate_symbol_sexpr("RC0805FR", res_pins, ref_prefix="R", width=7.62, height=5.08)
        save_symbol_to_library(self.lib_path, res_sym)
        
        # LED (Everlight 19-217)
        led_pins = [
            {"side": "left", "number": "1", "name": "A", "type": "passive"},
            {"side": "right", "number": "2", "name": "K", "type": "passive"}
        ]
        led_sym = generate_symbol_sexpr("EVERLIGHT_19_217", led_pins, ref_prefix="D", width=7.62, height=5.08)
        save_symbol_to_library(self.lib_path, led_sym)
        
        # Verify the library was created and contains the symbols
        self.assertTrue(os.path.exists(self.lib_path))
        with open(self.lib_path, 'r', encoding='utf-8') as f:
            lib_content = f.read()
        self.assertIn("KEYSTONE_3003", lib_content)
        self.assertIn("CL_SB_12B_02T", lib_content)
        self.assertIn("RC0805FR", lib_content)
        self.assertIn("EVERLIGHT_19_217", lib_content)
        
        # Step 2: Place all 4 symbols onto the schematic and resolve overlap collisions
        print("--- Placing symbol instances ---")
        placements = [
            {"lib_id": "flashlight:KEYSTONE_3003", "reference": "BT1", "value": "KEYSTONE_3003", "x": 100.0, "y": 100.0, "angle": 0.0},
            {"lib_id": "flashlight:CL_SB_12B_02T", "reference": "SW1", "value": "CL_SB_12B_02T", "x": 100.0, "y": 100.0, "angle": 0.0}, # Intentional overlap to test resolution
            {"lib_id": "flashlight:RC0805FR", "reference": "R1", "value": "RC0805FR", "x": 100.0, "y": 100.0, "angle": 0.0}, # Intentional overlap
            {"lib_id": "flashlight:EVERLIGHT_19_217", "reference": "D1", "value": "EVERLIGHT_19_217", "x": 100.0, "y": 100.0, "angle": 0.0} # Intentional overlap
        ]
        
        resolved = place_symbols_and_resolve(
            schematic_path=self.sch_path,
            table_path=self.table_path,
            new_placements=placements,
            margin=7.62,
            resolve=True
        )
        
        # Assert placement resolved overlaps
        self.assertEqual(len(resolved), 4)
        positions = [(r['tx'], r['ty']) for r in resolved]
        # Coordinates must all be distinct
        self.assertEqual(len(set(positions)), 4, "Overlapping components did not get shifted to distinct coordinates!")
        
        # Step 3: Connect components using orthogonal wires
        print("--- Routing connection wires ---")
        connections = [
            {"from": "BT1:VCC", "to": "SW1:COM"},
            {"from": "SW1:NO", "to": "R1:1"},
            {"from": "R1:2", "to": "D1:A"},
            {"from": "D1:K", "to": "BT1:GND"}
        ]
        
        num_wires = connect_symbols_in_schematic(
            schematic_path=self.sch_path,
            table_path=self.table_path,
            connections=connections,
            orthogonal=True
        )
        
        # Verify wires were added successfully
        self.assertTrue(num_wires > 0, "No wires were routed in the E2E flashlight schematic!")
        
        # Read schematic to verify wire blocks are present
        with open(self.sch_path, 'r', encoding='utf-8') as f:
            sch_content = f.read()
        self.assertIn("(wire", sch_content)
        print(f"E2E Flashlight Design Test successful! Added {num_wires} wire segments.")

if __name__ == "__main__":
    unittest.main()
