import os
import sys

# Ensure python can find our local kicad_skill package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.symbol import generate_symbol_sexpr, save_symbol_to_library
from kicad_skill.schematic import place_symbols_and_resolve, connect_symbols_in_schematic

def main():
    # Target paths in the user's workspace for testing
    workspace_dir = "/Users/gary/hardwares/underwater-machine"
    schematic_dir = os.path.join(workspace_dir, "schematic")
    
    if not os.path.exists(schematic_dir):
        schematic_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_project")
        print(f"Target schematic directory '{workspace_dir}/schematic' not found. Falling back to local '{schematic_dir}'.")
        
    lib_path = os.path.join(schematic_dir, "local_test.kicad_sym")
    if "test_project" in schematic_dir:
        sch_path = os.path.join(schematic_dir, "test_project.kicad_sch")
    else:
        sch_path = os.path.join(schematic_dir, "communication.kicad_sch")
    table_path = os.path.join(schematic_dir, "sym-lib-table")
    
    print("--- 1. Generating Custom Symbol ---")
    pins = [
        {"side": "left", "number": "1", "name": "VCC", "type": "power_in"},
        {"side": "left", "number": "2", "name": "PA0", "type": "bidirectional"},
        {"side": "left", "number": "3", "name": "PA1", "type": "bidirectional"},
        {"side": "right", "number": "4", "name": "GND", "type": "power_in"},
        {"side": "right", "number": "5", "name": "PB0", "type": "bidirectional"},
        {"side": "right", "number": "6", "name": "PB1", "type": "bidirectional"},
        {"side": "top", "number": "7", "name": "RST", "type": "input"},
        {"side": "bottom", "number": "8", "name": "TEST", "type": "input"}
    ]
    
    symbol_def = generate_symbol_sexpr(
        name="STM32_TEST",
        pins=pins,
        ref_prefix="U",
        width=25.4,
        height=20.32
    )
    
    save_symbol_to_library(lib_path, symbol_def)
    print(f"Generated symbol and saved to {lib_path}")
    
    # 2. Register library in sym-lib-table if not already present
    print("\n--- 2. Registering Library in sym-lib-table ---")
    if os.path.exists(table_path):
        with open(table_path, 'r', encoding='utf-8') as f:
            content = f.read()
        table_sexpr = parse_sexpr(content)
        
        is_registered = False
        for child in table_sexpr[1:]:
            if isinstance(child, list) and child[0] == 'lib':
                for prop in child[1:]:
                    if isinstance(prop, list) and prop[0] == 'name' and prop[1] == 'local_test':
                        is_registered = True
                        break
                        
        if not is_registered:
            lib_uri = "${KIPRJMOD}/local_test.kicad_sym"
            new_lib = ["lib", ["name", "local_test"], ["type", "KiCad"], ["uri", lib_uri], ["options", ""], ["descr", "Test Custom Library"]]
            table_sexpr.append(new_lib)
            with open(table_path, 'w', encoding='utf-8') as f:
                f.write(format_sexpr(table_sexpr))
            print(f"Registered local_test in sym-lib-table")
        else:
            print("local_test already registered in sym-lib-table")
            
    # 3. Place multiple symbols in schematic
    print("\n--- 3. Placing Symbols and Resolving Overlaps ---")
    new_placements = [
        {
            "lib_id": "local_test:STM32_TEST",
            "reference": "U101",
            "value": "STM32_TEST",
            "x": 150.0,
            "y": 100.0,
            "angle": 0.0,
            "properties": {"Footprint": "Package:QFP-8"}
        },
        {
            "lib_id": "local_test:STM32_TEST",
            "reference": "U102",
            "value": "STM32_TEST",
            "x": 150.0,
            "y": 100.0,
            "angle": 0.0,
            "properties": {"Footprint": "Package:QFP-8"}
        },
        {
            "lib_id": "local_test:STM32_TEST",
            "reference": "U103",
            "value": "STM32_TEST",
            "x": 152.0,
            "y": 102.0,
            "angle": 90.0,
            "properties": {"Footprint": "Package:QFP-8"}
        }
    ]
    
    sch_backup = sch_path + ".bak"
    if os.path.exists(sch_path):
        with open(sch_path, 'r', encoding='utf-8') as f:
            orig_sch = f.read()
        with open(sch_backup, 'w', encoding='utf-8') as f:
            f.write(orig_sch)
        print("Backed up schematic to .bak")
        
    try:
        resolved = place_symbols_and_resolve(
            schematic_path=sch_path,
            table_path=table_path,
            new_placements=new_placements,
            margin=5.08,
            resolve=True
        )
        print("\nResults of Placement:")
        for r in resolved:
            if r['movable']:
                print(f"  Placed {r['ref']} at ({r['tx']:.2f}, {r['ty']:.2f})")
                
        # 4. Connect symbols together
        print("\n--- 4. Connecting Symbols with Wires ---")
        connections = [
            {"from": "U101:PA0", "to": "U102:PB0"},
            {"from": "U101:VCC", "to": "U103:RST"}
        ]
        
        num_wires = connect_symbols_in_schematic(
            schematic_path=sch_path,
            table_path=table_path,
            connections=connections,
            orthogonal=True
        )
        print(f"Successfully added {num_wires} connection wires!")
        
    finally:
        # Clean up backup
        if os.path.exists(sch_backup):
            os.remove(sch_backup)
            print("Cleaned up test backup file.")

if __name__ == "__main__":
    main()
