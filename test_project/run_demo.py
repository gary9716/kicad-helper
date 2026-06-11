import os
import sys

# Ensure python can find our local kicad_skill package
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.symbol import generate_symbol_sexpr, save_symbol_to_library
from kicad_skill.schematic import place_symbols_and_resolve, connect_symbols_in_schematic

def main():
    schematic_dir = os.path.dirname(os.path.abspath(__file__))
    lib_path = os.path.join(schematic_dir, "local_test.kicad_sym")
    sch_path = os.path.join(schematic_dir, "test_project.kicad_sch")
    table_path = os.path.join(schematic_dir, "sym-lib-table")
    
    # 0. Clear existing symbols and wires from the schematic
    print("--- 0. Clearing Existing Symbols and Wires from Schematic ---")
    if os.path.exists(sch_path):
        with open(sch_path, 'r', encoding='utf-8') as f:
            content = f.read()
        try:
            sch_sexpr = parse_sexpr(content)
            if sch_sexpr and sch_sexpr[0] == 'kicad_sch':
                new_children = []
                for child in sch_sexpr[1:]:
                    if isinstance(child, list) and len(child) > 0:
                        if child[0] in ('symbol', 'wire'):
                            continue
                    new_children.append(child)
                sch_sexpr = [sch_sexpr[0]] + new_children
                with open(sch_path, 'w', encoding='utf-8') as f:
                    f.write(format_sexpr(sch_sexpr))
                print(f"Successfully cleared all components and wires in {sch_path}")
        except Exception as e:
            print(f"Failed to clear schematic: {e}")
            
    print("\n--- 1. Generating Custom Symbol 'STM32_DEMO' ---")
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
        name="STM32_DEMO",
        pins=pins,
        ref_prefix="U",
        width=25.4,
        height=20.32
    )
    
    save_symbol_to_library(lib_path, symbol_def)
    print(f"Generated symbol and saved to {lib_path}")
    
    # 2. Place multiple symbols in schematic at overlapping locations
    print("\n--- 2. Placing Symbols and Resolving Overlaps ---")
    new_placements = [
        {
            "lib_id": "local_test:STM32_DEMO",
            "reference": "U101",
            "value": "STM32_DEMO",
            "x": 100.0,
            "y": 100.0,
            "angle": 0.0,
            "properties": {"Footprint": "Package:QFP-8"}
        },
        {
            "lib_id": "local_test:STM32_DEMO",
            "reference": "U102",
            "value": "STM32_DEMO",
            "x": 100.0,
            "y": 100.0,
            "angle": 0.0,
            "properties": {"Footprint": "Package:QFP-8"}
        },
        {
            "lib_id": "local_test:STM32_DEMO",
            "reference": "U103",
            "value": "STM32_DEMO",
            "x": 102.0,
            "y": 102.0,
            "angle": 90.0,
            "properties": {"Footprint": "Package:QFP-8"}
        }
    ]
    
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
            
    # 3. Connect symbols together
    print("\n--- 3. Connecting Symbols with Wires ---")
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

if __name__ == "__main__":
    main()
