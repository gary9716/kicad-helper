import os
import sys
import shutil
import subprocess
import re

# Ensure python can find our local kicad_skill package
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.symbol import generate_symbol_sexpr, save_symbol_to_library
from kicad_skill.schematic import place_symbols_and_resolve, connect_symbols_in_schematic
from kicad_skill.evaluate_layout import load_sym_lib_table, find_symbol_definition
from kicad_skill.schematic import find_pin_local_data, transform_pin_coordinate, get_symbol_instance_transform

KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"

def run_erc(sch_path, report_path):
    res = subprocess.run([
        KICAD_CLI, "sch", "erc", sch_path, "-o", report_path
    ], capture_output=True, text=True)
    with open(report_path, 'r', encoding='utf-8') as f:
        return f.read()

def main():
    test_dir = os.path.dirname(os.path.abspath(__file__))
    sch_path = os.path.join(test_dir, "dangling_test.kicad_sch")
    pro_path = os.path.join(test_dir, "dangling_test.kicad_pro")
    table_path = os.path.join(test_dir, "sym-lib-table")
    lib_path = os.path.join(test_dir, "dangling_test.kicad_sym")
    erc_report = os.path.join(test_dir, "erc_report.txt")
    
    # 1. Copy project config
    src_pro = "/Users/gary/kicad-helper/scratch/mcp_test/can_node_clean.kicad_pro"
    shutil.copy(src_pro, pro_path)
    
    # 2. Setup sym-lib-table pointing to local dangling_test.kicad_sym
    with open(table_path, 'w') as f:
        f.write(format_sexpr([
            "sym_lib_table",
            ["lib", ["name", "dangling_test"], ["type", "KiCad"], ["uri", "${KIPRJMOD}/dangling_test.kicad_sym"], ["options", ""], ["descr", ""]]
        ]))
        
    # 3. Create empty schematic
    with open(sch_path, 'w') as f:
        f.write(format_sexpr(["kicad_sch", ["version", "20260306"], ["generator", "eeschema"], ["generator_version", "10.0"], ["uuid", "r"], ["paper", "A4"]]))
        
    # 4. Create custom symbols in local library
    # Custom resistor
    res_pins = [
        {"name": "1", "number": "1", "type": "passive", "side": "left"},
        {"name": "2", "number": "2", "type": "output", "side": "right"}
    ]
    save_symbol_to_library(lib_path, generate_symbol_sexpr("MY_RESISTOR", res_pins, ref_prefix="R", width=7.62, height=5.08))
    
    # Custom capacitor
    cap_pins = [
        {"name": "1", "number": "1", "type": "input", "side": "left"},
        {"name": "2", "number": "2", "type": "passive", "side": "right"}
    ]
    save_symbol_to_library(lib_path, generate_symbol_sexpr("MY_CAPACITOR", cap_pins, ref_prefix="C", width=7.62, height=5.08))
    
    # 5. Place symbols (one pair of built-ins, one pair of custom ones)
    # R1/C1 are built-in Device:R and Device:C
    # R2/C2 are custom dangling_test:MY_RESISTOR and dangling_test:MY_CAPACITOR
    placements = [
        {"lib_id": "Device:R", "reference": "R1", "value": "10k", "x": 100.0, "y": 80.0},
        {"lib_id": "Device:C", "reference": "C1", "value": "0.1uF", "x": 120.0, "y": 80.0},
        {"lib_id": "dangling_test:MY_RESISTOR", "reference": "R2", "value": "10k", "x": 100.0, "y": 120.0},
        {"lib_id": "dangling_test:MY_CAPACITOR", "reference": "C2", "value": "0.1uF", "x": 120.0, "y": 120.0}
    ]
    place_symbols_and_resolve(sch_path, table_path, placements, margin=5.08, resolve=True)
    
    # Let's inspect the resolved coordinates of R1:2, C1:1, R2:2, C2:1 before routing
    with open(sch_path, 'r', encoding='utf-8') as f:
        content = f.read()
    sch_sexpr = parse_sexpr(content)
    
    # Load definitions
    lib_map = load_sym_lib_table(table_path)
    
    instances = {}
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child[0] == 'symbol':
            ref = None
            lib_id = None
            for sub in child[1:]:
                if isinstance(sub, list) and len(sub) > 1:
                    if sub[0] == 'lib_id':
                        lib_id = sub[1]
                    elif sub[0] == 'property' and len(sub) > 2 and sub[1] == 'Reference':
                        ref = sub[2]
            if ref:
                instances[ref] = {"sexpr": child, "lib_id": lib_id}
                
    # Add labels to the schematic for TEST 1
    import uuid
    label_uuid1 = str(uuid.uuid4())
    label1 = [
        "label", "TEST_NET",
        ["at", "106.68", "119.38", "0"],
        ["effects", ["font", ["size", "1.27", "1.27"]]],
        ["uuid", label_uuid1]
    ]
    label_uuid2 = str(uuid.uuid4())
    label2 = [
        "label", "TEST_NET2",
        ["at", "100.33", "83.82", "0"],
        ["effects", ["font", ["size", "1.27", "1.27"]]],
        ["uuid", label_uuid2]
    ]
    sch_sexpr.append(label1)
    sch_sexpr.append(label2)
    with open(sch_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sch_sexpr))
                
    local_definitions = {}
    lib_symbols = next((child for child in sch_sexpr[1:] if isinstance(child, list) and child[0] == 'lib_symbols'), None)
    if lib_symbols:
        for child in lib_symbols[1:]:
            if isinstance(child, list) and child[0] == 'symbol':
                local_definitions[child[1]] = child
                
    print("\n--- Diagnostic Coordinate Calculations ---")
    for ref, pin_name in [("R1", "2"), ("C1", "1"), ("R2", "2"), ("C2", "1")]:
        inst = instances[ref]
        defn = local_definitions.get(inst['lib_id'])
        if not defn and inst['lib_id'] and ':' in inst['lib_id']:
            lib_name, sym_name = inst['lib_id'].split(':', 1)
            defn = find_symbol_definition(lib_name, sym_name, lib_map, test_dir)
            
        if not defn:
            print(f"Error: definition for {ref} ({inst['lib_id']}) not found!")
            continue
            
        pin_data = find_pin_local_data(defn, pin_name)
        if not pin_data:
            print(f"Error: pin {pin_name} not found in definition for {ref} ({inst['lib_id']})")
            # Let's print the library symbol's pins
            print(f"Library definition child symbols:")
            for c in defn:
                if isinstance(c, list) and c[0] == 'symbol':
                    print(f"  sub-symbol {c[1]}:")
                    for p in c:
                        if isinstance(p, list) and p[0] == 'pin':
                            print(f"    pin: {p}")
            continue
            
        px, py, orientation = pin_data
        tx, ty, angle, mirror_x, mirror_y = get_symbol_instance_transform(inst['sexpr'])
        gx, gy = transform_pin_coordinate(px, py, tx, ty, angle, mirror_x, mirror_y)
        print(f"Symbol {ref} (lib: {inst['lib_id']}) placed at ({tx}, {ty}):")
        print(f"  Pin {pin_name} local: ({px}, {py}), orientation: {orientation}")
        print(f"  Calculated global: ({gx:.3f}, {gy:.3f})")
        
    # 6. Route connections
    # We want to connect R1:2 to C1:1, and R2:2 to C2:1
    connections = [
        {"from": "R1:2", "to": "C1:1"},
        {"from": "R2:2", "to": "C2:1"}
    ]
    
    # --- TEST 1: Orthogonal Routing ---
    print("\n=== TEST 1: Orthogonal Routing using connect_symbols_in_schematic ===")
    connect_symbols_in_schematic(sch_path, table_path, connections, orthogonal=True)
    
    with open(sch_path, 'r', encoding='utf-8') as f:
        content_test1 = f.read()
    sch_sexpr_test1 = parse_sexpr(content_test1)
    print("Generated Wires in TEST 1:")
    for child in sch_sexpr_test1[1:]:
        if isinstance(child, list) and child[0] == 'wire':
            pts = next((sub for sub in child[1:] if isinstance(sub, list) and sub[0] == 'pts'), None)
            if pts:
                print(f"  Wire: {[p for p in pts[1:] if isinstance(p, list)]}")
                
    erc_report_path1 = os.path.join(test_dir, "erc_report_test1.txt")
    erc_output1 = run_erc(sch_path, erc_report_path1)
    print("ERC Output for TEST 1:")
    print(erc_output1)
    
    # --- TEST 2: Manual Direct Connections ---
    print("\n=== TEST 2: Manual Direct Connections (straight lines) ===")
    # Reload schematic without wires
    with open(sch_path, 'w', encoding='utf-8') as f:
        # Re-create empty schematic and place symbols again
        f.write(format_sexpr(["kicad_sch", ["version", "20260306"], ["generator", "eeschema"], ["generator_version", "10.0"], ["uuid", "r"], ["paper", "A4"]]))
    place_symbols_and_resolve(sch_path, table_path, placements, margin=5.08, resolve=True)
    
    # Read the schematic with placed symbols
    with open(sch_path, 'r', encoding='utf-8') as f:
        content_placed = f.read()
    sch_sexpr_placed = parse_sexpr(content_placed)
    
    # Get pin coordinates from the placed symbol instances
    # R1:2, C1:1
    # For R1:2: placed at (100.33, 80.01), pin local (0, -3.81) -> global (100.330, 83.820)
    # For C1:1: placed at (119.38, 80.01), pin local (0, 3.81) -> global (119.380, 76.200)
    # For R2:2: placed at (99.06, 119.38), pin local (7.62, 0) -> global (106.680, 119.380)
    # For C2:1: placed at (119.38, 119.38), pin local (-7.62, 0) -> global (111.760, 119.380)
    import uuid
    def make_wire_sexpr_clean(x1, y1, x2, y2):
        def fmt(v):
            s = f"{v:.4f}"
            if '.' in s:
                s = s.rstrip('0').rstrip('.')
            return s
        uid = str(uuid.uuid4())
        return [
            "wire",
            ["pts", ["xy", fmt(x1), fmt(y1)], ["xy", fmt(x2), fmt(y2)]],
            ["stroke", ["width", "0"], ["type", "default"]],
            ["uuid", uid]
        ]
    
    # Add direct wire R1:2 to C1:1
    wire1 = make_wire_sexpr_clean(100.330, 83.820, 119.380, 76.200)
    # Add direct wire R2:2 to C2:1
    wire2 = make_wire_sexpr_clean(106.680, 119.380, 111.760, 119.380)
    
    # Add a label at (109.22, 119.38) - middle of wire2
    label_uuid1 = str(uuid.uuid4())
    label1 = [
        "label", "TEST_NET",
        ["at", "109.22", "119.38", "0"],
        ["effects", ["font", ["size", "1.27", "1.27"]]],
        ["uuid", label_uuid1]
    ]
    
    # Add a label at (109.855, 80.010) - middle of wire1
    label_uuid2 = str(uuid.uuid4())
    label2 = [
        "label", "TEST_NET2",
        ["at", "109.855", "80.01", "0"],
        ["effects", ["font", ["size", "1.27", "1.27"]]],
        ["uuid", label_uuid2]
    ]
    
    sch_sexpr_placed.append(wire1)
    sch_sexpr_placed.append(wire2)
    sch_sexpr_placed.append(label1)
    sch_sexpr_placed.append(label2)
    
    with open(sch_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sch_sexpr_placed))
        
    print("Generated Wires in TEST 2:")
    for child in sch_sexpr_placed[1:]:
        if isinstance(child, list) and child[0] == 'wire':
            pts = next((sub for sub in child[1:] if isinstance(sub, list) and sub[0] == 'pts'), None)
            if pts:
                print(f"  Wire: {[p for p in pts[1:] if isinstance(p, list)]}")
                
    erc_report_path2 = os.path.join(test_dir, "erc_report_test2.txt")
    erc_output2 = run_erc(sch_path, erc_report_path2)
    print("ERC Output for TEST 2:")
    print(erc_output2)

if __name__ == "__main__":
    main()
