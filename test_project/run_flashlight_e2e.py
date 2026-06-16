#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import shutil

# Ensure package path is visible
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pcbnew
from kicad_skill.symbol import generate_symbol_sexpr, save_symbol_to_library
from kicad_skill.schematic import place_symbols_and_resolve, connect_symbols_in_schematic

def center_schematic(sch_path):
    from kicad_skill.parser import parse_sexpr, format_sexpr
    with open(sch_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    sch_sexpr = parse_sexpr(content)
    
    min_x, min_y = float('inf'), float('inf')
    max_x, max_y = float('-inf'), float('-inf')
    has_symbols = False
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child[0] == 'symbol':
            for sub in child[1:]:
                if isinstance(sub, list) and sub[0] == 'at':
                    x = float(sub[1])
                    y = float(sub[2])
                    min_x = min(min_x, x)
                    max_x = max(max_x, x)
                    min_y = min(min_y, y)
                    max_y = max(max_y, y)
                    has_symbols = True
                    break
                    
    if not has_symbols:
        return
        
    bb_w = max_x - min_x
    bb_h = max_y - min_y
    
    # Required size with 40mm margin on all sides (total 80mm margin)
    required_w = bb_w + 80.0
    required_h = bb_h + 80.0
    
    PAPERS = [
        ("A4", 297.0, 210.0),
        ("A3", 420.0, 297.0),
        ("A2", 594.0, 420.0),
        ("A1", 841.0, 594.0),
        ("A0", 1189.0, 841.0)
    ]
    
    selected_paper = "A4"
    paper_w = 297.0
    paper_h = 210.0
    for name, w, h in PAPERS:
        if required_w <= w and required_h <= h:
            selected_paper = name
            paper_w = w
            paper_h = h
            break
    else:
        selected_paper = "A0"
        paper_w = 1189.0
        paper_h = 841.0
        
    # Update paper node in sch_sexpr
    paper_found = False
    for idx, child in enumerate(sch_sexpr):
        if isinstance(child, list) and child[0] == 'paper':
            sch_sexpr[idx] = ['paper', selected_paper]
            paper_found = True
            break
    if not paper_found:
        sch_sexpr.insert(4, ['paper', selected_paper])
        
    paper_center_x = paper_w / 2.0
    paper_center_y = paper_h / 2.0
    symbols_center_x = (min_x + max_x) / 2.0
    symbols_center_y = (min_y + max_y) / 2.0
    
    dx = round((paper_center_x - symbols_center_x) / 1.27) * 1.27
    dy = round((paper_center_y - symbols_center_y) / 1.27) * 1.27
    
    print(f"Centering schematic: symbols center ({symbols_center_x:.2f}, {symbols_center_y:.2f}) -> paper '{selected_paper}' center ({paper_center_x:.2f}, {paper_center_y:.2f})")
    print(f"  * Shifting all elements by dx={dx:.4f}, dy={dy:.4f}")
    
    def shift_node(node):
        if not isinstance(node, list):
            return
        tag = node[0]
        if tag == 'lib_symbols':
            return
            
        if tag == 'at' and len(node) >= 3:
            node[1] = f"{float(node[1]) + dx:.4f}"
            node[2] = f"{float(node[2]) + dy:.4f}"
        elif tag == 'xy' and len(node) >= 3:
            node[1] = f"{float(node[1]) + dx:.4f}"
            node[2] = f"{float(node[2]) + dy:.4f}"
            
        for child in node[1:]:
            if isinstance(child, list):
                shift_node(child)
                
    shift_node(sch_sexpr)
    
    with open(sch_path, 'w', encoding='utf-8') as f:
        f.write(format_sexpr(sch_sexpr))

def add_edge_line(board, start_x, start_y, end_x, end_y):
    segment = pcbnew.PCB_SHAPE(board)
    segment.SetShape(pcbnew.SHAPE_T_SEGMENT)
    segment.SetStart(pcbnew.VECTOR2I(int(start_x * 1000000), int(start_y * 1000000)))
    segment.SetEnd(pcbnew.VECTOR2I(int(end_x * 1000000), int(end_y * 1000000)))
    segment.SetLayer(pcbnew.Edge_Cuts)
    segment.SetWidth(int(0.15 * 1000000))  # 0.15 mm outline line width
    board.Add(segment)

def get_or_create_net(board, net_name):
    net = board.FindNet(net_name)
    if not net:
        net = pcbnew.NETINFO_ITEM(board, net_name)
        board.Add(net)
    return net

def main():
    demo_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flashlight_demo")
    os.makedirs(demo_dir, exist_ok=True)

    sch_path = os.path.join(demo_dir, "flashlight_demo.kicad_sch")
    lib_path = os.path.join(demo_dir, "flashlight.kicad_sym")
    table_path = os.path.join(demo_dir, "sym-lib-table")
    pro_path = os.path.join(demo_dir, "flashlight_demo.kicad_pro")
    pcb_path = os.path.join(demo_dir, "flashlight_demo.kicad_pcb")
    dsn_path = os.path.join(demo_dir, "flashlight_demo.dsn")
    ses_path = os.path.join(demo_dir, "flashlight_demo.ses")
    bom_path = os.path.join(demo_dir, "flashlight_bom.csv")
    erc_report_path = os.path.join(demo_dir, "erc_report.txt")
    drc_report_path = os.path.join(demo_dir, "drc_report.txt")
    pcb_analysis_path = os.path.join(demo_dir, "pcb_analysis.json")

    kicad_cli = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
    java_bin = "/opt/homebrew/opt/openjdk/bin/java"
    freerouting_jar = os.path.join(os.path.dirname(os.path.abspath(__file__)), "freerouting.jar")
    skills_dir = "/Users/gary/.gemini/skills"

    print("=================================================================")
    print("               E2E FLASHLIGHT HARDWARE FLOW DESIGN               ")
    print("=================================================================")

    # 1. Clear or initialize files
    print("\n[1/11] Initializing schematic and project...")
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
        
    with open(table_path, 'w', encoding='utf-8') as f:
        f.write(f"""(sym_lib_table
	(lib (name "flashlight") (type "KiCad") (uri "${{KIPRJMOD}}/flashlight.kicad_sym") (options "") (descr ""))
)
""")

    # Copy project template if it exists
    template_pro = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_project.kicad_pro")
    if os.path.exists(template_pro):
        shutil.copy(template_pro, pro_path)
    else:
        with open(pro_path, 'w', encoding='utf-8') as f:
            f.write("{}")

    # 2. Custom Symbol Library creation
    print("\n[2/11] Generating custom symbols...")
    
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
    print("Custom symbols saved to library.")

    # 3. Placement
    print("\n[3/11] Placing symbols on schematic...")
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
    for r in resolved:
        print(f"  * {r['ref']} -> ({r['tx']:.2f}, {r['ty']:.2f})")

    # 4. Schematic Routing
    print("\n[4/11] Routing orthogonal connection wires on schematic...")
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
    print(f"Added {num_wires} wire segments in schematic.")

    # Centering Schematic Elements on Paper Sheet
    print("  * Centering schematic symbols and wires on paper...")
    center_schematic(sch_path)

    # 5. Schematic ERC
    print("\n[5/11] Running Schematic Electrical Rules Check (ERC)...")
    res = subprocess.run([
        kicad_cli, "sch", "erc", sch_path, "-o", erc_report_path
    ], cwd=demo_dir, capture_output=True, text=True)
    if os.path.exists(erc_report_path):
        with open(erc_report_path, 'r', encoding='utf-8') as f:
            print(f.read().strip())
    else:
        print(f"ERC failed to run. Stderr: {res.stderr}")

    # 6. PCB Design Generation
    print("\n[6/11] Building PCB layout programmatically via pcbnew (centering elements on A4 sheet)...")
    board = pcbnew.BOARD()

    # Dynamic calculation of PCB coordinates centered on A4 page
    # Initial footprint relative positions (spaced out for THT footprints)
    init_pos = {
        "BT1": (30.0, 50.0),
        "SW1": (85.0, 50.0),
        "R1": (105.0, 45.0),
        "D1": (105.0, 55.0)
    }
    
    xs = [p[0] for p in init_pos.values()]
    ys = [p[1] for p in init_pos.values()]
    min_x_pos, max_x_pos = min(xs), max(xs)
    min_y_pos, max_y_pos = min(ys), max(ys)
    
    margin_mm = 25.0
    out_min_x = min_x_pos - margin_mm
    out_max_x = max_x_pos + margin_mm
    out_min_y = min_y_pos - margin_mm
    out_max_y = max_y_pos + margin_mm
    
    board_center_x = (out_min_x + out_max_x) / 2.0
    board_center_y = (out_min_y + out_max_y) / 2.0
    
    page_center_x = 148.5  # A4 width / 2
    page_center_y = 105.0  # A4 height / 2
    
    # Translation offset
    dx = page_center_x - board_center_x
    dy = page_center_y - board_center_y

    # Draw centered Board Outline
    print(f"  * Drawing board outline centered at ({page_center_x:.2f}, {page_center_y:.2f}) mm...")
    add_edge_line(board, out_min_x + dx, out_min_y + dy, out_max_x + dx, out_min_y + dy)
    add_edge_line(board, out_max_x + dx, out_min_y + dy, out_max_x + dx, out_max_y + dy)
    add_edge_line(board, out_max_x + dx, out_max_y + dy, out_min_x + dx, out_max_y + dy)
    add_edge_line(board, out_min_x + dx, out_max_y + dy, out_min_x + dx, out_min_y + dy)

    # Setup Net definitions
    net_vcc = get_or_create_net(board, "VCC")
    net_gnd = get_or_create_net(board, "GND")
    net_r1 = get_or_create_net(board, "Net-(R1-Pad1)")
    net_d1 = get_or_create_net(board, "Net-(D1-Pad1)")

    # Load footprints and place at centered positions
    print("  * Loading and placing footprints...")
    shared_support = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
    
    # BT1 (Battery Holder THT)
    bt1 = pcbnew.FootprintLoad(os.path.join(shared_support, "Battery.pretty"), "BatteryHolder_Keystone_2466_1xAAA")
    bt1.SetReference("BT1")
    bt1.SetPosition(pcbnew.VECTOR2I(int((init_pos["BT1"][0] + dx) * 1000000), int((init_pos["BT1"][1] + dy) * 1000000)))
    board.Add(bt1)

    # SW1 (Slide Switch THT)
    sw1 = pcbnew.FootprintLoad(os.path.join(shared_support, "Button_Switch_THT.pretty"), "SW_Slide_SPDT_Straight_CK_OS102011MS2Q")
    sw1.SetReference("SW1")
    sw1.SetPosition(pcbnew.VECTOR2I(int((init_pos["SW1"][0] + dx) * 1000000), int((init_pos["SW1"][1] + dy) * 1000000)))
    board.Add(sw1)

    # R1 (Resistor THT)
    r1 = pcbnew.FootprintLoad(os.path.join(shared_support, "Resistor_THT.pretty"), "R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
    r1.SetReference("R1")
    r1.SetPosition(pcbnew.VECTOR2I(int((init_pos["R1"][0] + dx) * 1000000), int((init_pos["R1"][1] + dy) * 1000000)))
    board.Add(r1)

    # D1 (LED THT)
    d1 = pcbnew.FootprintLoad(os.path.join(shared_support, "LED_THT.pretty"), "LED_D5.0mm")
    d1.SetReference("D1")
    d1.SetPosition(pcbnew.VECTOR2I(int((init_pos["D1"][0] + dx) * 1000000), int((init_pos["D1"][1] + dy) * 1000000)))
    board.Add(d1)

    # Pad-to-Net mapping
    print("  * Assigning nets to footprint pads...")
    
    # BT1: pad 1 -> VCC, pad 2 -> GND
    bt1.FindPadByNumber("1").SetNet(net_vcc)
    bt1.FindPadByNumber("2").SetNet(net_gnd)

    # SW1: pad 1 -> VCC, pad 2 -> Net-(R1-Pad1)
    sw1.FindPadByNumber("1").SetNet(net_vcc)
    sw1.FindPadByNumber("2").SetNet(net_r1)

    # R1: pad 1 -> Net-(R1-Pad1), pad 2 -> Net-(D1-Pad1)
    r1.FindPadByNumber("1").SetNet(net_r1)
    r1.FindPadByNumber("2").SetNet(net_d1)

    # D1: pad 1 -> Net-(D1-Pad1), pad 2 -> GND
    d1.FindPadByNumber("1").SetNet(net_d1)
    d1.FindPadByNumber("2").SetNet(net_gnd)

    # Set netclass rules (clearance=0.25mm, track_width=0.3mm)
    net_settings = board.GetDesignSettings().m_NetSettings
    default_class = net_settings.GetDefaultNetclass()
    default_class.SetClearance(int(0.25 * 1000000))
    default_class.SetTrackWidth(int(0.3 * 1000000))
    default_class.SetViaDiameter(int(0.6 * 1000000))
    default_class.SetViaDrill(int(0.3 * 1000000))

    # Save initial PCB
    pcbnew.SaveBoard(pcb_path, board)
    print(f"Initial board configuration saved to {pcb_path}")

    # 7. Export Specctra DSN
    print("\n[7/11] Exporting Specctra DSN design file...")
    success = pcbnew.ExportSpecctraDSN(board, dsn_path)
    if success:
        print(f"DSN design file exported to {dsn_path}")
    else:
        print("Failed to export Specctra DSN!")
        sys.exit(1)

    # 8. Run FreeRouting CLI
    print("\n[8/11] Launching FreeRouting (Java-based autorouter)...")
    if not os.path.exists(freerouting_jar):
        print(f"Error: FreeRouting JAR not found at {freerouting_jar}")
        sys.exit(1)

    cmd = [
        java_bin, "-jar", freerouting_jar,
        "-de", dsn_path,
        "-do", ses_path,
        "-mp", "50",
        "--gui.enabled=false",
        "--api_server.enabled=false"
    ]
    print(f"Running command: {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    print("FreeRouting Stdout:")
    print(res.stdout)
    if res.stderr:
        print("FreeRouting Stderr:")
        print(res.stderr)

    if not os.path.exists(ses_path):
        print("Error: FreeRouting did not produce a Specctra SES file!")
        sys.exit(1)
    print(f"Specctra SES session file generated successfully at {ses_path}")

    # 9. Import Specctra SES
    print("\n[9/11] Importing Specctra SES session back into PCB...")
    # Load original board to avoid any duplicate objects
    board = pcbnew.LoadBoard(pcb_path)
    success = pcbnew.ImportSpecctraSES(board, ses_path)
    if success:
        pcbnew.SaveBoard(pcb_path, board)
        print(f"Routed PCB tracks imported and saved to {pcb_path}")
    else:
        print("Failed to import Specctra SES!")
        sys.exit(1)

    # 10. PCB DRC Check
    print("\n[10/11] Running PCB Design Rules Check (DRC)...")
    res = subprocess.run([
        kicad_cli, "pcb", "drc", pcb_path, "-o", drc_report_path
    ], cwd=demo_dir, capture_output=True, text=True)
    if os.path.exists(drc_report_path):
        with open(drc_report_path, 'r', encoding='utf-8') as f:
            print(f.read().strip())
    else:
        print(f"DRC failed to run. Stderr: {res.stderr}")

    # 11. BOM Export
    print("\n[11/11] Exporting BOM from schematic...")
    res = subprocess.run([
        kicad_cli, "sch", "export", "bom", sch_path, "-o", bom_path
    ], cwd=demo_dir, capture_output=True, text=True)
    if os.path.exists(bom_path):
        print(f"BOM exported successfully to {bom_path}")
    else:
        print(f"BOM export failed. Stderr: {res.stderr}")

    # 12. Run PCB Analyzer & DFM Checks
    print("\n[12/11] Running PCB Layout Analyzer and DFM Check...")
    analyze_pcb_script = os.path.join(skills_dir, "kicad", "scripts", "analyze_pcb.py")
    if os.path.exists(analyze_pcb_script):
        res = subprocess.run([
            "python3", analyze_pcb_script, pcb_path, "--output", pcb_analysis_path
        ], capture_output=True, text=True)
        if os.path.exists(pcb_analysis_path):
            print("PCB Layout Analyzer executed successfully.")
            # Run DFM Checks on the resulting JSON
            with open(pcb_analysis_path, 'r', encoding='utf-8') as f:
                pcb_data = json.load(f)
            
            stats = pcb_data.get("statistics", {})
            setup_data = pcb_data.get("setup", {})
            findings = pcb_data.get("findings", [])

            print("\n==================================================")
            print("       DFM COMPLIANCE REPORT: JLCPCB & PCBWAY     ")
            print("==================================================")
            print(f"File: {os.path.basename(pcb_path)}")
            print(f"KiCad Version: {pcb_data.get('kicad_version', 'Unknown')}")
            print("--------------------------------------------------")
            thickness = setup_data.get("board_thickness_mm", 1.6)
            layers_count = stats.get("copper_layers_used", 2)
            print(f"Board Thickness: {thickness} mm")
            print(f"Copper Layers: {layers_count}")
            print(f"Routing Tracks: {stats.get('track_segments', 0)} segments")
            print(f"Vias Count: {stats.get('via_count', 0)}")
            print(f"Unrouted Nets: {stats.get('unrouted_net_count', 0)} / {stats.get('net_count', 0)}")
            
            thickness_jlc = "PASS" if 0.4 <= thickness <= 2.4 else "FAIL (Range: 0.4 - 2.4mm)"
            thickness_pcbway = "PASS" if 0.2 <= thickness <= 3.2 else "FAIL (Range: 0.2 - 3.2mm)"
            print(f"\nDFM Design Rule Audits:")
            print(f"  * JLCPCB Thickness: {thickness_jlc}")
            print(f"  * PCBWay Thickness: {thickness_pcbway}")
            
            assembly_warnings = []
            connectivity_warnings = []
            for f in findings:
                cat = f.get("category", "")
                summary = f.get("summary", "")
                rec = f.get("recommendation", "")
                if cat == "assembly":
                    assembly_warnings.append((summary, rec))
                elif cat == "connectivity":
                    connectivity_warnings.append((summary, rec))

            print("\nAssembly & Placement Issues:")
            if assembly_warnings:
                for s, r in assembly_warnings:
                    print(f"  * [WARNING] {s}")
                    print(f"    Rec: {r}")
            else:
                print("  * None")

            print("\nConnectivity & Routing Issues:")
            if connectivity_warnings:
                for s, r in connectivity_warnings:
                    print(f"  * [ERROR] {s}")
                    print(f"    Rec: {r}")
            else:
                print("  * None (Routing is complete and correct!)")
            print("==================================================")
        else:
            print(f"PCB Layout Analyzer failed. Stderr: {res.stderr}")
    else:
        print(f"Skipping PCB layout analysis: script not found at {analyze_pcb_script}")

    print("\n-----------------------------------------------------------------")
    print("✅ Flashlight E2E Automation Completed successfully!")
    print(f"Project path: {demo_dir}")
    print("=================================================================")

if __name__ == "__main__":
    main()
