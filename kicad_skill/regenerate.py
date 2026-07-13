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
    get_symbol_local_bbox,
    get_all_pins_from_symbol_def,
    BoundingBox,
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

ADJ_THRESHOLD = 35.0  # mm; components within this form a local cluster wired directly
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
        distinct = list(dict.fromkeys(refs))
        wireable = False
        if not _is_power(net["name"]) and len(distinct) >= 2:
            kinds = [_component_kind(components.get(r, {}).get("lib_id", "")) for r in refs]
            touches_non_ic = any(k != "ic" for k in kinds)
            pts = [centers.get(r) for r in distinct]
            local = all(pts) and all(
                math.dist(pts[i], pts[j]) < ADJ_THRESHOLD
                for i in range(len(pts)) for j in range(i + 1, len(pts)))
            if touches_non_ic and local:
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


def _label_orientation(px, py, cx, cy):
    """Return (angle, justify) so a label at a pin grows AWAY from the symbol body.

    The pin's side is the dominant axis of (pin - symbol_center). KiCad label
    justify sets which side of the anchor the text occupies; combined with the
    angle this points the text outward (off the body and off neighbouring pins).
    The anchor itself stays on the pin so the label still connects.
    """
    dx, dy = px - cx, py - cy
    if abs(dx) >= abs(dy):
        # Horizontal pin: text grows left (off a left-side pin) or right.
        return (0, "right") if dx < 0 else (0, "left")
    # Vertical pin: text grows up (off a top-side pin) or down.
    return (90, "left") if dy < 0 else (90, "right")


def _make_label(name, x, y, angle=0, justify="left"):
    return [
        "label", name,
        ["at", f"{x:.3f}", f"{y:.3f}", str(angle)],
        ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", justify]],
        ["uuid", str(uuid.uuid4())],
    ]


def _emit_labels(sch_path, label_nets, pin_coords):
    """Insert a local label at each pin coordinate of each label-net, oriented so
    the text radiates away from the owning symbol's body."""
    centers = _centers_from_schematic(sch_path)
    with open(sch_path, "r", encoding="utf-8") as f:
        sexpr = parse_sexpr(f.read())
    for net in label_nets:
        for pin in net["pins"]:
            ref, num = pin.split(":")
            xy = pin_coords.get((ref, num))
            if xy is None:
                raise ValueError(f"pin {pin} not found among placed symbols")
            cx, cy = centers.get(ref, xy)
            angle, justify = _label_orientation(xy[0], xy[1], cx, cy)
            sexpr.append(_make_label(net["name"], xy[0], xy[1], angle, justify))
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


_LABEL_CHAR_WIDTH = 1.0   # mm per character at 1.27mm font (matches symbol.py)
_LABEL_GAP = 1.27         # mm breathing room beyond the text


def _label_padded_bboxes(components, nets, table_path, project_dir):
    """Return {ref: BoundingBox} of each symbol's local body box padded outward by
    its net-label text extents, so overlap resolution spreads symbols far enough
    that labels do not collide. Every pin is assumed labelled (conservative); a pin
    that ends up wire-routed instead just leaves a little extra slack.
    """
    lib_map = load_sym_lib_table(table_path) if os.path.exists(table_path) else {}
    pin_to_net = {pin: n["name"] for n in nets for pin in n["pins"]}
    out = {}
    for ref, c in components.items():
        lib_id = c.get("lib_id", "")
        if ":" not in lib_id:
            continue
        lib_name, sym_name = lib_id.split(":", 1)
        defn = find_symbol_definition(lib_name, sym_name, lib_map, project_dir)
        if not defn:
            continue
        bbox = get_symbol_local_bbox(defn)
        h_ext = v_ext = 0.0
        for p in get_all_pins_from_symbol_def(defn):
            name = pin_to_net.get(f"{ref}:{p['number']}")
            if not name:
                continue
            ext = len(name) * _LABEL_CHAR_WIDTH + _LABEL_GAP
            # A pin on a horizontal side carries a horizontal label (grows in x);
            # a pin on a vertical side carries a vertical label (grows in y).
            if abs(p["x"]) >= abs(p["y"]):
                h_ext = max(h_ext, ext)
            else:
                v_ext = max(v_ext, ext)
        # The Reference (e.g. "U2") and Value (e.g. "MCP2515", "0.1uF") text fields
        # are drawn just outside the body and overlap neighbours too. Reserve for the
        # wider of the two horizontally, and a text row vertically, on every side.
        text_w = max(len(ref), len(c.get("value", ""))) * _LABEL_CHAR_WIDTH
        h_ext = max(h_ext, text_w / 2.0)
        v_ext = max(v_ext, 2.54 + 1.27)
        out[ref] = BoundingBox(bbox.xmin - h_ext, bbox.ymin - v_ext,
                               bbox.xmax + h_ext, bbox.ymax + v_ext)
    return out


