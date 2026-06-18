#!/usr/bin/env python3
import os
import sys

# Ensure package path is visible
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kicad_skill.symbol import generate_symbol_sexpr, save_symbol_to_library
from kicad_skill.schematic import place_symbols_and_resolve, connect_symbols_in_schematic

def main():
    demo_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flashlight_demo")
    os.makedirs(demo_dir, exist_ok=True)
    
    sch_path = os.path.join(demo_dir, "flashlight_demo.kicad_sch")
    lib_path = os.path.join(demo_dir, "flashlight.kicad_sym")
    table_path = os.path.join(demo_dir, "sym-lib-table")
    
    print("==========================================================")
    # 1. Initialize a blank schematic file
    print("[1/4] Initializing blank schematic...")
    with open(sch_path, 'w', encoding='utf-8') as f:
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
    with open(table_path, 'w', encoding='utf-8') as f:
        f.write(f"""(sym_lib_table
	(lib (name "flashlight") (type "KiCad") (uri "${{KIPRJMOD}}/flashlight.kicad_sym") (options "") (descr ""))
)
""")

    # 3. Create custom symbols for the flashlight components
    print("[2/4] Generating custom symbols library...")
    
    # Battery Holder (THT Keystone 2466 1xAAA)
    bat_pins = [
        {"side": "left", "number": "1", "name": "VCC", "type": "power_out"},
        {"side": "right", "number": "2", "name": "GND", "type": "passive"}
    ]
    bat_sym = generate_symbol_sexpr("KEYSTONE_2466", bat_pins, ref_prefix="BT", width=12.7, height=12.7)
    save_symbol_to_library(lib_path, bat_sym)
    
    # Slide Switch (THT OS102011MS2Q SPDT)
    sw_pins = [
        {"side": "left", "number": "1", "name": "COM", "type": "passive"},
        {"side": "right", "number": "2", "name": "NO", "type": "passive"},
        {"side": "bottom", "number": "3", "name": "NC", "type": "no_connect"}
    ]
    sw_sym = generate_symbol_sexpr("OS102011MS2Q", sw_pins, ref_prefix="SW", width=10.16, height=10.16)
    save_symbol_to_library(lib_path, sw_sym)
    
    # Resistor (THT Axial DIN0207)
    res_pins = [
        {"side": "left", "number": "1", "name": "1", "type": "passive"},
        {"side": "right", "number": "2", "name": "2", "type": "passive"}
    ]
    res_sym = generate_symbol_sexpr("R_Axial_DIN0207", res_pins, ref_prefix="R", width=7.62, height=5.08)
    save_symbol_to_library(lib_path, res_sym)
    
    # LED (THT D5.0mm)
    led_pins = [
        {"side": "left", "number": "1", "name": "A", "type": "passive"},
        {"side": "right", "number": "2", "name": "K", "type": "passive"}
    ]
    led_sym = generate_symbol_sexpr("LED_D5.0mm", led_pins, ref_prefix="D", width=7.62, height=5.08)
    save_symbol_to_library(lib_path, led_sym)
    
    # 4. Place symbols
    print("[3/4] Placing symbol instances (with initial overlap at x=100, y=100)...")
    placements = [
        {"lib_id": "flashlight:KEYSTONE_2466", "reference": "BT1", "value": "KEYSTONE_2466", "x": 100.0, "y": 100.0, "angle": 0.0, "properties": {"Footprint": "Battery:BatteryHolder_Keystone_2466_1xAAA"}},
        {"lib_id": "flashlight:OS102011MS2Q", "reference": "SW1", "value": "OS102011MS2Q", "x": 100.0, "y": 100.0, "angle": 0.0, "properties": {"Footprint": "Button_Switch_THT:SW_Slide_SPDT_Straight_CK_OS102011MS2Q"}},
        {"lib_id": "flashlight:R_Axial_DIN0207", "reference": "R1", "value": "R_Axial_DIN0207", "x": 100.0, "y": 100.0, "angle": 0.0, "properties": {"Footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal"}},
        {"lib_id": "flashlight:LED_D5.0mm", "reference": "D1", "value": "LED_D5.0mm", "x": 100.0, "y": 100.0, "angle": 0.0, "properties": {"Footprint": "LED_THT:LED_D5.0mm"}}
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
        {"from": "BT1:VCC", "to": "SW1:COM"},
        {"from": "SW1:NO", "to": "R1:1"},
        {"from": "R1:2", "to": "D1:A"},
        {"from": "D1:K", "to": "BT1:GND"}
    ]
    
    num_wires = connect_symbols_in_schematic(
        schematic_path=sch_path,
        table_path=table_path,
        connections=connections,
        orthogonal=True
    )
    
    print("----------------------------------------------------------")
    print("✅ Flashlight Demo Generated Successfully!")
    print(f"Schematic File: {sch_path}")
    print(f"Symbol Library: {lib_path}")
    print(f"Added {num_wires} connection wires.")
    print("==========================================================")

if __name__ == "__main__":
    main()
