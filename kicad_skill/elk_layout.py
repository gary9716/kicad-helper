"""ELK (elkjs) based schematic auto-layout.

Pipeline: parse (reuse resolve_layout/_netlist_eval) -> classify nets
(power & high-fanout -> labels, 2-3 pin signals -> ELK edges) -> build ELK
JSON (layered, FIXED_POS ports, orthogonal edges) -> node tools/elk_runner.js
-> snap to KiCad grid -> write symbols/wires/labels/junctions back.

Spec: docs/superpowers/specs/2026-07-13-elk-layout-design.md
"""
import json
import os
import subprocess

from .regenerate import _is_power

GRID = 1.27  # KiCad wire/pin grid (mm)


def classify_for_elk(nets, fanout_threshold=4):
    """Split named nets into (edge_nets, label_nets).

    nets: iterable of (name, set_of_pin_ids). Power-named nets and nets with
    fanout >= threshold become labels; 2..threshold-1 pin signal nets become
    ELK edges; singletons are dropped (nothing to draw).
    """
    edge_nets, label_nets = [], []
    for name, pins in nets:
        if len(pins) < 2:
            continue
        if _is_power(name) or len(pins) >= fanout_threshold:
            label_nets.append((name, pins))
        else:
            edge_nets.append((name, pins))
    return edge_nets, label_nets


def name_nets(nets, pin_positions, labels_at):
    """Attach a name to each anonymous pin-set net.

    A net whose any pin position carries an existing label uses that label's
    text; otherwise the name is synthesized from the first pin id (sorted),
    e.g. NET_U2_1. Returns list of (name, pin_set).
    """
    named = []
    for net in nets:
        name = None
        for pid in sorted(net):
            pos = pin_positions.get(pid)
            if pos is not None and pos in labels_at:
                name = labels_at[pos]
                break
        if name is None:
            name = "NET_" + sorted(net)[0].replace(":", "_")
        named.append((name, net))
    return named


def collect_labels_at(sch_sexpr):
    """{(x, y): label_text} for every label/global_label in the sheet."""
    out = {}
    for child in sch_sexpr[1:]:
        if isinstance(child, list) and child and child[0] in ("label", "global_label"):
            at = next((s for s in child[1:]
                       if isinstance(s, list) and s[0] == "at" and len(s) > 2), None)
            if at is not None:
                out[(float(at[1]), float(at[2]))] = child[1]
    return out


def _port_side(pin_x, pin_y, bbox):
    """Closest bbox edge wins. KiCad y grows downward, same as ELK: the
    bbox ymin edge is the visual top -> NORTH."""
    dists = {
        "WEST": pin_x - bbox.xmin,
        "EAST": bbox.xmax - pin_x,
        "NORTH": pin_y - bbox.ymin,
        "SOUTH": bbox.ymax - pin_y,
    }
    return min(dists, key=dists.get)


def build_elk_graph(symbols, edge_nets):
    """symbols: _extract_symbols output (with 'pins'). edge_nets: [(name, pins)].

    Node origin = bbox min corner; ports relative to it; FIXED_POS so ELK
    never moves a pin. Spacing values are mm (ELK is unitless).
    """
    children = []
    for sym in symbols:
        b = sym["bbox"]
        ports = []
        for p in sym["pins"]:
            ports.append({
                "id": f'{sym["ref"]}:{p["number"]}',
                "x": p["x"] - b.xmin,
                "y": p["y"] - b.ymin,
                "width": 0.1,
                "height": 0.1,
                "layoutOptions": {"elk.port.side": _port_side(p["x"], p["y"], b)},
            })
        children.append({
            "id": sym["ref"],
            "width": b.xmax - b.xmin,
            "height": b.ymax - b.ymin,
            "ports": ports,
            "layoutOptions": {"elk.portConstraints": "FIXED_POS"},
        })

    edges = []
    for i, (name, pins) in enumerate(edge_nets):
        ordered = sorted(pins)
        edges.append({
            "id": f"e{i}_{name}",
            "sources": [ordered[0]],
            "targets": ordered[1:],
        })

    return {
        "id": "root",
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT",
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.spacing.nodeNode": 5.08,
            "elk.spacing.edgeNode": 2.54,
            "elk.layered.spacing.nodeNodeBetweenLayers": 10.16,
        },
        "children": children,
        "edges": edges,
    }
