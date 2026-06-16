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


def _placements_from_components(components):
    placements = []
    for ref, c in sorted(components.items()):
        placements.append({
            "lib_id": c["lib_id"],
            "reference": ref,
            "value": c.get("value", ""),
            "x": float(c.get("x", 0.0)),
            "y": float(c.get("y", 0.0)),
            "angle": float(c.get("angle", 0.0)),
        })
    # Auto-row fallback for components without coords: spread along a row.
    col = 0
    for p in placements:
        if p["x"] == 0.0 and p["y"] == 0.0:
            p["x"] = 50.0 + col * 25.4
            p["y"] = 100.0
            col += 1
    return placements


def regenerate_schematic(gt_path, table_path, out_sch, max_iter=None):
    """Build a clean flat schematic from the ground-truth netlist.

    Returns (out_sch, report). Raises if a fatal report survives the
    all-labels configuration (GT/geometry contradiction).
    """
    gt = load_ground_truth(gt_path)
    nets, components = load_gt_components(gt)
    placements = _placements_from_components(components)

    forced_labels = set()  # net names demoted from wire to label after a short/open
    if max_iter is None:
        max_iter = len(nets) + 1

    for _ in range(max_iter):
        _write_blank_schematic(out_sch)
        place_symbols_and_resolve(out_sch, table_path, placements, margin=2.54, resolve=True)
        coords = _pin_coords(out_sch, table_path)
        centers = _centers_from_schematic(out_sch)

        label_nets, wire_nets = classify_nets(nets, centers)
        # Apply demotions from previous iterations.
        demoted = [n for n in wire_nets if n["name"] in forced_labels]
        wire_nets = [n for n in wire_nets if n["name"] not in forced_labels]
        label_nets = label_nets + demoted

        _emit_labels(out_sch, label_nets, coords)
        for net in wire_nets:
            a, b = net["pins"]
            connect_symbols_in_schematic(out_sch, table_path, [{"from": a, "to": b}], orthogonal=True)

        actual = extract_actual_netlist(out_sch, table_path)
        rep = compare(actual, nets)
        if not rep["fatal"]:
            return out_sch, rep

        # Demote every wire-net implicated in a short/open and retry.
        offenders = {n for s in rep["shorts"] for n in s["gt_nets"]}
        offenders |= {o["gt_net"] for o in rep["opens"]}
        wire_names = {n["name"] for n in wire_nets}
        newly = offenders & wire_names
        if not newly:
            raise RuntimeError(f"regenerate failed: fatal report not fixable by label demotion: {rep}")
        forced_labels |= newly

    raise RuntimeError("regenerate did not converge")


def _centers_from_schematic(sch_path):
    """Return {ref: (x, y)} symbol placement centers from a schematic."""
    with open(sch_path, "r", encoding="utf-8") as f:
        sexpr = parse_sexpr(f.read())
    centers = {}
    for child in sexpr[1:]:
        if not isinstance(child, list) or not child or child[0] != "symbol":
            continue
        ref, at = "", None
        for sub in child[1:]:
            if isinstance(sub, list) and len(sub) > 1:
                if sub[0] == "property" and len(sub) > 2 and sub[1] == "Reference":
                    ref = sub[2]
                elif sub[0] == "at":
                    at = (float(sub[1]), float(sub[2]))
        if ref and at:
            centers[ref] = at
    return centers
