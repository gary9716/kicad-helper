import json
import os

pcb_analysis_path = "/Users/gary/hardwares/underwater-machine/schematic/analysis/pcb_analysis.json"

with open(pcb_analysis_path, "r") as f:
    pcb_data = json.load(f)

stats = pcb_data.get("statistics", {})
setup = pcb_data.get("setup", {})
findings = pcb_data.get("findings", [])

print("==================================================")
print("       DFM COMPLIANCE REPORT: JLCPCB & PCBWAY     ")
print("==================================================")
print(f"File: {os.path.basename(pcb_data.get('file', 'Unknown'))}")
print(f"KiCad Version: {pcb_data.get('kicad_version', 'Unknown')}")
print("--------------------------------------------------\n")

# 1. Physical Attributes & Stackup
thickness = setup.get("board_thickness_mm", 1.6)
layers_count = stats.get("copper_layers_used", 2)
print("1. Board Physical Specs:")
print(f"   Board Thickness: {thickness} mm")
print(f"   Copper Layers: {layers_count}")
print(f"   Routing Tracks: {stats.get('track_segments', 0)} segments")
print(f"   Vias Count: {stats.get('via_count', 0)}")
print(f"   Unrouted Nets: {stats.get('unrouted_net_count', 0)} / {stats.get('net_count', 0)}")

print("\n2. DFM Design Rule Audits:")
# JLCPCB Rules (Standard 2-Layer)
# - Thickness: 0.4mm to 2.4mm
# - Min Trace Width: 0.127mm (5mil)
# - Min Via Hole: 0.2mm, Diameter: 0.45mm
# PCBWay Rules (Standard 2-Layer)
# - Thickness: 0.2mm to 3.2mm
# - Min Trace Width: 0.1mm (4mil)
# - Min Via Hole: 0.15mm, Diameter: 0.3mm

thickness_jlc = "PASS" if 0.4 <= thickness <= 2.4 else "FAIL (Range: 0.4 - 2.4mm)"
thickness_pcbway = "PASS" if 0.2 <= thickness <= 3.2 else "FAIL (Range: 0.2 - 3.2mm)"
print(f"   [Thickness {thickness}mm]:")
print(f"     * JLCPCB: {thickness_jlc}")
print(f"     * PCBWay: {thickness_pcbway}")

# 3. Check for PCB Findings
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

print("\n3. Assembly & Placement Issues:")
if assembly_warnings:
    for s, r in assembly_warnings:
        print(f"   * [WARNING] {s}")
        print(f"     Recommendation: {r}")
else:
    print("   * None found.")

print("\n4. Connectivity & Routing Issues:")
if connectivity_warnings:
    for s, r in connectivity_warnings:
        print(f"   * [ERROR] {s}")
        print(f"     Recommendation: {r}")
else:
    print("   * None found.")

print("\n5. Sourcing & Production Readiness:")
sourcing_issues = []
for f in pcb_data.get("property_issues", []):
    sourcing_issues.append(f)
for f in findings:
    if f.get("detector") == "analyze_test_point_coverage":
        print(f"   * [WARNING] {f.get('summary')}")
        print(f"     Recommendation: {f.get('recommendation')}")

print("\n==================================================")
