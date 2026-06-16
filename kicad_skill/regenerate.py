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
