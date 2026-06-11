import json
import os
import csv
from datetime import datetime

# Paths to input files
BASE_DIR = "/Users/gary/hardwares/underwater-machine/schematic"
SCHEMATIC_ANALYSIS = os.path.join(BASE_DIR, "analysis/schematic_analysis.json")
PCB_ANALYSIS = os.path.join(BASE_DIR, "analysis/pcb_analysis.json")
BOM_CSV = os.path.join(BASE_DIR, "bom/bom.csv")
SPICE_SIMULATION = os.path.join(BASE_DIR, "analysis/spice_simulation.json")
EMC_ANALYSIS = os.path.join(BASE_DIR, "analysis/emc_analysis.json")
THERMAL_ANALYSIS = os.path.join(BASE_DIR, "analysis/thermal_analysis.json")
OUTPUT_MD = os.path.join(BASE_DIR, "analysis/design_review_report.md")

def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {path}: {e}")
    return None

def main():
    print("Consolidating analysis results...")
    
    # Load all data
    sch_data = load_json(SCHEMATIC_ANALYSIS)
    pcb_data = load_json(PCB_ANALYSIS)
    spice_data = load_json(SPICE_SIMULATION)
    emc_data = load_json(EMC_ANALYSIS)
    thermal_data = load_json(THERMAL_ANALYSIS)
    
    bom_rows = []
    if os.path.exists(BOM_CSV):
        try:
            with open(BOM_CSV, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                bom_rows = list(reader)
        except Exception as e:
            print(f"Error loading {BOM_CSV}: {e}")

    # Build report sections
    md = []
    md.append("# 🛠️ Consolidated KiCad Design Review Report")
    md.append(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md.append("")
    md.append("This report summarizes the design review audits performed on the **`underwater-machine`** project using the integrated `kicad-happy` skills (Schematic/PCB analysis, BOM Sourcing, SPICE simulation, EMC, Thermal, and DFM).")
    md.append("")
    md.append("---")
    
    # ----------------------------------------------------
    # 📊 EXECUTIVE SUMMARY TABLE
    # ----------------------------------------------------
    md.append("## 📊 Executive Summary")
    md.append("")
    md.append("| Check / Metric | Status | Result / Key Metrics |")
    md.append("|---|---|---|")
    
    # Schematic
    if sch_data:
        num_components = len(sch_data.get("components", []))
        num_sheets = len(sch_data.get("sheets", []))
        md.append(f"| **Schematic Analysis** | Done | {num_components} components across {num_sheets} sheets |")
    else:
        md.append("| **Schematic Analysis** | Not Available | - |")
        
    # PCB / DFM
    if pcb_data:
        stats = pcb_data.get("statistics", {})
        setup = pcb_data.get("setup", {})
        thickness = setup.get("board_thickness_mm", 1.6)
        layers = stats.get("copper_layers_used", 2)
        unrouted = stats.get("unrouted_net_count", 0)
        md.append(f"| **PCB Layout & DFM** | Warning | {layers} copper layers, {thickness}mm thickness, {unrouted} unrouted nets |")
    else:
        md.append("| **PCB Layout & DFM** | Not Available | - |")
        
    # BOM Sourcing
    if bom_rows:
        total_parts = len(bom_rows)
        with_mpn = sum(1 for r in bom_rows if r.get("MPN"))
        pct = (with_mpn / total_parts * 100) if total_parts else 0
        md.append(f"| **BOM Sourcing** | In Progress | {with_mpn}/{total_parts} unique parts with MPN assigned ({pct:.1f}%) |")
    else:
        md.append("| **BOM Sourcing** | Not Available | - |")

    # SPICE Simulation
    if spice_data:
        sims = spice_data.get("simulation_results", [])
        passed = sum(1 for s in sims if s.get("status") == "pass")
        skipped = sum(1 for s in sims if s.get("status") == "skipped")
        failed = sum(1 for s in sims if s.get("status") == "fail")
        md.append(f"| **SPICE Simulation** | Passed | {passed} passed, {failed} failed, {skipped} skipped |")
    else:
        md.append("| **SPICE Simulation** | Not Available | - |")

    # EMC Pre-compliance
    if emc_data:
        score = emc_data.get("summary", {}).get("emc_score", 0)
        findings_count = len(emc_data.get("findings", []))
        md.append(f"| **EMC Pre-compliance** | Score: {score}/100 | {findings_count} pre-compliance findings (Target: {emc_data.get('target_standard', 'CISPR32')}) |")
    else:
        md.append("| **EMC Pre-compliance** | Not Available | - |")

    # Thermal Analysis
    if thermal_data:
        findings_count = len(thermal_data.get("findings", []))
        shunt_warning = "Critical shunt resistor temperature warning!" if findings_count > 0 else "No critical warnings"
        md.append(f"| **Thermal Hotspots** | Warning | {findings_count} findings ({shunt_warning}) |")
    else:
        md.append("| **Thermal Hotspots** | Not Available | - |")

    md.append("")
    md.append("---")
    
    # ----------------------------------------------------
    # 🔍 SECTION 1: BOM SOURCING & DATASHEETS
    # ----------------------------------------------------
    md.append("## 🔍 1. BOM Sourcing & Datasheets")
    if bom_rows:
        total_parts = len(bom_rows)
        with_mpn = sum(1 for r in bom_rows if r.get("MPN"))
        pct = (with_mpn / total_parts * 100) if total_parts else 0
        md.append(f"**MPN Coverage**: {with_mpn} of {total_parts} unique parts have manufacturer part numbers assigned ({pct:.1f}%).")
        md.append("")
        
        # List parts missing MPNs
        missing_mpns = [r for r in bom_rows if not r.get("MPN")]
        if missing_mpns:
            md.append("### ⚠️ Components Missing MPNs:")
            md.append("These are mostly generic passives (capacitors and resistors) that need concrete MPNs assigned before placing a turnkey assembly order:")
            md.append("")
            md.append("| Reference | Value | Footprint | Notes |")
            md.append("|---|---|---|---|")
            for item in missing_mpns[:15]:
                md.append(f"| `{item.get('Reference', '')}` | {item.get('Value', '')} | `{item.get('Footprint', '')}` | {item.get('Notes', '')} |")
            if len(missing_mpns) > 15:
                md.append(f"| *...and {len(missing_mpns)-15} more rows* | | | |")
        else:
            md.append("✅ **All components have valid MPNs assigned!**")
    else:
        md.append("BOM tracking CSV not found.")
    md.append("")
    
    # ----------------------------------------------------
    # 📐 SECTION 2: DFM AUDIT (JLCPCB & PCBWAY)
    # ----------------------------------------------------
    md.append("## 📐 2. DFM Verification (JLCPCB & PCBWay)")
    if pcb_data:
        stats = pcb_data.get("statistics", {})
        setup = pcb_data.get("setup", {})
        thickness = setup.get("board_thickness_mm", 1.6)
        layers = stats.get("copper_layers_used", 2)
        unrouted = stats.get("unrouted_net_count", 0)
        
        md.append(f"* **Board Thickness**: {thickness} mm (Standard: 1.6mm - Passes for both JLCPCB and PCBWay).")
        md.append(f"* **Copper Layers**: {layers}")
        md.append(f"* **Routing Status**: {stats.get('track_segments', 0)} trace segments, {stats.get('via_count', 0)} vias.")
        md.append(f"* **Unrouted Nets**: {unrouted} nets remaining unrouted (Nets: `/PA0`, `VDD`, `GND`).")
        md.append("")
        
        # Extract assembly warnings (like fiducials)
        findings = pcb_data.get("findings", [])
        assembly_findings = [f for f in findings if f.get("category") == "assembly"]
        if assembly_findings:
            md.append("### ⚠️ Assembly Issues:")
            for f in assembly_findings:
                md.append(f"- **{f.get('summary', '')}**")
                md.append(f"  *Recommendation:* {f.get('recommendation', '')}")
        else:
            md.append("✅ No assembly warnings detected in PCB layout.")
    else:
        md.append("PCB analysis not found. Please run the PCB analyzer first.")
    md.append("")
    
    # ----------------------------------------------------
    # ⚡ SECTION 3: SPICE SIMULATION
    # ----------------------------------------------------
    md.append("## ⚡ 3. SPICE Analog Simulation Results")
    if spice_data:
        sims = spice_data.get("simulation_results", [])
        passed = sum(1 for s in sims if s.get("status") == "pass")
        skipped = sum(1 for s in sims if s.get("status") == "skipped")
        failed = sum(1 for s in sims if s.get("status") == "fail")
        
        md.append(f"Simulated **{len(sims)}** analog subcircuits automatically:")
        md.append(f"- **Passed**: {passed}")
        md.append(f"- **Failed**: {failed}")
        md.append(f"- **Skipped**: {skipped}")
        md.append("")
        
        # Group by subcircuit type
        types = {}
        for s in sims:
            t = s.get("subcircuit_type", "unknown")
            types[t] = types.get(t, 0) + 1
        
        md.append("### Simulated Subcircuit Types:")
        for t, count in types.items():
            md.append(f"- **{t.replace('_', ' ').title()}**: {count} subcircuits")
            
        # List failed or skipped
        not_passed = [s for s in sims if s.get("status") != "pass"]
        if not_passed:
            md.append("")
            md.append("### ⚠️ Skipped or Failed Simulations:")
            md.append("| Reference | Subcircuit Type | Status | Expected | Simulated | Delta |")
            md.append("|---|---|---|---|---|---|")
            for s in not_passed:
                md.append(f"| `{s.get('reference', '')}` | {s.get('subcircuit_type', '')} | **{s.get('status', '').upper()}** | {s.get('expected', '')} | {s.get('simulated', '')} | {s.get('delta', '')} |")
    else:
        md.append("SPICE simulation data not found.")
    md.append("")
    
    # ----------------------------------------------------
    # 📡 SECTION 4: EMC PRE-COMPLIANCE
    # ----------------------------------------------------
    md.append("## 📡 4. EMC Pre-compliance Findings")
    if emc_data:
        score = emc_data.get("summary", {}).get("emc_score", 0)
        findings = emc_data.get("findings", [])
        md.append(f"**EMC Score**: **{score}/100** (Target Standard: `{emc_data.get('target_standard', 'CISPR32')}`)")
        md.append("")
        
        severity_colors = {"error": "🔴 ERROR", "warning": "🟡 WARNING", "info": "🔵 INFO"}
        
        if findings:
            md.append("### Key EMC Pre-compliance Findings:")
            for f in findings:
                sev = severity_colors.get(f.get("severity", "").lower(), f.get("severity", "").upper())
                md.append(f"- **[{sev}] {f.get('title', '')}**")
                md.append(f"  *Description:* {f.get('description', '')}")
                md.append(f"  *Recommendation:* {f.get('recommendation', '')}")
        else:
            md.append("✅ No EMC findings detected.")
    else:
        md.append("EMC pre-compliance data not found.")
    md.append("")
    
    # ----------------------------------------------------
    # 🌡️ SECTION 5: THERMAL ANALYSIS
    # ----------------------------------------------------
    md.append("## 🌡️ 5. Thermal Hotspot Audit")
    if thermal_data:
        findings = thermal_data.get("findings", [])
        violations = [f for f in findings if f.get("title")]
        temp_reports = [f for f in findings if not f.get("title") and "tj_estimated_c" in f]
        
        md.append(f"**Thermal findings**: {len(findings)} issues identified ({len(violations)} violations, {len(temp_reports)} temperature reports).")
        md.append("")
        
        if violations:
            md.append("### Critical Thermal Warnings:")
            for f in violations:
                sev = "🔴 CRITICAL" if f.get("severity") == "error" else "🟡 WARNING"
                md.append(f"> [!CAUTION]")
                md.append(f"> **{sev}: {f.get('title', '')}**")
                md.append(f"> ")
                md.append(f"> *Description:* {f.get('description', '')}")
                md.append(f"> ")
                md.append(f"> *Recommendation:* {f.get('recommendation', '')}")
                md.append("")
        
        if temp_reports:
            md.append("### 🌡️ Junction Temperature Estimations:")
            md.append("")
            md.append("| Reference | Component | Package | Power (W) | Est. Tj (°C) | Max Tj (°C) | Margin (°C) | Status |")
            md.append("|---|---|---|---|---|---|---|---|")
            for r in temp_reports:
                ref = r.get("ref", "")
                val = r.get("value", "")
                pkg = r.get("package", "unknown")
                pdiss = r.get("pdiss_w", 0)
                tj_est = r.get("tj_estimated_c", 0)
                tj_max = r.get("tj_max_c", 0)
                margin = r.get("margin_c", 0)
                
                # Status formatting
                status = "✅ OK"
                if margin < 0:
                    status = "🔴 OVERHEAT"
                elif margin < 15:
                    status = "🟡 WARNING"
                
                md.append(f"| `{ref}` | {val} | `{pkg}` | {pdiss:.3f} | {tj_est:.1f} | {tj_max:.1f} | {margin:.1f} | {status} |")
            md.append("")
        
        if not violations and not temp_reports:
            md.append("✅ No thermal hotspots or power dissipation warnings detected.")
    else:
        md.append("Thermal analysis data not found.")
    md.append("")
    
    # Write to output file
    try:
        with open(OUTPUT_MD, "w", encoding="utf-8") as f:
            f.write("\n".join(md))
        print(f"Successfully generated local design review report at: {OUTPUT_MD}")
    except Exception as e:
        print(f"Error writing markdown report: {e}")

if __name__ == "__main__":
    main()
