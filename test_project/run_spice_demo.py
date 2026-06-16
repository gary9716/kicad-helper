#!/usr/bin/env python3
import os
import sys

# Ensure package path is visible
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kicad_skill.symbol import generate_symbol_sexpr, save_symbol_to_library
from kicad_skill.schematic import place_symbols_and_resolve, connect_symbols_in_schematic

def main():
    demo_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spice_demo")
    os.makedirs(demo_dir, exist_ok=True)
    
    sch_path = os.path.join(demo_dir, "spice_demo.kicad_sch")
    lib_path = os.path.join(demo_dir, "spice_demo.kicad_sym")
    table_path = os.path.join(demo_dir, "sym-lib-table")
    
    print("==========================================================")
    print("      GENERATING SPICE SIMULATION DEMO PROJECT            ")
    print("==========================================================")
    
    # 1. Initialize a blank schematic file
    print("[1/4] Initializing blank schematic...")
    with open(sch_path, 'w', encoding='utf-8') as f:
        f.write("""(kicad_sch
	(version 20260306)
	(generator "eeschema")
	(generator_version "10.0")
	(uuid "3d1b3240-5a3b-419b-a010-090f230588aa")
	(paper "A4" portrait)
	(lib_symbols
	)
)
""")
        
    # 2. Initialize a blank sym-lib-table file
    with open(table_path, 'w', encoding='utf-8') as f:
        f.write(f"""(sym_lib_table
	(lib (name "spice_demo") (type "KiCad") (uri "${{KIPRJMOD}}/spice_demo.kicad_sym") (options "") (descr ""))
)
""")

    # 3. Create custom symbols for resistors and capacitors
    print("[2/4] Generating custom symbols library...")
    
    # Resistor Symbol
    res_pins = [
        {"side": "left", "number": "1", "name": "1", "type": "passive"},
        {"side": "right", "number": "2", "name": "2", "type": "passive"}
    ]
    res_sym = generate_symbol_sexpr("R", res_pins, ref_prefix="R", width=5.08, height=2.54)
    save_symbol_to_library(lib_path, res_sym)
    
    # Capacitor Symbol
    cap_pins = [
        {"side": "left", "number": "1", "name": "1", "type": "passive"},
        {"side": "right", "number": "2", "name": "2", "type": "passive"}
    ]
    cap_sym = generate_symbol_sexpr("C", cap_pins, ref_prefix="C", width=5.08, height=2.54)
    save_symbol_to_library(lib_path, cap_sym)

    # Battery Symbol
    bat_pins = [
        {"side": "left", "number": "1", "name": "VCC", "type": "power_out"},
        {"side": "right", "number": "2", "name": "GND", "type": "passive"}
    ]
    bat_sym = generate_symbol_sexpr("KEYSTONE_2466", bat_pins, ref_prefix="BT", width=12.7, height=12.7)
    save_symbol_to_library(lib_path, bat_sym)
    
    # 4. Place symbols
    # We will create:
    # - RC low-pass filter: R1 (10k) and C1 (100nF)
    # - Voltage divider: R2 (10k) and R3 (10k)
    print("[3/4] Placing symbol instances...")
    placements = [
        # Battery power source
        {"lib_id": "spice_demo:KEYSTONE_2466", "reference": "BT1", "value": "KEYSTONE_2466", "x": 70.0, "y": 80.0, "angle": 0.0, "properties": {"Footprint": "Battery:BatteryHolder_Keystone_2466_1xAAA"}},
        
        # RC filter components
        {"lib_id": "spice_demo:R", "reference": "R1", "value": "10k", "x": 110.0, "y": 80.0, "angle": 0.0, "properties": {"Footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal"}},
        {"lib_id": "spice_demo:C", "reference": "C1", "value": "100nF", "x": 110.0, "y": 80.0, "angle": 90.0, "properties": {"Footprint": "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.54mm"}},
        
        # Voltage divider components
        {"lib_id": "spice_demo:R", "reference": "R2", "value": "10k", "x": 150.0, "y": 80.0, "angle": 0.0, "properties": {"Footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal"}},
        {"lib_id": "spice_demo:R", "reference": "R3", "value": "10k", "x": 150.0, "y": 80.0, "angle": 90.0, "properties": {"Footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal"}}
    ]
    
    resolved = place_symbols_and_resolve(
        schematic_path=sch_path,
        table_path=table_path,
        new_placements=placements,
        margin=7.62,
        resolve=True
    )
    
    print("Resolved placements:")
    for r in resolved:
        print(f"  * {r['ref']} placed at ({r['tx']:.2f}, {r['ty']:.2f})")
    
    # 5. Connect symbols
    print("[4/4] Routing orthogonal wires to connect parts...")
    connections = [
        # RC Filter connections
        {"from": "BT1:VCC", "to": "R1:1"},
        {"from": "R1:2", "to": "C1:1"},
        {"from": "C1:2", "to": "BT1:GND"},
        
        # Voltage Divider connections
        {"from": "BT1:VCC", "to": "R2:1"},
        {"from": "R2:2", "to": "R3:1"},
        {"from": "R3:2", "to": "BT1:GND"}
    ]
    
    num_wires = connect_symbols_in_schematic(
        schematic_path=sch_path,
        table_path=table_path,
        connections=connections,
        orthogonal=True
    )
    
    print("----------------------------------------------------------")
    print("✅ SPICE Demo Project Generated Successfully!")
    print(f"Schematic File: {sch_path}")
    print(f"Symbol Library: {lib_path}")
    print(f"Added {num_wires} connection wires.")
    print("==========================================================")

if __name__ == "__main__":
    main()
