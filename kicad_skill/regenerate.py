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
from kicad_skill.erc import find_kicad_cli, run_erc


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


def _component_kind(lib_id):
    """Classify a component by its lib_id: 'passive', 'connector', 'power', or 'ic'."""
    if not lib_id:
        return "ic"
    if lib_id.startswith("Connector"):
        return "connector"
    if "power" in lib_id.lower():
        return "power"
    if lib_id.startswith("Device:"):
        return "passive"
    return "ic"


def classify_nets(nets, components, centers):
    """Split nets into (label_nets, wire_nets).

    Golden rule (user guidance): a normal symbol connecting to a passive, a power
    symbol, or a connector reads best as a short wire; symbol-to-symbol (IC<->IC)
    reads best as a local label. Wire-routing is restricted to adjacent 2-pin
    pairs. Power/ground rails are always labels (they fan out widely).

    This is an optimistic classification: the ERC gate in regenerate_schematic
    demotes any wire that fails to connect back to a label, so correctness never
    depends on getting this exactly right.
    """
    label_nets, wire_nets = [], []
    for net in nets:
        pins = net["pins"]
        refs = [p.split(":")[0] for p in pins]
        wireable = False
        if not _is_power(net["name"]) and len(pins) == 2 and refs[0] != refs[1]:
            kinds = [_component_kind(components.get(r, {}).get("lib_id", "")) for r in refs]
            touches_non_ic = any(k != "ic" for k in kinds)
            a, b = centers.get(refs[0]), centers.get(refs[1])
            if touches_non_ic and a and b and math.dist(a, b) < ADJ_THRESHOLD:
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


def _build_once(out_sch, table_path, nets, components, placements, forced_labels):
    """Place symbols and route one pass; nets in forced_labels are label-routed.

    Returns the list of wire-net names actually emitted as wires this pass.
    """
    _write_blank_schematic(out_sch)
    place_symbols_and_resolve(out_sch, table_path, placements, margin=2.54, resolve=True)
    coords = _pin_coords(out_sch, table_path)
    centers = _centers_from_schematic(out_sch)

    label_nets, wire_nets = classify_nets(nets, components, centers)
    demoted = [n for n in wire_nets if n["name"] in forced_labels]
    wire_nets = [n for n in wire_nets if n["name"] not in forced_labels]
    label_nets = label_nets + demoted

    _emit_labels(out_sch, label_nets, coords)
    for net in wire_nets:
        a, b = net["pins"]
        connect_symbols_in_schematic(out_sch, table_path, [{"from": a, "to": b}], orthogonal=True)
    return [n["name"] for n in wire_nets]


def regenerate_schematic(gt_path, table_path, out_sch, max_iter=None, use_erc=True):
    """Build a clean flat schematic from the ground-truth netlist.

    The gate is KiCad's own ERC (authoritative) when kicad-cli is available,
    backed by the ground-truth netlist comparison for logical correctness. Any
    ERC error (short, open, wire_dangling, label_dangling) demotes the pass's
    wire-routed nets to labels and rebuilds; the all-labels configuration is
    electrically clean, so the loop converges. When kicad-cli is absent it falls
    back to the ground-truth comparison alone.

    Returns (out_sch, report). The report always carries the ground-truth
    comparison keys plus, when ERC ran, erc_error_count and erc_violations.
    """
    gt = load_ground_truth(gt_path)
    nets, components = load_gt_components(gt)
    placements = _placements_from_components(components)

    forced_labels = set()
    if max_iter is None:
        max_iter = len(nets) + 1
    erc_available = use_erc and find_kicad_cli() is not None

    for _ in range(max_iter):
        wire_names = set(_build_once(out_sch, table_path, nets, components, placements, forced_labels))
        rep = compare(extract_actual_netlist(out_sch, table_path), nets)

        if erc_available:
            erc = run_erc(out_sch)
            rep["erc_error_count"] = erc["error_count"]
            rep["erc_violations"] = erc["violations"]
            if erc["ok"] and not rep["fatal"]:
                return out_sch, rep
            # Demote this pass's wires to labels (all-labels is ERC-clean).
            remaining = wire_names - forced_labels
            if not remaining:
                raise RuntimeError(
                    f"regenerate failed ERC with all nets label-routed: "
                    f"{erc['violations'] if not erc['ok'] else rep}")
            forced_labels |= remaining
            continue

        # No kicad-cli: gate on the ground-truth comparison alone.
        if not rep["fatal"]:
            return out_sch, rep
        offenders = {n for s in rep["shorts"] for n in s["gt_nets"]}
        offenders |= {o["gt_net"] for o in rep["opens"]}
        newly = offenders & wire_names
        if not newly:
            raise RuntimeError(f"regenerate failed: fatal report not fixable by label demotion: {rep}")
        forced_labels |= newly

    raise RuntimeError("regenerate did not converge")

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


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        prog="regenerate-from-gt",
        description="Generate a clean flat schematic from a ground-truth netlist.")
    parser.add_argument("--ground-truth", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    out_path, rep = regenerate_schematic(args.ground_truth, args.table, args.out)
    status = "FATAL" if rep["fatal"] else "CLEAN"
    print(f"[{status}] wrote {out_path}")
    print(f"  GT nets: {len(rep['ok'])} ok, "
          f"{len(rep['shorts'])} shorts, {len(rep['opens'])} opens, "
          f"{len(rep['missing'])} missing pins")
    return 1 if rep["fatal"] else 0


if __name__ == "__main__":
    sys.exit(main())
