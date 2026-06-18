import unittest
import sys
import os
import shutil
import subprocess
import json

# Ensure package path is visible
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pcbnew
    HAS_PCBNEW = True
except ImportError:
    HAS_PCBNEW = False

from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.symbol import generate_symbol_sexpr, save_symbol_to_library
from kicad_skill.schematic import place_symbols_and_resolve, connect_symbols_in_schematic

def center_schematic(sch_path):
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

class TestFlashlightE2E(unittest.TestCase):
    def setUp(self):
        # Create a temp directory for E2E files
        self.test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flashlight_temp")
        os.makedirs(self.test_dir, exist_ok=True)
        
        self.sch_path = os.path.join(self.test_dir, "flashlight.kicad_sch")
        self.lib_path = os.path.join(self.test_dir, "flashlight.kicad_sym")
        self.table_path = os.path.join(self.test_dir, "sym-lib-table")
        self.pro_path = os.path.join(self.test_dir, "flashlight.kicad_pro")
        self.pcb_path = os.path.join(self.test_dir, "flashlight.kicad_pcb")
        self.dsn_path = os.path.join(self.test_dir, "flashlight.dsn")
        self.ses_path = os.path.join(self.test_dir, "flashlight.ses")
        self.bom_path = os.path.join(self.test_dir, "bom.csv")
        self.erc_report_path = os.path.join(self.test_dir, "erc_report.txt")
        self.drc_report_path = os.path.join(self.test_dir, "drc_report.txt")
        self.pcb_analysis_path = os.path.join(self.test_dir, "pcb_analysis.json")
        
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

        # Copy project template if it exists
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_pro = os.path.join(project_root, "test_project", "test_project.kicad_pro")
        if os.path.exists(template_pro):
            shutil.copy(template_pro, self.pro_path)
        else:
            with open(self.pro_path, 'w', encoding='utf-8') as f:
                f.write("{}")

    def tearDown(self):
        # Clean up temp directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_flashlight_design_flow(self):
        # Step 1: Create custom symbols for the flashlight components
        print("\n--- Creating custom symbols ---")
        
        # Battery Holder (THT Keystone 2466 1xAAA)
        bat_pins = [
            {"side": "left", "number": "1", "name": "VCC", "type": "power_out"},
            {"side": "right", "number": "2", "name": "GND", "type": "passive"}
        ]
        bat_sym = generate_symbol_sexpr("KEYSTONE_2466", bat_pins, ref_prefix="BT", width=12.7, height=12.7)
        save_symbol_to_library(self.lib_path, bat_sym)
        
        # Slide Switch (THT OS102011MS2Q SPDT)
        sw_pins = [
            {"side": "left", "number": "1", "name": "COM", "type": "passive"},
            {"side": "right", "number": "2", "name": "NO", "type": "passive"},
            {"side": "bottom", "number": "3", "name": "NC", "type": "no_connect"}
        ]
        sw_sym = generate_symbol_sexpr("OS102011MS2Q", sw_pins, ref_prefix="SW", width=10.16, height=10.16)
        save_symbol_to_library(self.lib_path, sw_sym)
        
        # Resistor (THT Axial DIN0207)
        res_pins = [
            {"side": "left", "number": "1", "name": "1", "type": "passive"},
            {"side": "right", "number": "2", "name": "2", "type": "passive"}
        ]
        res_sym = generate_symbol_sexpr("R_Axial_DIN0207", res_pins, ref_prefix="R", width=7.62, height=5.08)
        save_symbol_to_library(self.lib_path, res_sym)
        
        # LED (THT D5.0mm)
        led_pins = [
            {"side": "left", "number": "1", "name": "A", "type": "passive"},
            {"side": "right", "number": "2", "name": "K", "type": "passive"}
        ]
        led_sym = generate_symbol_sexpr("LED_D5.0mm", led_pins, ref_prefix="D", width=7.62, height=5.08)
        save_symbol_to_library(self.lib_path, led_sym)
        
        # Verify the library was created and contains the symbols
        self.assertTrue(os.path.exists(self.lib_path))
        with open(self.lib_path, 'r', encoding='utf-8') as f:
            lib_content = f.read()
        self.assertIn("KEYSTONE_2466", lib_content)
        self.assertIn("OS102011MS2Q", lib_content)
        self.assertIn("R_Axial_DIN0207", lib_content)
        self.assertIn("LED_D5.0mm", lib_content)
        
        # Step 2: Place all 4 symbols onto the schematic and resolve overlap collisions
        print("--- Placing symbol instances ---")
        placements = [
            {"lib_id": "flashlight:KEYSTONE_2466", "reference": "BT1", "value": "KEYSTONE_2466", "x": 100.0, "y": 100.0, "angle": 0.0, "properties": {"Footprint": "Battery:BatteryHolder_Keystone_2466_1xAAA"}},
            {"lib_id": "flashlight:OS102011MS2Q", "reference": "SW1", "value": "OS102011MS2Q", "x": 100.0, "y": 100.0, "angle": 0.0, "properties": {"Footprint": "Button_Switch_THT:SW_Slide_SPDT_Straight_CK_OS102011MS2Q"}}, # Intentional overlap
            {"lib_id": "flashlight:R_Axial_DIN0207", "reference": "R1", "value": "R_Axial_DIN0207", "x": 100.0, "y": 100.0, "angle": 0.0, "properties": {"Footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal"}}, # Intentional overlap
            {"lib_id": "flashlight:LED_D5.0mm", "reference": "D1", "value": "LED_D5.0mm", "x": 100.0, "y": 100.0, "angle": 0.0, "properties": {"Footprint": "LED_THT:LED_D5.0mm"}} # Intentional overlap
        ]
        
        resolved = place_symbols_and_resolve(
            schematic_path=self.sch_path,
            table_path=self.table_path,
            new_placements=placements,
            margin=7.62,
            resolve=True
        )
        
        self.assertEqual(len(resolved), 4)
        positions = [(r['tx'], r['ty']) for r in resolved]
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
        
        self.assertTrue(num_wires > 0, "No wires were routed in the E2E flashlight schematic!")
        
        with open(self.sch_path, 'r', encoding='utf-8') as f:
            sch_content = f.read()
        self.assertIn("(wire", sch_content)
        
        # Center schematic elements
        print("--- Centering schematic symbols and wires on paper ---")
        center_schematic(self.sch_path)
        
        # Step 4: Run ERC check using kicad-cli
        kicad_cli = shutil.which("kicad-cli")
        if not kicad_cli:
            mac_path = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
            if os.path.exists(mac_path):
                kicad_cli = mac_path
        
        if not kicad_cli:
            print("--- kicad-cli is not available, skipping ERC check ---")
        else:
            print("--- Running Schematic ERC ---")
            res_erc = subprocess.run([
                kicad_cli, "sch", "erc", self.sch_path, "-o", self.erc_report_path
            ], cwd=self.test_dir, capture_output=True, text=True)
            self.assertTrue(os.path.exists(self.erc_report_path), "ERC report was not generated!")
            print("ERC Report successfully generated.")

        # Step 5: Run PCB routing and DFM checks if pcbnew is available
        if not HAS_PCBNEW:
            print("--- pcbnew library is not available, skipping PCB generation and routing tests ---")
            return

        print("--- Programmatic PCB Layout and Autorouting ---")
        board = pcbnew.BOARD()

        # Add outline segments
        def add_edge_line(board, sx, sy, ex, ey):
            segment = pcbnew.PCB_SHAPE(board)
            segment.SetShape(pcbnew.SHAPE_T_SEGMENT)
            segment.SetStart(pcbnew.VECTOR2I(int(sx * 1000000), int(sy * 1000000)))
            segment.SetEnd(pcbnew.VECTOR2I(int(ex * 1000000), int(ey * 1000000)))
            segment.SetLayer(pcbnew.Edge_Cuts)
            segment.SetWidth(int(0.15 * 1000000))
            board.Add(segment)

        # Create Nets
        def get_or_create_net(board, name):
            net = board.FindNet(name)
            if not net:
                net = pcbnew.NETINFO_ITEM(board, name)
                board.Add(net)
            return net

        net_vcc = get_or_create_net(board, "VCC")
        net_gnd = get_or_create_net(board, "GND")
        net_r1 = get_or_create_net(board, "Net-(R1-Pad1)")
        net_d1 = get_or_create_net(board, "Net-(D1-Pad1)")

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
        
        dx = page_center_x - board_center_x
        dy = page_center_y - board_center_y

        # Draw centered board outline
        add_edge_line(board, out_min_x + dx, out_min_y + dy, out_max_x + dx, out_min_y + dy)
        add_edge_line(board, out_max_x + dx, out_min_y + dy, out_max_x + dx, out_max_y + dy)
        add_edge_line(board, out_max_x + dx, out_max_y + dy, out_min_x + dx, out_max_y + dy)
        add_edge_line(board, out_min_x + dx, out_max_y + dy, out_min_x + dx, out_min_y + dy)

        # Load and place footprints at centered positions
        shared_support = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
        
        bt1 = pcbnew.FootprintLoad(os.path.join(shared_support, "Battery.pretty"), "BatteryHolder_Keystone_2466_1xAAA")
        bt1.SetReference("BT1")
        bt1.SetPosition(pcbnew.VECTOR2I(int((init_pos["BT1"][0] + dx) * 1000000), int((init_pos["BT1"][1] + dy) * 1000000)))
        board.Add(bt1)

        sw1 = pcbnew.FootprintLoad(os.path.join(shared_support, "Button_Switch_THT.pretty"), "SW_Slide_SPDT_Straight_CK_OS102011MS2Q")
        sw1.SetReference("SW1")
        sw1.SetPosition(pcbnew.VECTOR2I(int((init_pos["SW1"][0] + dx) * 1000000), int((init_pos["SW1"][1] + dy) * 1000000)))
        board.Add(sw1)

        r1 = pcbnew.FootprintLoad(os.path.join(shared_support, "Resistor_THT.pretty"), "R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
        r1.SetReference("R1")
        r1.SetPosition(pcbnew.VECTOR2I(int((init_pos["R1"][0] + dx) * 1000000), int((init_pos["R1"][1] + dy) * 1000000)))
        board.Add(r1)

        d1 = pcbnew.FootprintLoad(os.path.join(shared_support, "LED_THT.pretty"), "LED_D5.0mm")
        d1.SetReference("D1")
        d1.SetPosition(pcbnew.VECTOR2I(int((init_pos["D1"][0] + dx) * 1000000), int((init_pos["D1"][1] + dy) * 1000000)))
        board.Add(d1)

        # Pad Net Assignments
        bt1.FindPadByNumber("1").SetNet(net_vcc)
        bt1.FindPadByNumber("2").SetNet(net_gnd)
        sw1.FindPadByNumber("1").SetNet(net_vcc)
        sw1.FindPadByNumber("2").SetNet(net_r1)
        r1.FindPadByNumber("1").SetNet(net_r1)
        r1.FindPadByNumber("2").SetNet(net_d1)
        d1.FindPadByNumber("1").SetNet(net_d1)
        d1.FindPadByNumber("2").SetNet(net_gnd)

        # Set design rules
        net_settings = board.GetDesignSettings().m_NetSettings
        default_class = net_settings.GetDefaultNetclass()
        default_class.SetClearance(int(0.25 * 1000000))
        default_class.SetTrackWidth(int(0.3 * 1000000))

        # Save initial board and export DSN
        pcbnew.SaveBoard(self.pcb_path, board)
        self.assertTrue(os.path.exists(self.pcb_path))

        success_dsn = pcbnew.ExportSpecctraDSN(board, self.dsn_path)
        self.assertTrue(success_dsn)
        self.assertTrue(os.path.exists(self.dsn_path))

        # Run FreeRouting
        freerouting_jar = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_project", "freerouting.jar")
        java_bin = "/opt/homebrew/opt/openjdk/bin/java"
        if not os.path.exists(freerouting_jar):
            print(f"--- freerouting.jar not found at {freerouting_jar}, skipping autorouting execution ---")
            return

        cmd = [
            java_bin, "-jar", freerouting_jar,
            "-de", self.dsn_path,
            "-do", self.ses_path,
            "-mp", "50",
            "--gui.enabled=false",
            "--api_server.enabled=false"
        ]
        res_route = subprocess.run(cmd, capture_output=True, text=True)
        self.assertTrue(os.path.exists(self.ses_path), f"FreeRouting did not produce SES file! Stderr: {res_route.stderr}")

        # Import SES back
        board_routed = pcbnew.LoadBoard(self.pcb_path)
        success_ses = pcbnew.ImportSpecctraSES(board_routed, self.ses_path)
        self.assertTrue(success_ses)
        pcbnew.SaveBoard(self.pcb_path, board_routed)

        # Run PCB DRC Check
        print("--- Running PCB DRC check ---")
        res_drc = subprocess.run([
            kicad_cli, "pcb", "drc", self.pcb_path, "-o", self.drc_report_path
        ], cwd=self.test_dir, capture_output=True, text=True)
        self.assertTrue(os.path.exists(self.drc_report_path))
        with open(self.drc_report_path, 'r', encoding='utf-8') as f:
            drc_content = f.read()
        self.assertIn("Found 0 DRC violations", drc_content, f"DRC check failed! Content:\n{drc_content}")
        self.assertIn("Found 0 unconnected pads", drc_content, f"Unconnected pads found! Content:\n{drc_content}")

        # Run BOM export
        print("--- Running BOM Export ---")
        res_bom = subprocess.run([
            kicad_cli, "sch", "export", "bom", self.sch_path, "-o", self.bom_path
        ], cwd=self.test_dir, capture_output=True, text=True)
        self.assertTrue(os.path.exists(self.bom_path))

        # Run PCB Analyzer & DFM Checks
        print("--- Running PCB Analyzer and DFM check ---")
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skills_dir = "/Users/gary/.gemini/skills"
        analyze_pcb_script = os.path.join(skills_dir, "kicad", "scripts", "analyze_pcb.py")
        if os.path.exists(analyze_pcb_script):
            res_analyze = subprocess.run([
                "python3", analyze_pcb_script, self.pcb_path, "--output", self.pcb_analysis_path
            ], capture_output=True, text=True)
            self.assertTrue(os.path.exists(self.pcb_analysis_path))
            
            with open(self.pcb_analysis_path, 'r', encoding='utf-8') as f:
                pcb_data = json.load(f)
            
            stats = pcb_data.get("statistics", {})
            self.assertEqual(stats.get("unrouted_net_count", -1), 0, "Analyzer reported unrouted nets!")
            self.assertTrue(stats.get("track_segments", 0) > 0, "Analyzer reported 0 track segments!")
            print("E2E PCB DFM compliance check successful!")

if __name__ == "__main__":
    unittest.main()
