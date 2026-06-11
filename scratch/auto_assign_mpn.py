import json
import os
import re
import subprocess
import sys

_GENERIC_VALUE_RE = re.compile(
    r"^[\d.]+\s*[pnuμmkMGR]?[FHΩRfhω]?$"    # 100nF, 10K, 4.7uF, 100R
    r"|^[\d.]+\s*[kKmM]?[Ωω]?$"               # 10K, 4.7k
    r"|^[\d.]+\s*[pnuμm]?[Ff]$"               # 100pF, 10uF
    r"|^[\d.]+\s*[pnuμm]?[Hh]$"               # 10uH
    r"|^[\d.]+%$"                               # 1%
    r"|^DNP$|^NC$|^N/A$|^SW_SPDT$|^USB_C_Receptacle_USB2.0$",
    re.IGNORECASE,
)

def is_real_mpn(mpn: str) -> bool:
    mpn = mpn.strip()
    if not mpn or len(mpn) < 3:
        return False
    if _GENERIC_VALUE_RE.match(mpn):
        return False
    has_letter = any(c.isalpha() for c in mpn)
    has_digit = any(c.isdigit() for c in mpn)
    return has_letter and has_digit

analysis_path = "/Users/gary/hardwares/underwater-machine/schematic/analysis/schematic_analysis.json"
edit_script = "/Users/gary/.gemini/skills/bom/scripts/edit_properties.py"

with open(analysis_path, "r") as f:
    data = json.load(f)

sheets = data.get("sheets", [])
components = data.get("components", [])

# Group updates by sheet file
sheet_updates = {}

for comp in components:
    ref = comp.get("reference", "")
    val = comp.get("value", "")
    mpn = comp.get("mpn", "").strip()
    sheet_idx = comp.get("_sheet")
    
    if not ref or sheet_idx is None or sheet_idx >= len(sheets):
        continue
        
    sheet_path = sheets[sheet_idx]
    
    # Check if we should copy Value to MPN
    if is_real_mpn(val):
        if sheet_path not in sheet_updates:
            sheet_updates[sheet_path] = {}
        sheet_updates[sheet_path][ref] = {"MPN": val}
        print(f"Assigning MPN: {ref} -> {val} (in {os.path.basename(sheet_path)})")

# Apply updates to each sheet
for sheet_path, updates in sheet_updates.items():
    print(f"\nApplying {len(updates)} updates to {sheet_path}...")
    updates_json = json.dumps(updates)
    result = subprocess.run(
        [sys.executable, edit_script, sheet_path],
        input=updates_json,
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print("Success:", result.stdout.strip())
    else:
        print("Failed:", result.stderr.strip())

print("\nFinished assigning MPNs!")
