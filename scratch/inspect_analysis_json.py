import json
import os

analysis_path = "/Users/gary/hardwares/underwater-machine/schematic/analysis/schematic_analysis.json"
with open(analysis_path, "r") as f:
    data = json.load(f)

print("Root keys:", list(data.keys()))
if "bom" in data and len(data["bom"]) > 0:
    print("\nBOM entry keys:", list(data["bom"][0].keys()))
    print("Example BOM entry:", data["bom"][0])
    
if "components" in data and len(data["components"]) > 0:
    comp_key = list(data["components"].keys())[0]
    print("\nComponents entry keys:", list(data["components"][comp_key].keys()))
    print("Example component entry:", data["components"][comp_key])
