"""Regenerate a clean flat schematic from a ground-truth netlist.

See docs/superpowers/specs/2026-06-16-regenerate-from-gt-design.md.
Pin identity is "Ref:PinNumber", matching the ground-truth JSON.
"""
import json
import os
import sys
import uuid

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from kicad_skill.parser import parse_sexpr, format_sexpr
from kicad_skill.schematic import (
    place_symbols_and_resolve,
    connect_symbols_in_schematic,
    load_sym_lib_table,
    find_symbol_definition,
)
from kicad_skill.module import get_symbol_pins_global
from kicad_skill.netlist_eval import extract_actual_netlist, compare, load_ground_truth


def load_gt_components(gt):
    """Return (nets, components) after validating every net ref has a component."""
    nets = gt["nets"]
    components = gt.get("components", {})
    used = {pin.split(":")[0] for net in nets for pin in net["pins"]}
    missing = sorted(r for r in used if r not in components)
    if missing:
        raise ValueError(f"components block missing entries for refs: {', '.join(missing)}")
    return nets, components


import math

ADJ_THRESHOLD = 30.0  # mm; two components closer than this can share a short wire
POWER_NAMES = {"VDD", "VCC", "VSS", "GND", "VBUS", "V+", "V-", "3V3", "5V", "GNDA", "VDDA"}


def _is_power(name):
    u = name.upper()
    return u in POWER_NAMES or u.startswith("GND") or u.startswith("VDD") or u.startswith("VCC")


def classify_nets(nets, centers):
    """Split nets into (label_nets, wire_nets). See module docstring / spec."""
    label_nets, wire_nets = [], []
    for net in nets:
        pins = net["pins"]
        refs = [p.split(":")[0] for p in pins]
        wireable = False
        if not _is_power(net["name"]) and len(pins) == 2 and refs[0] != refs[1]:
            a, b = centers.get(refs[0]), centers.get(refs[1])
            if a and b and math.dist(a, b) < ADJ_THRESHOLD:
                wireable = True
        (wire_nets if wireable else label_nets).append(net)
    return label_nets, wire_nets


def _write_blank_schematic(path):
    blank = [
        "kicad_sch",
        ["version", "20211123"],
        ["generator", "eeschema"],
        ["generator_version", "10.0"],
        ["uuid", str(uuid.uuid4())],
        ["paper", "A4"],
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write(format_sexpr(blank))


def _pin_coords(sch_path, table_path):
    """Return {(ref, number): (x, y)} for every placed symbol pin."""
    project_dir = os.path.dirname(os.path.abspath(sch_path))
    lib_map = load_sym_lib_table(table_path) if os.path.exists(table_path) else {}
    with open(sch_path, "r", encoding="utf-8") as f:
        sexpr = parse_sexpr(f.read())
    local_defs = {}
    for child in sexpr[1:]:
        if isinstance(child, list) and child and child[0] == "lib_symbols":
            for d in child[1:]:
                if isinstance(d, list) and d[0] == "symbol" and len(d) > 1:
                    local_defs[d[1]] = d
    coords = {}
    for child in sexpr[1:]:
        if not isinstance(child, list) or not child or child[0] != "symbol":
            continue
        ref, lib_id = "", ""
        for sub in child[1:]:
            if isinstance(sub, list) and len(sub) > 1:
                if sub[0] == "lib_id":
                    lib_id = sub[1]
                elif sub[0] == "property" and len(sub) > 2 and sub[1] == "Reference":
                    ref = sub[2]
        defn = local_defs.get(lib_id)
        if not defn and ":" in lib_id:
            lib_name, sym_name = lib_id.split(":", 1)
            defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
        for p in get_symbol_pins_global(child, defn):
            coords[(ref, p["number"])] = (p["x"], p["y"])
    return coords


def _make_label(name, x, y):
    return [
        "label", name,
        ["at", f"{x:.3f}", f"{y:.3f}", "0"],
        ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", "left", "bottom"]],
        ["uuid", str(uuid.uuid4())],
    ]


def _emit_labels(sch_path, label_nets, pin_coords):
    """Insert a local label at each pin coordinate of each label-net."""
    with open(sch_path, "r", encoding="utf-8") as f:
        sexpr = parse_sexpr(f.read())
    for net in label_nets:
        for pin in net["pins"]:
            ref, num = pin.split(":")
            xy = pin_coords.get((ref, num))
            if xy is None:
                raise ValueError(f"pin {pin} not found among placed symbols")
            sexpr.append(_make_label(net["name"], xy[0], xy[1]))
    with open(sch_path, "w", encoding="utf-8") as f:
        f.write(format_sexpr(sexpr))
