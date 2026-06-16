"""Canonical end-to-end check for module creation + wiring.

THE demo to run when you want to know "is create_module still correct".

Pipeline per run:
  build CAN-node symbols -> regenerate_schematic(routing='wires') flat
  -> create_module_from_components(subset) -> KiCad ERC on the PARENT (hierarchical)

The PARENT hierarchical ERC is the ONLY authoritative gate: kicad-cli loads the
sub-sheet through the parent's sheet path, so a real short/dangling inside the
module surfaces here. (ERC on the sub file standalone is a FALSE gate — its
hierarchical labels have no parent when opened alone — so we never use it.)

Usage:
    python3 scratch/verify_module_creation.py                # default subset
    python3 scratch/verify_module_creation.py U2 U3 Y1 C1 C2 R2   # custom subset
Outputs schematics + rendered PDFs under scratch/module_out/.
"""
import os
import sys
import shutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from kicad_skill.parser import format_sexpr
from kicad_skill.symbol import generate_symbol_sexpr, save_symbol_to_library
from kicad_skill.regenerate import regenerate_schematic
from kicad_skill.module import create_module_from_components
from kicad_skill.erc import find_kicad_cli, run_erc

OUT_DIR = os.path.join(PROJECT_ROOT, "test_project", "can_node_module")
GT_SRC = os.path.join(PROJECT_ROOT, "tests", "fixtures", "can_node", "can_node.groundtruth.json")
DEFAULT_SUBSET = ["U2", "U3", "Y1", "C1", "C2", "R2"]   # the CAN controller block


def build_symbols(lib_path):
    """Create the three custom ICs the CAN-node ground truth references."""
    save_symbol_to_library(lib_path, generate_symbol_sexpr("MCU", [
        {"side": "right", "number": "1", "name": "SPI_CS", "type": "output"},
        {"side": "right", "number": "2", "name": "SPI_MOSI", "type": "output"},
        {"side": "right", "number": "3", "name": "SPI_MISO", "type": "input"},
        {"side": "right", "number": "4", "name": "SPI_SCK", "type": "output"},
        {"side": "right", "number": "5", "name": "3V3", "type": "power_out"},
        {"side": "right", "number": "6", "name": "GND", "type": "power_out"},
    ], ref_prefix="U", width=20.32, height=20.32))
    save_symbol_to_library(lib_path, generate_symbol_sexpr("MCP2515", [
        {"side": "left", "number": "16", "name": "CS", "type": "input"},
        {"side": "left", "number": "14", "name": "SI", "type": "input"},
        {"side": "left", "number": "15", "name": "SO", "type": "tri_state"},
        {"side": "left", "number": "13", "name": "SCK", "type": "input"},
        {"side": "left", "number": "18", "name": "VDD", "type": "power_in"},
        {"side": "left", "number": "9", "name": "VSS", "type": "power_in"},
        {"side": "bottom", "number": "17", "name": "RESET", "type": "input"},
        {"side": "bottom", "number": "8", "name": "OSC1", "type": "input"},
        {"side": "bottom", "number": "7", "name": "OSC2", "type": "output"},
        {"side": "right", "number": "1", "name": "TXCAN", "type": "output"},
        {"side": "right", "number": "2", "name": "RXCAN", "type": "input"},
    ], ref_prefix="U", width=25.4, height=35.56))
    save_symbol_to_library(lib_path, generate_symbol_sexpr("TJA1050", [
        {"side": "left", "number": "1", "name": "TXD", "type": "input"},
        {"side": "left", "number": "4", "name": "RXD", "type": "output"},
        {"side": "left", "number": "3", "name": "VCC", "type": "power_in"},
        {"side": "left", "number": "2", "name": "GND", "type": "power_in"},
        {"side": "bottom", "number": "8", "name": "RS", "type": "input"},
        {"side": "right", "number": "7", "name": "CANH", "type": "bidirectional"},
        {"side": "right", "number": "6", "name": "CANL", "type": "bidirectional"},
    ], ref_prefix="U", width=20.32, height=25.4))


def render(cli, sch_path, pdf_path):
    import subprocess
    subprocess.run([cli, "sch", "export", "pdf", "--output", pdf_path, sch_path],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def main(subset):
    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)

    lib = os.path.join(OUT_DIR, "mcp_test.kicad_sym")
    table = os.path.join(OUT_DIR, "sym-lib-table")
    parent = os.path.join(OUT_DIR, "can_node.kicad_sch")
    sub_file = "can_mod.kicad_sch"

    shutil.copy(GT_SRC, os.path.join(OUT_DIR, "gt.json"))
    build_symbols(lib)
    with open(table, "w") as f:
        f.write(format_sexpr(["sym_lib_table",
            ["lib", ["name", "mcp_test"], ["type", "KiCad"],
             ["uri", "${KIPRJMOD}/mcp_test.kicad_sym"], ["options", ""], ["descr", ""]]]))
    for ext in ("kicad_pro", "kicad_prl"):
        shutil.copy(os.path.join(PROJECT_ROOT, "test_project", f"test_project.{ext}"),
                    os.path.join(OUT_DIR, f"can_node.{ext}"))

    regenerate_schematic(os.path.join(OUT_DIR, "gt.json"), table, parent, routing="wires")
    n_nets, _ = create_module_from_components(parent, table, subset, "CAN_MOD", sub_file)
    print(f"subset {subset}: {n_nets} boundary nets")

    if find_kicad_cli() is None:
        print("kicad-cli not found — cannot run the authoritative ERC gate.")
        return 1

    erc = run_erc(parent)   # hierarchical: parent + every sub-sheet
    cli = find_kicad_cli()
    render(cli, parent, os.path.join(OUT_DIR, "parent.pdf"))
    render(cli, os.path.join(OUT_DIR, sub_file), os.path.join(OUT_DIR, "sub.pdf"))

    status = "PASS" if erc["error_count"] == 0 else "FAIL"
    print(f"\n[{status}] PARENT hierarchical ERC: {erc['error_count']} error(s)  (authoritative)")
    for v in erc["violations"]:
        print(f"   [{v['type']}] {v['description'][:80]}")
    print(f"\nWrote schematics + parent.pdf / sub.pdf to {OUT_DIR}")
    return 0 if erc["error_count"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or DEFAULT_SUBSET))