def _build_once(out_sch, table_path, nets, components, placements, forced_labels,
                bbox_overrides=None, routing="auto"):
    """Place symbols and route one pass; nets in forced_labels are label-routed.

    routing: "auto" classifies per the golden rule; "wires" routes every net with
    wires (now that bare wires connect — useful for feeding create_module, whose
    boundary detection is wire-based); "labels" routes every net with labels.

    Returns the list of wire-net names actually emitted as wires this pass.
    """
    _write_blank_schematic(out_sch)
    place_symbols_and_resolve(out_sch, table_path, placements, margin=2.54, resolve=True,
                              bbox_overrides=bbox_overrides)
    coords = _pin_coords(out_sch, table_path)
    centers = _centers_from_schematic(out_sch)

    if routing == "wires":
        label_nets, wire_nets = [], list(nets)
    elif routing == "labels":
        label_nets, wire_nets = list(nets), []
    else:
        label_nets, wire_nets = classify_nets(nets, components, centers)
    demoted = [n for n in wire_nets if n["name"] in forced_labels]
    wire_nets = [n for n in wire_nets if n["name"] not in forced_labels]
    label_nets = label_nets + demoted

    _emit_labels(out_sch, label_nets, coords)
    for net in wire_nets:
        pins = net["pins"]
        conns = [{"from": pins[i], "to": pins[i + 1]} for i in range(len(pins) - 1)]
        connect_symbols_in_schematic(out_sch, table_path, conns, orthogonal=True)
    return [n["name"] for n in wire_nets]


def regenerate_schematic(gt_path, table_path, out_sch, max_iter=None, use_erc=True, routing="auto"):
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
    if routing == "elk":
        # Build an all-labels schematic (electrically clean by construction),
        # then hand placement+routing to the ELK engine.
        out_sch, rep = regenerate_schematic(gt_path, table_path, out_sch,
                                            max_iter=max_iter, use_erc=use_erc,
                                            routing="labels")
        from .elk_layout import elk_layout_schematic
        elk_rep = elk_layout_schematic(out_sch, table_path)
        if not elk_rep["ok"]:
            raise RuntimeError(f"elk-layout broke connectivity: {elk_rep['report']}")
        rep["elk"] = {k: elk_rep[k] for k in ("wires", "labels", "junctions")}
        return out_sch, rep

    gt = load_ground_truth(gt_path)
    nets, components = load_gt_components(gt)
    placements = _placements_from_components(components)
    project_dir = os.path.dirname(os.path.abspath(out_sch))
    bbox_overrides = _label_padded_bboxes(components, nets, table_path, project_dir)

    forced_labels = set()
    if max_iter is None:
        max_iter = len(nets) + 1
    erc_available = use_erc and find_kicad_cli() is not None

    for _ in range(max_iter):
        wire_names = set(_build_once(out_sch, table_path, nets, components, placements,
                                     forced_labels, bbox_overrides, routing))
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
